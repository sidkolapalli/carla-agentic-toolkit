"""Shared CARLA MCP exceptions."""


class CarlaAdapterError(RuntimeError):
    """Raised when CARLA cannot satisfy an adapter operation."""


class ActorRegistryError(RuntimeError):
    """Raised when persistent actor-name state cannot satisfy an operation."""


class OwnershipError(RuntimeError):
    """Raised when an execution ownership journal cannot be updated."""


class UnsupportedFeatureError(RuntimeError):
    """Raised when the connected runtime lacks a requested capability."""
