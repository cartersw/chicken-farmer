"""Revalidate and summarize independent acceptance artifacts without promotion."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from . import acceptance as policy
from .io import exclusive_output, publish, read_json, sha256_file, staging_paths, write_json


def bound_file(path: Path, expected: Any, role: str) -> str:
    digest = sha256_file(path)
    if not isinstance(expected, str) or digest != expected:
        raise ValueError(f"Campaign source hash mismatch: {role}")
    return digest


def verify_acceptance(directory: Path, temporary_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recompute the exact partition, including policy changes and local evidence."""
    report = read_json(directory / "acceptance_manifest.json")
    if (report.get("schema_version") != 1 or report.get("acceptance_version") != 1
            or report.get("status") != "complete"):
        raise ValueError("Campaign requires a supported complete acceptance manifest")
    if set(report.get("files", {})) != {"accepted_samples.jsonl", "rejected_samples.jsonl"}:
        raise ValueError("Campaign acceptance partition hashes are missing or unexpected")
    for name, digest in report["files"].items():
        bound_file(directory / name, digest, name)
    dataset = Path(report["source_dataset"]).resolve()
    validation = Path(report["source_validation"]).resolve()
    context = Path(report["source_state_context"]).resolve() if report.get("source_state_context") else None
    bound_file(dataset / "pipeline_manifest.json", report.get("source_dataset_sha256"), "pipeline_manifest")
    bound_file(validation, report.get("source_validation_sha256"), "validation_report")
    if context is not None:
        bound_file(context, report.get("source_state_context_sha256"), "state_context")
    elif report.get("source_state_context_sha256") is not None:
        raise ValueError("Campaign state context path/hash disagree")
    # This path is only a locator. The validator independently verifies its
    # identity, content, and links to the exact dataset before it is trusted.
    locator = read_json(validation)
    parsed = Path(locator["parsed_directory"]).resolve()
    bound_file(parsed / "manifest.json", report.get("source_parsed_manifest_sha256"), "parsed_manifest")
    proof = policy.load_validation(validation, parsed, dataset, state_context=context)
    if not policy.same_identity(proof, report) or proof.get("clip_id") != report.get("clip_id"):
        raise ValueError("Campaign validation/acceptance identity mismatch")
    config = report.get("configuration", {})
    with TemporaryDirectory(prefix=".campaign-verification-", dir=temporary_root) as name:
        expected = policy.accept_samples(parsed, dataset, validation, Path(name),
            history_frames=config.get("history_frames"), target_horizon_frames=config.get("target_horizon_frames"),
            include_previous_actions=config.get("include_previous_actions"), state_context=context)
        if expected != report:
            raise ValueError("Acceptance differs from current recomputation; stale or edited partition cannot enter campaign")
    return report, proof


def partition_rows(directory: Path, report: dict[str, Any]):
    """Read stored records, verifying explicit partition semantics and identity."""
    for filename, accepted in (("accepted_samples.jsonl", True), ("rejected_samples.jsonl", False)):
        with (directory / filename).open(encoding="utf8") as handle:
            for line in handle:
                row = json.loads(line)
                if (not isinstance(row, dict) or row.get("training_ready") is not accepted
                        or not policy.same_identity(row, report) or row.get("clip_id") != report["clip_id"]
                        or not isinstance(row.get("sample_id"), str) or not row["sample_id"]
                        or not isinstance(row.get("reason_codes"), list)
                        or bool(row["reason_codes"]) is accepted):
                    raise ValueError("Campaign sample partition contains inconsistent records")
                yield row


def check_summary(check: dict[str, Any] | None) -> dict[str, Any]:
    check = check or {}
    status = check.get("status", "unknown")
    if status not in ("passed", "failed", "unknown"):
        status = "unknown"
    scope = check.get("scope", {})
    return {"status": status, "method": check.get("method"), "evidence_count": check.get("evidence_count", 0),
            "scoped_frame_count": len(set(scope.get("frame_indices", []))),
            "scoped_command_count": len(set(scope.get("command_row_ids", []))),
            "reason_codes": check.get("reason_codes", []), "metrics": check.get("metrics", {})}


