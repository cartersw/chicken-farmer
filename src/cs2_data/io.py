"""Small, explicit I/O helpers shared by the dataset stages."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import uuid
from functools import wraps
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

from . import __version__


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")


def check_new_outputs(*paths: Path) -> None:
    for path in paths:
        if path.exists():
            raise ValueError(f"Refusing to overwrite an existing artifact: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)


def parsed_manifest(parsed: Path, required_files: Iterable[str] = ()) -> dict[str, Any]:
    manifest = read_json(parsed / "manifest.json")
    demo_id = manifest.get("demo_id")
    if not isinstance(demo_id, str) or not demo_id:
        raise ValueError("Parsed manifest must identify demo_id")
    if manifest.get("parse_status") != "complete" or manifest.get("partial") is not False:
        raise ValueError("Derived artifacts require a complete, nonpartial parse")
    if str(manifest.get("parser_schema_version")) not in ("1", "2"):
        raise ValueError("Unsupported parser schema version (supported: 1 and 2)")
    for name in required_files:
        expected = manifest.get("files", {}).get(name)
        if not expected or sha256_file(parsed / name) != expected:
            raise ValueError(f"Source artifact hash does not match parsed manifest: {name}")
    return manifest


def exclusive_output(file_output: bool = False):
    """Serialize publication in an output directory, including same-output competing runs."""
    def decorate(function):
        signature = inspect.signature(function)

        @wraps(function)
        def wrapped(*args, **kwargs):
            out = signature.bind(*args, **kwargs).arguments["out"]
            directory = out.parent if file_output else out
            directory.mkdir(parents=True, exist_ok=True)
            lock = directory / ".cs2-data.lock"
            try:
                handle = lock.open("x", encoding="utf-8")
            except FileExistsError as error:
                raise ValueError(f"Another stage owns {directory}; existing lock: {lock}") from error
            try:
                with handle:
                    return function(*args, **kwargs)
            finally:
                lock.unlink()
        return wrapped
    return decorate


def staging_paths(paths: list[Path]) -> list[Path]:
    check_new_outputs(*paths)
    token = uuid.uuid4().hex
    return [path.with_name(f".{path.name}.partial-{token}") for path in paths]


def publish(staged: list[Path], destinations: list[Path]) -> None:
    """Publish completion JSON last. Caller holds exclusive_output's directory lock."""
    check_new_outputs(*destinations)
    if any(not path.is_file() for path in staged):
        raise ValueError("Stage did not produce all required artifacts")
    for source, destination in zip(staged, destinations):
        os.replace(source, destination)


def batches(path: Path, columns: list[str] | None = None) -> Iterable[list[dict[str, Any]]]:
    for batch in pq.ParquetFile(path).iter_batches(batch_size=32768, columns=columns):
        yield batch.to_pylist()


def require_columns(path: Path, names: Iterable[str]) -> None:
    missing = set(names) - set(pq.read_schema(path).names)
    if missing:
        raise ValueError(f"Missing columns in {path.name}: {', '.join(sorted(missing))}")


def versioned_schema(fields: list[tuple[str, pa.DataType]], stage: str, demo_id: str,
                     schema_version: int = 1) -> pa.Schema:
    return pa.schema(fields, metadata={
        b"schema_version": str(schema_version).encode(), b"processing_version": __version__.encode(),
        b"stage": stage.encode(), b"demo_id": demo_id.encode(),
    })


def sha256_file(path: Path) -> str:
    from .immutable_evidence import guarded_digest
    guarded = guarded_digest(path)
    if guarded is not None:
        return guarded
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
