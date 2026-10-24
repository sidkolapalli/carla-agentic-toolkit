"""Pure JSON-backed constructor values for import-free CARLA scripts."""

from __future__ import annotations

from typing import cast


class _Value(dict[str, object]):
    """Keep public component attributes and their JSON keys in the same storage."""

    def __getattr__(self, name: str) -> object:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: object) -> None:
        if name not in self:
            raise AttributeError(name)
        self[name] = value


class Vector3D(_Value):
    """Construct vector components; existing API parsers validate their numeric values."""

    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
        """Store components without weakening the facade's numeric validation."""
        super().__init__(x=x, y=y, z=z)


class Location(Vector3D):
    """Construct location components in CARLA's x, y, z order."""


class Rotation(_Value):
    """Construct rotation components in CARLA's pitch, yaw, roll order."""

    def __init__(self, pitch: float = 0.0, yaw: float = 0.0, roll: float = 0.0) -> None:
        """Store angles in degrees without converting invalid input types."""
        super().__init__(pitch=pitch, yaw=yaw, roll=roll)


class Transform(_Value):
    """Construct independent location/rotation values, without native math or RPCs."""

    def __init__(self, location: Location | None = None, rotation: Rotation | None = None) -> None:
        """Copy supplied components, or create independent zero defaults."""
        point = (
            Location()
            if location is None
            else Location(
                cast("float", location.x), cast("float", location.y), cast("float", location.z)
            )
        )
        angles = (
            Rotation()
            if rotation is None
            else Rotation(
                cast("float", rotation.pitch),
                cast("float", rotation.yaw),
                cast("float", rotation.roll),
            )
        )
        super().__init__(location=point, rotation=angles)


def value_constructors() -> dict[str, object]:
    """Return the reserved constructor globals for each script request."""
    return {
        "Location": Location,
        "Vector3D": Vector3D,
        "Rotation": Rotation,
        "Transform": Transform,
    }
