"""Shared CARLA Agentic Toolkit exceptions."""


class CarlaAdapterError(RuntimeError):
    """Raised when CARLA cannot satisfy an adapter operation."""

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        """Retain optional partial-operation evidence alongside the original cause."""
        super().__init__(message)
        self.details = details or {}


class CarlaApiUnavailableError(CarlaAdapterError):
    """Distinguish local client import/ABI failures from simulator connectivity."""

    def __init__(self, cause: str) -> None:
        """Retain the native import diagnostic without suggesting a network retry."""
        super().__init__(
            f"CARLA Python API is not importable: {cause}",
            details={"error_type": "carla_api_unavailable", "retryable": False},
        )


class TrafficManagerUnavailableError(CarlaAdapterError):
    """Report native TM bind failure without claiming its exact underlying cause."""

    def __init__(self, port: int, cause: str) -> None:
        """Suggest a prestarted dedicated sidecar, never additional bind permissions."""
        super().__init__(
            f"Traffic Manager on port {port} could not be reached or created. "
            "Start a dedicated TM sidecar outside the sandbox for this simulator, "
            f"or use its correct port. Native diagnostic: {cause}",
            details={"error_type": "traffic_manager_unavailable", "traffic_manager_port": port},
        )


class ActorRegistryError(RuntimeError):
    """Raised when persistent actor-name state cannot satisfy an operation."""


class OwnershipError(RuntimeError):
    """Raised when an execution ownership journal cannot be updated."""


class UnsupportedFeatureError(RuntimeError):
    """Raised when the connected runtime lacks a requested capability."""
