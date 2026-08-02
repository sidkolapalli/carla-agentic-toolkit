"""Shared CARLA MCP exceptions."""


class CarlaAdapterError(RuntimeError):
    """Raised when CARLA cannot satisfy an adapter operation."""


class ActorRegistryError(RuntimeError):
    """Raised when persistent actor-name state cannot satisfy an operation."""
