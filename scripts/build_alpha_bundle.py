#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run scripts/build_alpha_bundle.py /tmp/alpha-output
# 3. Or make executable and run:
#      chmod +x scripts/build_alpha_bundle.py
#      ./scripts/build_alpha_bundle.py /tmp/alpha-output
# ──────────────────

"""Build and verify the reproducible Linux aarch64 closed-alpha wheelhouse."""

from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

_ARCHIVE_NAME: Final = "proactive-mcp-alpha-linux-aarch64-py311.tar.gz"
_ARCHIVE_ROOT: Final = "proactive-mcp-alpha"
_COMMAND_TIMEOUT_SECONDS: Final = 300
_DOWNLOAD_TIMEOUT_SECONDS: Final = 60
_EXPECTED_ARGUMENT_COUNT: Final = 1
_WHEEL_URL_PATTERN: Final = r'url = "(?P<url>https://[^"\s]+\.whl)"'
_WHEEL_HASH_PATTERN: Final = r"(?P<sha256>[0-9a-f]{64})"
_PYLOCK_WHEEL: Final = re.compile(
    _WHEEL_URL_PATTERN + r'.*?hashes = \{ sha256 = "' + _WHEEL_HASH_PATTERN + r'" \}',
    re.DOTALL,
)
_UV_LOCK_WHEEL: Final = re.compile(
    _WHEEL_URL_PATTERN + r', hash = "sha256:' + _WHEEL_HASH_PATTERN + r'"'
)
_MANIFEST_LINE: Final = re.compile(
    r"(?P<sha256>[0-9a-f]{64})  wheels/(?P<filename>[^/\s]+\.whl)"
)


@dataclass(frozen=True, slots=True)
class BuildConfig:
    project_root: Path
    output_directory: Path


@dataclass(frozen=True, slots=True)
class RunSpec:
    step: str
    argv: tuple[str, ...]
    cwd: Path


@dataclass(frozen=True, slots=True)
class WheelSource:
    url: str
    filename: str
    sha256: str


class BuildError(Exception):
    def __init__(self, *, reason: str) -> None:
        super().__init__(reason)
        self.reason: Final = reason


class ManifestError(BuildError):
    pass


Runner = Callable[[RunSpec], None]
Downloader = Callable[[WheelSource, Path], None]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _run(spec: RunSpec) -> None:
    try:
        completed = subprocess.run(  # noqa: S603 - argv is assembled from constants.
            spec.argv,
            cwd=spec.cwd,
            env=os.environ | {"SOURCE_DATE_EPOCH": "315532800"},
            capture_output=True,
            text=True,
            check=False,
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise BuildError(reason=f"{spec.step}-timed-out") from error
    except OSError as error:
        raise BuildError(reason=f"{spec.step}-could-not-start") from error
    if completed.returncode != 0:
        raise BuildError(reason=f"{spec.step}-failed")


def _download(source: WheelSource, destination: Path) -> None:
    parsed = urlsplit(source.url)
    if parsed.scheme != "https" or parsed.hostname != "files.pythonhosted.org":
        raise BuildError(reason="wheel-source-is-not-approved")
    partial = destination.with_suffix(".part")
    try:
        connection = http.client.HTTPSConnection(
            parsed.hostname,
            timeout=_DOWNLOAD_TIMEOUT_SECONDS,
        )
        with closing(connection), partial.open("wb") as target:
            connection.request("GET", parsed.path)
            response = connection.getresponse()
            status = response.status
            while chunk := response.read(1024 * 1024):
                _ = target.write(chunk)
    except (OSError, http.client.HTTPException) as error:
        partial.unlink(missing_ok=True)
        raise BuildError(reason="wheel-download-failed") from error
    if status != http.client.OK:
        partial.unlink(missing_ok=True)
        raise BuildError(reason="wheel-download-failed")
    _ = partial.replace(destination)


def _wheel_sources(pylock: Path, uv_lock: Path) -> tuple[WheelSource, ...]:
    locked = {
        (match.group("url"), match.group("sha256"))
        for match in _UV_LOCK_WHEEL.finditer(uv_lock.read_text(encoding="utf-8"))
    }
    sources: dict[str, WheelSource] = {}
    for match in _PYLOCK_WHEEL.finditer(pylock.read_text(encoding="utf-8")):
        url, sha256 = match.group("url"), match.group("sha256")
        filename = Path(urlsplit(url).path).name
        if (url, sha256) not in locked:
            raise BuildError(reason="resolved-wheel-is-not-locked")
        previous = sources.get(filename)
        if previous is not None and previous.sha256 != sha256:
            raise BuildError(reason="duplicate-wheel-filename")
        sources[filename] = WheelSource(url=url, filename=filename, sha256=sha256)
    if not sources:
        raise BuildError(reason="target-resolution-has-no-wheels")
    return tuple(sources[name] for name in sorted(sources))


def _normalize_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.mtime, info.uid, info.gid = 0, 0, 0
    info.uname = info.gname = ""
    info.mode = 0o755 if info.isdir() else 0o644
    return info


def _write_archive(root: Path, archive: Path) -> None:
    with (
        archive.open("xb") as raw_archive,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw_archive, mtime=0) as zipped,
        tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as bundle,
    ):
        paths = (root, *sorted(root.rglob("*"), key=lambda path: path.as_posix()))
        for path in paths:
            bundle.add(
                path,
                arcname=path.relative_to(root.parent),
                recursive=False,
                filter=_normalize_tar_info,
            )


