"""Pinned native implementation of the recording-session contract."""

PROFILE = "cs2-recording-session-v1"
PLUGIN_SHA256 = "23042660ab6b211fece39a2295c1b7b0011629fef6e67dd16acaa31f2183a5b5"


def require(value, message):
    if not value:
        raise ValueError(message)
