"""Trusted server configuration and conservative account inference reservations."""

import math
import threading
from dataclasses import dataclass
from typing import Protocol

# Reserve the documented maximum input context for every attempt. Reservations are
# never refunded: missing usage remains unknown, and retries cannot overspend it.
INPUT_TOKEN_RESERVATION = 65_536
OUTPUT_TOKEN_RESERVATION = 65_536
TOKEN_RESERVATION = INPUT_TOKEN_RESERVATION + OUTPUT_TOKEN_RESERVATION


@dataclass(frozen=True, slots=True)
class JevConfig:
    """Local limits; generated experiments cannot override provider/model/credentials."""

    timeout_seconds: float = 5.0
    max_attempts: int = 1
    retry_delay_seconds: float = 0.1
    max_requests: int = 40

    def __post_init__(self) -> None:
        """Reject limits that could make inference unbounded."""
        _bounded_number(self.timeout_seconds, 0.001, 30.0)
        _bounded_number(self.retry_delay_seconds, 0.0, 5.0)
        _bounded_number(self.max_attempts, 1, 2)
        _bounded_number(self.max_requests, 1, 200)


def _bounded_number(value: float, minimum: float, maximum: float) -> None:
    if not math.isfinite(value) or not minimum <= value <= maximum:
        msg = "Jev limits must be finite and within the supported bounds."
        raise ValueError(msg)


class ReservationBudget(Protocol):
    """A shared reservation boundary, independent of transport or worker lifetime."""

    def reserve(self) -> bool:
        """Atomically reserve one attempt's conservative total allowance."""
        ...


class AccountBudget:
    """Thread-safe cumulative input/request budget shared by every server run."""

    def __init__(self, max_requests: int, max_tokens: int) -> None:
        """Create a budget that an inference attempt must reserve atomically."""
        self._requests = max_requests
        self._tokens = max_tokens
        self._lock = threading.Lock()

    def reserve(self) -> bool:
        """Charge the full documented input ceiling, including failed attempts."""
        with self._lock:
            if self._requests < 1 or self._tokens < TOKEN_RESERVATION:
                return False
            self._requests -= 1
            self._tokens -= TOKEN_RESERVATION
            return True
