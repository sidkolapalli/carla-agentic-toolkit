"""Bounded session configuration and atomic file protocol fail closed."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from carla_agentic_toolkit.session_protocol import (
    ProtocolError,
    SessionConfig,
    read_message,
    write_message,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "value",
    [
        {"absolute_timeout_seconds": 3601},
        {"idle_timeout_seconds": 0},
        {"request_timeout_seconds": float("nan")},
        {"port": True},
        {"host": ""},
        {"provider_key": "must-not-forward"},
    ],
)
def test_invalid_session_config_is_rejected(value: dict[str, object]) -> None:
    """No caller may remove resource bounds or smuggle extra environment fields."""
    with pytest.raises(ValueError, match=r"must|unsupported"):
        SessionConfig.parse(value)


def test_protocol_roundtrip_is_one_atomic_bounded_object(tmp_path: Path) -> None:
    """Readers observe one complete request rather than partial file writes."""
    path = tmp_path / "request.json"
    write_message(path, {"sequence": 1, "code": "result = 3"})
    assert read_message(path) == {"sequence": 1, "code": "result = 3"}


def test_protocol_rejects_oversized_and_nonobject_payloads(tmp_path: Path) -> None:
    """Untrusted child IPC cannot force unlimited parent allocation or malformed state."""
    path = tmp_path / "response.json"
    path.write_text("x" * 1048577)
    with pytest.raises(ProtocolError, match="limit"):
        read_message(path)
    path.write_text("[]")
    with pytest.raises(ProtocolError, match="object"):
        read_message(path)


@pytest.mark.skipif(os.name != "posix", reason="Linux sandbox IPC uses O_NOFOLLOW")
def test_protocol_never_follows_child_controlled_symlinks(tmp_path: Path) -> None:
    """A malicious response link cannot trick the trusted parent into reading other files."""
    secret = tmp_path / "secret"
    secret.write_text('{"secret": "not-for-child"}')
    path = tmp_path / "response.json"
    path.symlink_to(secret)
    with pytest.raises(ProtocolError):
        read_message(path)
