"""Fail-closed cooperative ownership shared by all local simulator mutators.

The flock has no expiry. Dirty recovery evidence is independent of process lifetime;
only a verified cleanup can clear it. This is not protection from unrelated clients.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import sys
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Self

from carla_agentic_toolkit.managed_control_io import read_control
from carla_agentic_toolkit.sandbox_paths import output_dir_path, read_only_paths

if TYPE_CHECKING:
    from types import TracebackType

fcntl = import_module("fcntl") if sys.platform == "linux" else None

MAX_RECOVERY_BYTES = 65_536


class LeaseBusyError(RuntimeError):
    """Another cooperating process still owns the mutation interval."""


class RecoveryRequiredError(RuntimeError):
    """An interrupted owner left evidence requiring explicit verified recovery."""

    def __init__(self, state: dict[str, object]) -> None:
        """Expose bounded recovery evidence to the trusted local coordinator."""
        super().__init__("Simulator recovery is required before another mutation.")
        self.state = state


def simulator_identity(host: str, port: int) -> str:
    """Resolve aliases to one endpoint; refuse ambiguous multi-address hosts."""
    addresses = {
        str(entry[4][0]) for entry in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    }
    canonical = {_address_identity(address) for address in addresses}
    if len(canonical) != 1:
        message = "Ambiguous simulator hostname; configure one stable literal IP for all clients."
        raise ValueError(message)
    return f"{canonical.pop()}:{port}"


def _address_identity(address: str) -> str:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return "loopback" if ip.is_loopback else ip.compressed


def private_state_root() -> Path:
    """Return local-owner-only state outside the generated-script allowlist."""
    configured = os.environ.get("CARLA_AGENTIC_TOOLKIT_STATE_DIR")
    root = Path(configured) if configured else Path.home() / ".local/state/carla-agentic-toolkit"
    root = root.expanduser().resolve()
    exposed = (*read_only_paths(), output_dir_path())
    if any(root.is_relative_to(path.resolve()) for path in exposed):
        message = "Trusted state must be outside all sandbox-readable and output directories."
        raise ValueError(message)
    return _private_directory(root)


def _private_directory(root: Path) -> Path:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        message = "Trusted state cannot be a symbolic link."
        raise ValueError(message)
    if os.name == "posix":
        metadata = root.stat()
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            message = "Trusted state must be owned by this user with permissions 0700."
            raise PermissionError(message)
    return root


class SimulatorLease:
    """Hold a non-expiring kernel lock until work AND cleanup have terminated."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        state_root: Path | None = None,
        recovering: bool = False,
    ) -> None:
        """Configure an endpoint; acquisition occurs at context entry."""
        self.identity = simulator_identity(host, port)
        self._root = _private_directory(state_root) if state_root else private_state_root()
        token = hashlib.sha256(self.identity.encode()).hexdigest()
        self._lock_path = self._root / f"{token}.lock"
        self._state_path = self._root / f"{token}.json"
        self._recovering = recovering
        self._fd: int | None = None
        self.recovery_state: dict[str, object] = {}

    def __enter__(self) -> Self:
        """Acquire immediately; dirty state blocks normal use even after a crash."""
        if fcntl is None:
            message = "Simulator leases require the supported Linux/WSL2 runtime."
            raise RuntimeError(message)
        descriptor = os.open(self._lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            message = "Simulator is owned by an active local run."
            raise LeaseBusyError(message) from exc
        self._fd = descriptor
        try:
            self._check_recovery()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _check_recovery(self) -> None:
        self.recovery_state = self._read_state()
        if self.recovery_state and not self._recovering:
            raise RecoveryRequiredError(self.recovery_state)

    @property
    def descriptor(self) -> int:
        """Borrow the lock for a sandbox process, preserving ownership on parent death."""
        if self._fd is None:
            message = "Lease is not held."
            raise RuntimeError(message)
        return self._fd

    def mark_dirty(self, state: dict[str, object]) -> None:
        """Persist bounded recovery evidence BEFORE initiating mutations."""
        _ = self.descriptor
        encoded = json.dumps(state, allow_nan=False)
        if len(encoded.encode()) > MAX_RECOVERY_BYTES:
            message = "Recovery state exceeds 64 KiB."
            raise ValueError(message)
        temporary = self._state_path.with_suffix(".tmp")
        descriptor = os.open(
            temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self._state_path)
        self.recovery_state = state.copy()

    def mark_clean(self) -> None:
        """Clear evidence only after callers verify cleanup/restoration succeeded."""
        _ = self.descriptor
        self._state_path.unlink(missing_ok=True)
        self.recovery_state = {}

    def _read_state(self) -> dict[str, object]:
        try:
            state = read_control(self._state_path)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError):
            return {"error": "unreadable_recovery_state"}
        return state or {"error": "empty_recovery_state"}

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Close this reference; never erase unverified cleanup evidence."""
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