def verify_bundle(root: Path) -> int:
    """Verify that every staged wheel matches the deterministic manifest."""
    entries: dict[str, str] = {}
    for line in (root / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        match = _MANIFEST_LINE.fullmatch(line)
        if match is None:
            raise ManifestError(reason="malformed-manifest")
        filename = match.group("filename")
        if filename in entries:
            raise ManifestError(reason="duplicate-manifest-entry")
        entries[filename] = match.group("sha256")
    wheel_names = {path.name for path in (root / "wheels").glob("*.whl")}
    if wheel_names != entries.keys():
        raise ManifestError(reason="manifest-layout-mismatch")
    for filename, expected in entries.items():
        if _sha256(root / "wheels" / filename) != expected:
            raise ManifestError(reason="checksum-mismatch")
    return len(entries)


def build_bundle(
    config: BuildConfig,
    runner: Runner = _run,
    downloader: Downloader = _download,
) -> Path:
    """Build one reproducible archive for Linux aarch64 and CPython 3.11."""
    output = config.output_directory
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise BuildError(reason="output-directory-is-not-empty")
    archive = output / _ARCHIVE_NAME
    with tempfile.TemporaryDirectory(
        prefix=".proactive-mcp-alpha-", dir=output
    ) as temporary:
        work = Path(temporary)
        built, requirements = work / "built", work / "requirements.txt"
        pylock = work / "pylock.alpha.toml"
        commands = (
            (
                "build-project-wheel",
                ("uv", "build", "--wheel", "--out-dir", str(built), "."),
            ),
            (
                "export-locked-requirements",
                (
                    "uv",
                    "export",
                    "--frozen",
                    "--no-dev",
                    "--no-emit-project",
                    "--no-hashes",
                    "--format",
                    "requirements-txt",
                    "--output-file",
                    str(requirements),
                ),
            ),
            (
                "resolve-target-wheels",
                (
                    "uv",
                    "pip",
                    "compile",
                    str(requirements),
                    "--python-version",
                    "3.11",
                    "--python-platform",
                    "aarch64-manylinux_2_28",
                    "--only-binary",
                    ":all:",
                    "--format",
                    "pylock.toml",
                    "--output-file",
                    str(pylock),
                ),
            ),
        )
        for step, argv in commands:
            runner(RunSpec(step=step, argv=argv, cwd=config.project_root))
        project_wheels = tuple(built.glob("*.whl"))
        if len(project_wheels) != 1:
            raise BuildError(reason="project-build-did-not-produce-one-wheel")
        root, wheels = work / _ARCHIVE_ROOT, work / _ARCHIVE_ROOT / "wheels"
        wheels.mkdir(parents=True)
        _ = shutil.copy2(project_wheels[0], wheels / project_wheels[0].name)
        for source in _wheel_sources(pylock, config.project_root / "uv.lock"):
            destination = wheels / source.filename
            downloader(source, destination)
            if _sha256(destination) != source.sha256:
                raise ManifestError(reason="downloaded-wheel-checksum-mismatch")
        metadata = {
            "bundle_format": 1,
            "install_command": [
                "uv",
                "pip",
                "install",
                "--offline",
                "--no-index",
                "--find-links",
                "wheels",
                f"wheels/{project_wheels[0].name}",
            ],
            "lock_sha256": _sha256(config.project_root / "uv.lock"),
            "project_wheel": project_wheels[0].name,
            "target": {
                "implementation": "cpython",
                "platform": "manylinux_2_28_aarch64",
                "python_version": "3.11",
            },
            "verify_command": ["sha256sum", "--check", "SHA256SUMS"],
        }
        _ = (root / "bundle-metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        staged = sorted(wheels.glob("*.whl"), key=lambda path: path.name)
        checksums = "".join(f"{_sha256(item)}  wheels/{item.name}\n" for item in staged)
        _ = (root / "SHA256SUMS").write_text(checksums, encoding="utf-8", newline="\n")
        _ = verify_bundle(root)
        staged_archive = work / _ARCHIVE_NAME
        _write_archive(root, staged_archive)
        _ = staged_archive.replace(archive)
    return archive


def main(argv: Sequence[str] | None = None) -> int:
    """Build the bundle into the sole output-directory argument."""
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != _EXPECTED_ARGUMENT_COUNT:
        print("error: usage: OUTPUT_DIRECTORY", file=sys.stderr)  # noqa: T201
        return 1
    try:
        archive = build_bundle(BuildConfig(Path.cwd(), Path(arguments[0])))
    except BuildError as error:
        print(f"error: {error}", file=sys.stderr)  # noqa: T201 - CLI boundary.
        return 1
    print(f"built {archive.name}")  # noqa: T201 - CLI result.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
