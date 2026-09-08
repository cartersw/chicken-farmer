"""No recurring visual work is required for the user-approved capture setup."""
from copy import deepcopy
from pathlib import Path

import pytest

from cs2_data import hud_policy as policy
from cs2_data.native_replay_profile import CURRENT_PROFILE, get_native_replay_profile


@pytest.fixture
def render():
    plugin = "aa553ed98fba652e3b8b13dce0db7eba4225503dd3fafc90ba7e3517c896b84f"
    resource = "3c29e34ae294676cb9d9e7dd8c6ec675484db72171020d9dfe60d3abaa110721"
    return {"renderer_profile": "windows-14180-competitive-hud-v1", "fps": 32,
        "capture_method": "native-windows-cs2-startmovie-tga",
        "binary_profile": dict(get_native_replay_profile(CURRENT_PROFILE)["binary_profile"]),
        "plugin_sha256": plugin, "plugin_source_sha256": plugin, "plugin_staged_sha256": plugin,
        "hud_profile": {"raw_frames_masked": False, "visual_acceptance_verified": False},
        "hud_override": {"profile": "spectator-strip-private-css-v4",
            "resource": "panorama/styles/hud/hudhealthammocenter.vcss_c",
            "staged_relative_path": "panorama/styles/hud/hudhealthammocenter.vcss_c",
            "override_resource_sha256": resource, "private_chunk_sha256": resource,
            "private_archive_relative_path": "pakchicken_hud_dir.vpk",
            "private_chunk_relative_path": "pakchicken_hud_000.vpk",
            "delivery": "identical_loose_and_chunked_vpk_resource",
            "raw_frames_masked": False, "installed_resources_modified": False,
            "resource_load_verified": False, "rendered_hud_preservation_verified": False}}


def test_current_setup_needs_no_review_file_image_read_or_runtime_visual_claim(render, monkeypatch):
    render.update(capture_frame_files="missing-images.json", hud_review_path="missing-review.json")
    def no_io(*args, **kwargs):
        pytest.fail("HUD setup policy must not open frames or reviews")
    monkeypatch.setattr("builtins.open", no_io)
    monkeypatch.setattr(Path, "open", no_io)
    result = policy.trusted_hud_policy(render)
    assert policy.policy_allows_capture(result)
    assert result["basis"] == "user_approved_capture_setup"
    assert result["visual_review_performed"] is False
    assert "verified_frame_indices" not in result
    assert "review_path" not in result


@pytest.mark.parametrize("change", ["renderer", "binary", "plugin", "staged_plugin", "override",
    "resource", "chunk", "delivery", "mask", "installed_modified", "missing_hud", "missing_override", "fps"])
def test_unknown_or_incompatible_setup_does_not_reuse_user_approval(render, change):
    if change == "renderer": render["renderer_profile"] = "future-renderer"
    elif change == "binary": render["binary_profile"]["csgo/bin/win64/client.dll"] = "a"*64
    elif change == "plugin": render["plugin_sha256"] = "a"*64
    elif change == "staged_plugin": del render["plugin_staged_sha256"]
    elif change == "override": render["hud_override"]["profile"] = "future-hud"
    elif change == "resource": render["hud_override"]["override_resource_sha256"] = "a"*64
    elif change == "chunk": render["hud_override"]["private_chunk_sha256"] = "a"*64
    elif change == "delivery": render["hud_override"]["delivery"] = "another-delivery"
    elif change == "mask": render["hud_profile"]["raw_frames_masked"] = True
    elif change == "installed_modified": render["hud_override"]["installed_resources_modified"] = True
    elif change == "missing_hud": render["hud_profile"] = None
    elif change == "missing_override": del render["hud_override"]
    elif change == "fps": render["fps"] = 64
    render["visual_acceptance_verified"] = True
    result = policy.trusted_hud_policy(render)
    assert result["status"] == "unsupported_capture_setup"
    assert not policy.policy_allows_capture(result)


@pytest.mark.parametrize("field,value", [("profile", "unknown-policy"), ("scope", "all-renderers"),
    ("basis", "image-inspection"), ("visual_review_performed", True), ("status", "verified"),
    ("reason_codes", ["unsupported"]), ("capture_setup", {})])
def test_generic_approval_or_another_scope_is_not_the_named_policy(render, field, value):
    result = deepcopy(policy.trusted_hud_policy(render))
    result[field] = value
    assert not policy.policy_allows_capture(result)


@pytest.mark.parametrize("value", [None, [], "trusted", {"status": "trusted_capture_setup"}])
def test_malformed_input_does_not_claim_setup_compatibility(value):
    assert not policy.policy_allows_capture(policy.trusted_hud_policy(value))
    assert not policy.policy_allows_capture(value)


def test_session_plugin_uses_same_fixed_hud_setup_without_visual_review():
    setup = policy._expected_setup(session=True)
    receipt = policy.trusted_hud_policy(setup)
    assert policy.policy_allows_capture(receipt)
    assert receipt['visual_review_performed'] is False
    setup['recording_session']['profile'] = 'unregistered-session'
    assert not policy.policy_allows_capture(policy.trusted_hud_policy(setup))
