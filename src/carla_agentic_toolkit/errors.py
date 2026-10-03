"""Shared CARLA Agentic Toolkit exceptions."""


class CarlaAdapterError(RuntimeError):
    """Raised when CARLA cannot satisfy an adapter operation."""

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        """Retain optional partial-operation evidence alongside the original cause."""
        super().__init__(message)
        self.details = details or {}


class ActorRegistryError(RuntimeError):
    """Raised when persistent actor-name state cannot satisfy an operation."""


class OwnershipError(RuntimeError):
    """Raised when an execution ownership journal cannot be updated."""


class UnsupportedFeatureError(RuntimeError):
    """Raised when the connected runtime lacks a requested capability."""
