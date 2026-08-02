"""Bounded MCP publication for durable CARLA image captures."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Never

CAPTURE_SNAPSHOT_PREFIX = "carla-snapshot://captures/"
CAPTURE_RESOURCE_PREFIX = "carla-output://capture/"
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 512 * 1024
MAX_MCP_OUTPUT_BYTES = 1024 * 1024


class OutputContentError(RuntimeError):
    """A stable publication failure safe to return through MCP."""

    def __init__(self, error_type: str, message: str) -> None:
        """Store the structured error classification."""
        super().__init__(message)
        self.error_type = error_type


@dataclass(frozen=True, slots=True)
class PublishedCapture:
    """One validated image ready for MCP content and resource linking."""

    data: str
    mime_type: str
    name: str
    size: int
    resource_uri: str


def published_captures(
    snapshots: dict[str, object],
    output_dir: Path,
    *,
    text_bytes: int,
) -> tuple[PublishedCapture, ...]:
    """Return captured images within count, file, and combined-result limits."""
    paths = _capture_paths(snapshots)
    if len(paths) > MAX_IMAGES:
        _raise("too_many_images", f"At most {MAX_IMAGES} images may be returned.")
    captures: list[PublishedCapture] = []
    total_bytes = text_bytes
    for path in paths:
        data, mime_type, relative = _read_image(path, output_dir)
        encoded = base64.b64encode(data).decode("ascii")
        resource_uri = capture_resource_uri(relative)
        total_bytes += sum(map(len, (encoded, resource_uri, relative.name, mime_type)))
        if total_bytes > MAX_MCP_OUTPUT_BYTES:
            _raise(
                "image_output_too_large",
                f"Text and encoded images exceed {MAX_MCP_OUTPUT_BYTES} bytes.",
            )
        captures.append(
            PublishedCapture(
                data=encoded,
                mime_type=mime_type,
                name=relative.name,
                size=len(data),
                resource_uri=resource_uri,
            )
        )
    return tuple(captures)


def capture_resource_uri(relative_path: Path) -> str:
    """Encode one relative output path as an opaque resource URI."""
    if relative_path.is_absolute() or ".." in relative_path.parts:
        _raise("image_path_rejected", "Capture resource path must stay below the output directory.")
    token = base64.urlsafe_b64encode(relative_path.as_posix().encode()).decode().rstrip("=")
    return CAPTURE_RESOURCE_PREFIX + token


def read_capture_resource(token: str, output_dir: Path) -> bytes:
    """Read a capture resource token through the same path and image checks."""
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode()
    except (UnicodeDecodeError, ValueError) as exc:
        error_type = "image_path_rejected"
        message = "Invalid capture resource token."
        raise OutputContentError(error_type, message) from exc
    data, _mime_type, _relative = _read_image(Path(decoded), output_dir)
    return data


def _capture_paths(snapshots: dict[str, object]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for uri, value in sorted(snapshots.items()):
        path = _published_path(uri, value)
        if path is not None:
            paths.append(path)
    return tuple(paths)


def _published_path(uri: str, value: object) -> Path | None:
    if not uri.startswith(CAPTURE_SNAPSHOT_PREFIX):
        return None
    if not isinstance(value, dict) or value.get("publish") is not True:
        return None
    path = value.get("path")
    if not isinstance(path, str):
        _raise("image_metadata_error", f"Capture snapshot {uri} has no string path.")
    return Path(path)


def _read_image(path: Path, output_dir: Path) -> tuple[bytes, str, Path]:
    root, resolved = _resolved_capture(path, output_dir)
    data = _read_bounded_image(resolved)
    return data, _image_mime_type(data), resolved.relative_to(root)


def _read_bounded_image(path: Path) -> bytes:
    try:
        if path.stat().st_size > MAX_IMAGE_BYTES:
            _raise("image_too_large", f"One image exceeds {MAX_IMAGE_BYTES} bytes.")
        data = path.read_bytes()
    except OutputContentError:
        raise
    except OSError as exc:
        error_type = "image_read_error"
        message = f"Capture could not be read: {exc}"
        raise OutputContentError(error_type, message) from exc
    if len(data) > MAX_IMAGE_BYTES:
        _raise("image_too_large", f"One image exceeds {MAX_IMAGE_BYTES} bytes.")
    return data


def _resolved_capture(path: Path, output_dir: Path) -> tuple[Path, Path]:
    root = output_dir.resolve()
    candidate = path if path.is_absolute() else root / path
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        error_type = "image_read_error"
        message = f"Capture could not be resolved: {exc}"
        raise OutputContentError(error_type, message) from exc
    if not resolved.is_relative_to(root):
        _raise("image_path_rejected", "Capture path is outside the output directory.")
    return root, resolved


def _image_mime_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return _raise("unsupported_image", "Only valid PNG and JPEG captures may be published.")


def _raise(error_type: str, message: str) -> Never:
    raise OutputContentError(error_type, message)
