"""User-approved HUD setup policy, with no recurring visual inspection.

This policy trusts the established capture setup. It compares a fixed amount of
saved metadata; it does not open images, inspect CVars, run overlay detectors or
read review files. Its result is an explicit assumption about the setup, never
evidence that particular images were visually inspected. Native capture, source,
pixel correspondence, timing and label checks retain their separate contracts.
"""
from __future__ import annotations

from .native_replay_profile import CURRENT_PROFILE, get_native_replay_profile

POLICY_PROFILE = "cs2-trusted-hud-capture-setup-v1"
POLICY_SCOPE = "fixed_windows_14180_competitive_hud_setup"
RENDERER_PROFILE = "windows-14180-competitive-hud-v1"
PLUGIN_SHA256 = "aa553ed98fba652e3b8b13dce0db7eba4225503dd3fafc90ba7e3517c896b84f"
OVERRIDE_PROFILE = "spectator-strip-private-css-v4"
OVERRIDE_RESOURCE_SHA256 = "3c29e34ae294676cb9d9e7dd8c6ec675484db72171020d9dfe60d3abaa110721"
HUD_RESOURCE = "panorama/styles/hud/hudhealthammocenter.vcss_c"


def _expected_setup(session=False):
    from .session_profile import PROFILE, PLUGIN_SHA256 as SESSION_PLUGIN_SHA256
    plugin_sha = SESSION_PLUGIN_SHA256 if session else PLUGIN_SHA256
    return {
        **({"recording_session": {"profile": PROFILE}} if session else {}),
        "renderer_profile": RENDERER_PROFILE,
        "binary_profile": dict(get_native_replay_profile(CURRENT_PROFILE)["binary_profile"]),
        "plugin_sha256": plugin_sha,
        "plugin_source_sha256": plugin_sha,
        "plugin_staged_sha256": plugin_sha,
        "fps": 32,
        "capture_method": "native-windows-cs2-startmovie-tga",
        "hud_profile": {"raw_frames_masked": False},
        "hud_override": {
            "profile": OVERRIDE_PROFILE,
            "resource": HUD_RESOURCE,
            "staged_relative_path": HUD_RESOURCE,
            "override_resource_sha256": OVERRIDE_RESOURCE_SHA256,
            "delivery": "identical_loose_and_chunked_vpk_resource",
            "private_archive_relative_path": "pakchicken_hud_dir.vpk",
            "private_chunk_relative_path": "pakchicken_hud_000.vpk",
            "private_chunk_sha256": OVERRIDE_RESOURCE_SHA256,
            "installed_resources_modified": False,
            "raw_frames_masked": False,
        },
    }


def _matching_setup(value, expected):
    """Compare only the fixed setup fields, without traversing frame metadata."""
    if not isinstance(value, dict):
        return False
    for key, required in expected.items():
        actual = value.get(key)
        if isinstance(required, dict):
            if not _matching_setup(actual, required):
                return False
        elif type(actual) is not type(required) or actual != required:
            return False
    return True


def trusted_hud_policy(render):
    """Return the named setup policy; unknown setups require a policy decision.

    The render contract independently authenticates capture files and lifecycle.
    This helper intentionally makes no per-capture visual claim and requires no
    manual review registration. Future renderers or HUD resources are outside
    this version's scope, even if their manifest claims visual acceptance.
    """
    setup = _expected_setup(isinstance(render, dict) and "recording_session" in render)
    compatible = _matching_setup(render, setup)
    return {
        "profile": POLICY_PROFILE,
        "status": "trusted_capture_setup" if compatible else "unsupported_capture_setup",
        "basis": "user_approved_capture_setup",
        "scope": POLICY_SCOPE,
        "visual_review_performed": False,
        "reason_codes": [] if compatible else ["competitive_hud_capture_setup_unsupported"],
        "capture_setup": setup,
        "limits": [
            "HUD suitability is assumed from the user-approved setup; individual images are not visually checked.",
            "The policy does not establish resource use, overlay absence or HUD preservation in any individual image.",
            "Separate native POV, timing, pixel correspondence, source and label checks remain required.",
        ],
    }


def policy_allows_capture(policy):
    """Recognize this exact assumption and scope, not a generic approval flag."""
    return (isinstance(policy, dict) and policy.get("profile") == POLICY_PROFILE and
        policy.get("status") == "trusted_capture_setup" and
        policy.get("basis") == "user_approved_capture_setup" and policy.get("scope") == POLICY_SCOPE and
        policy.get("visual_review_performed") is False and policy.get("reason_codes") == [] and
        any(_matching_setup(policy.get("capture_setup"), _expected_setup(session)) for session in (False, True)))
