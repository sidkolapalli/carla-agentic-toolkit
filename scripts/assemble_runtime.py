"""Assemble a headless Python runtime with its complete native-library provenance.

Run only in the Docker build stage. The final image needs no shell, package manager,
terminal UI, or native UUID extension (Python's uuid module has a portable fallback).
Debian metadata and licenses accompany every copied shared library for honest scans.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path("/opt/runtime-root")
OPTIONAL_EXTENSIONS = ("_curses", "readline.", "_uuid.", "_tkinter.")


def _ignore(path: str, names: list[str]) -> set[str]:
    if path == "/usr/local/bin":
        return set(names) & {"uv", "uvx", "pip", "pip3", "pip3.12"}
    if path.endswith("/lib-dynload"):
        return {name for name in names if name.startswith(OPTIONAL_EXTENSIONS)}
    return set()


def _elf(path: Path) -> bool:
    if not path.is_file():
        return False
    with path.open("rb") as stream:
        return stream.read(4) == b"\x7fELF"


def _libraries(path: Path) -> set[Path]:
    result = subprocess.run(  # noqa: S603
        ["/usr/bin/ldd", str(path)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "LD_LIBRARY_PATH": f"{path.parent}:/usr/local/lib"},
    )
    output = result.stdout + result.stderr
    static = "not a dynamic executable" in output or "statically linked" in output
    if "not found" in output or (result.returncode and not static):
        message = f"Unresolved native dependency for {path}: {output}"
        raise RuntimeError(message)
    lines = result.stdout.splitlines()
    return {_library_path(line) for line in lines if "/" in line}


def _library_path(line: str) -> Path:
    value = line.split("=>", 1)[-1].strip().split(" ", 1)[0]
    path = Path(value)
    if not path.is_absolute() or not path.exists():
        message = f"Invalid native library in ldd output: {line}"
        raise RuntimeError(message)
    return path.parent.resolve() / path.name


def _copy_library(path: Path) -> None:
    destination = ROOT / path.relative_to("/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination, follow_symlinks=False)
    if path.is_symlink():
        resolved = path.resolve()
        target = ROOT / resolved.relative_to("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resolved, target)


def _owners(libraries: set[Path]) -> set[str]:
    needed = {path.resolve() for path in libraries}
    packages = set()
    for listing in Path("/var/lib/dpkg/info").glob("*.list"):
        files = {Path(line).resolve() for line in listing.read_text().splitlines()}
        if needed & files:
            packages.add(listing.stem)
            needed.difference_update(files)
    needed = {path for path in needed if not str(path).startswith(("/usr/local/", "/app/"))}
    if needed:
        message = f"Copied system libraries have no Debian provenance: {sorted(needed)}"
        raise RuntimeError(message)
    return packages


def _package_metadata(package: str) -> None:
    status = ROOT / "var/lib/dpkg/status.d"
    status.mkdir(parents=True, exist_ok=True)
    name = package.split(":", 1)[0]
    control = subprocess.check_output(  # noqa: S603
        ["/usr/bin/dpkg-query", "--status", package],
        text=True,
    )
    (status / name).write_text(control)
    checksums = Path(f"/var/lib/dpkg/info/{package}.md5sums")
    if checksums.exists():
        shutil.copy2(checksums, status / f"{name}.md5sums")
    license_file = Path(f"/usr/share/doc/{name}/copyright")
    if license_file.exists():
        destination = ROOT / "usr/share/doc/carla-native-libraries" / name / "copyright"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(license_file, destination)


def main() -> None:
    """Copy the application, Python, and discovered native dependency closure."""
    for source in (Path("/usr/local"), Path("/app")):
        shutil.copytree(source, ROOT / source.relative_to("/"), symlinks=True, ignore=_ignore)
    libraries = set()
    for destination in ROOT.rglob("*"):
        if _elf(destination):
            libraries.update(_libraries(Path("/") / destination.relative_to(ROOT)))
    for library in libraries:
        if not str(library).startswith(("/usr/local/", "/app/")):
            _copy_library(library)
    packages = _owners(libraries)
    for package in sorted(packages):
        _package_metadata(package)
    manifest = {
        "packages": sorted(packages),
        "libraries": sorted(map(str, libraries)),
        "excluded_optional_extensions": list(OPTIONAL_EXTENSIONS),
    }
    (ROOT / "app/runtime-native-manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