@exclusive_output()
def summarize_campaign(acceptance: list[Path], out: Path) -> dict[str, Any]:
    """Publish a fresh, deterministic campaign summary after current verification."""
    if not acceptance:
        raise ValueError("Campaign requires at least one acceptance directory")
    if any(path.name != ".cs2-data.lock" for path in out.iterdir()):
        raise ValueError("Campaign requires a fresh output directory")
    directories = sorted((Path(path).resolve() for path in acceptance), key=str)
    if len(set(directories)) != len(directories):
        raise ValueError("Duplicate acceptance directory would inflate campaign counts")
    manifests, datasets, samples = set(), set(), set()
    identities = set()
    counts, reasons = Counter(), Counter()
    statuses = {name: Counter({"passed": 0, "failed": 0, "unknown": 0}) for name in policy.REQUIRED_CHECKS}
    transitions = Counter({"passed": 0, "failed": 0, "unknown": 0})
    clips = []
    for directory in directories:
        manifest_path = directory / "acceptance_manifest.json"
        digest = sha256_file(manifest_path)
        preliminary = read_json(manifest_path)
        dataset_digest = preliminary.get("source_dataset_sha256")
        if digest in manifests or dataset_digest in datasets:
            raise ValueError("Duplicate acceptance artifact or dataset would inflate campaign counts")
        report, proof = verify_acceptance(directory, out)
        manifests.add(digest)
        datasets.add(dataset_digest)
        local = Counter()
        for row in partition_rows(directory, report):
            if row["sample_id"] in samples:
                raise ValueError("Duplicate sample ID would inflate campaign counts")
            samples.add(row["sample_id"])
            local["candidate_count"] += 1
            local["accepted_count" if row["training_ready"] else "rejected_count"] += 1
            local["eligible_window_count"] += row.get("window_complete") is True
            reasons.update(row["reason_codes"])
        for name in ("candidate_count", "accepted_count", "rejected_count", "eligible_window_count"):
            if local[name] != report.get(name):
                raise ValueError("Campaign acceptance counts disagree with actual records")
            counts[name] += local[name]
        if report.get("training_ready_sample_count") != local["accepted_count"]:
            raise ValueError("Campaign readiness count disagrees with accepted records")
        identity = tuple(str(report[key]) for key in policy.IDENTITY)
        identities.add(identity)
        checks = {name: check_summary(proof.get("checks", {}).get(name)) for name in policy.REQUIRED_CHECKS}
        for name, check in checks.items():
            statuses[name][check["status"]] += 1
        transition = check_summary(proof.get("checks", {}).get("native_transition_coverage"))
        transitions[transition["status"]] += 1
        clips.append({**{key: report[key] for key in policy.IDENTITY}, "clip_id": report["clip_id"],
            "acceptance_directory": str(directory), "acceptance_manifest_sha256": digest,
            "dataset_directory": report["source_dataset"], "dataset_sha256": report["source_dataset_sha256"],
            "validation_path": report["source_validation"], "validation_sha256": report["source_validation_sha256"],
            "validation_version": proof.get("validation_version"),
            "counts": {name: local[name] for name in ("candidate_count", "accepted_count", "rejected_count", "eligible_window_count")},
            "required_checks": checks, "native_transition_coverage": transition})
        # Catch edits made while records were being inspected, before publishing
        # a summary that claims to describe this exact immutable partition.
        bound_file(manifest_path, digest, "acceptance_manifest")
        for name, expected in report["files"].items():
            bound_file(directory / name, expected, name)
    report = {"schema_version": 1, "campaign_version": 1, "status": "complete", "training_ready": False,
        "acceptance_count": len(clips), **dict(counts), "training_ready_sample_count": counts["accepted_count"],
        "unique_demo_count": len({identity[0] for identity in identities}),
        "unique_round_count": len({identity[:2] for identity in identities}),
        "unique_player_count": len({identity[2] for identity in identities}),
        "unique_demo_round_player_count": len(identities),
        "demo_round_players": [dict(zip(policy.IDENTITY, identity)) for identity in sorted(identities)],
        "required_checks": {name: dict(value) for name, value in statuses.items()},
        "native_transition_coverage": {"clip_status_counts": dict(transitions),
            "scope": "Per-clip observations below; counts do not certify untested transitions or other clips."},
        "reason_counts": dict(sorted(reasons.items())), "clips": clips,
        "policy": "Every acceptance partition and validation report was recomputed. Campaign totals never promote capture, timing, or training flags; only source accepted sample records retain their existing approval."}
    destinations = [out / "campaign_manifest.json"]
    staged = staging_paths(destinations)
    try:
        write_json(staged[0], report)
        publish(staged, destinations)
    finally:
        for path in staged:
            if path.exists():
                path.unlink()
    return report
