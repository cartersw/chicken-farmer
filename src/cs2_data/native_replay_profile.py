"""Fixed native replay contracts; choosing one never grants runtime acceptance.

The current packet/filter profile follows a comparison against recovered legacy
binaries and archived 1.41.8.0 reader/filter disassembly. Runtime transcripts must
still match exact original-demo bytes and all companion capture checks.
Only the two registered string IDs are accepted by public auditors.
"""
from types import MappingProxyType

LEGACY_PROFILE = "cs2-14178-replay-v1"
CURRENT_PROFILE = "cs2-14180-competitive-replay-v1"

_CURRENT_BINARIES = MappingProxyType({
    "bin/win64/tier0.dll": "b9bf7c956bb31e11be649c31b096ea8813601748b9d984d39f10a9eb4458fb75",
    "bin/win64/rendersystemdx11.dll": "45b610ff89bb5adcb77a8d34b1f576b25e0ca389ffb3c4883f3243adfebf0500",
    "bin/win64/engine2.dll": "1fcf2920de28f625ee1ac5d6436c582ed381a4a913cfe2c9701b5551cc912d07",
    "csgo/bin/win64/client.dll": "809b62b2397e7849995ea427ed99fe2270d2f8ceae731e5ffa43c271e132f3ae",
    "csgo/bin/win64/server.dll": "cb5936528177b6da79be5dadcda0192be05feec687cb07dda0cd0e618a8f4d7c",
    "bin/win64/networksystem.dll": "fc33c097eca4ab0049590985a772b5b2f556523933d0482f86217889e1283127",
    "bin/win64/schemasystem.dll": "e3cff9d0dd23639da5a4e4b0267c6cd64f44598d8a2fb087b02f14a5af028512",
    "bin/win64/filesystem_stdio.dll": "a68eb1d28191b3f5d68989198b06b1842dd5dfc54ae098532a75bfc37c83f7ef",
})
_LEGACY_BINARIES = MappingProxyType({
    "bin/win64/engine2.dll": "26dc9c5fee70312e7851d87c2383f70cc4d036247d8de04e3cf342ccc20773ac",
    "csgo/bin/win64/client.dll": "b8e2c009763e8cefb88d89a2bdcf452db17501553d473db6da060df8e6769eb4",
})
PROFILES = MappingProxyType({
    LEGACY_PROFILE: MappingProxyType({
        "native_profile": LEGACY_PROFILE, "binary_profile": _LEGACY_BINARIES,
        "engine_sha256": _LEGACY_BINARIES["bin/win64/engine2.dll"],
        "client_sha256": _LEGACY_BINARIES["csgo/bin/win64/client.dll"],
        "seek_filter_policy": "cs2-14178-seek-message-filter-v1",
        "packet_bounds_profile": "engine_26dc9c5f_ordinary_returned_packet_prefix_v1",
    }),
    CURRENT_PROFILE: MappingProxyType({
        "native_profile": CURRENT_PROFILE, "binary_profile": _CURRENT_BINARIES,
        "engine_sha256": _CURRENT_BINARIES["bin/win64/engine2.dll"],
        "client_sha256": _CURRENT_BINARIES["csgo/bin/win64/client.dll"],
        "seek_filter_policy": "cs2-14180-seek-message-filter-v1",
        "packet_bounds_profile": "engine_1fcf2920_ordinary_returned_packet_prefix_v1",
    }),
})


def get_native_replay_profile(native_profile=LEGACY_PROFILE):
    if type(native_profile) is not str or native_profile not in PROFILES:
        raise ValueError("Unsupported fixed native replay profile")
    return PROFILES[native_profile]


def header_matches_profile(header, *, native_profile=LEGACY_PROFILE):
    profile = get_native_replay_profile(native_profile)
    observation = header.get("native_observation") if isinstance(header, dict) else None
    if (not isinstance(observation, dict) or header.get("engine_sha256") != profile["engine_sha256"] or
            observation.get("client_sha256") != profile["client_sha256"]):
        return False
    # Historical ledgers predate a native_profile marker. The current path is
    # explicit: same-build local calibration recordings are not replay proof.
    marker = header.get("native_profile")
    if native_profile == LEGACY_PROFILE:
        return marker in (None, LEGACY_PROFILE)
    return marker == CURRENT_PROFILE
