"""Archive, lock, download, and process support for the alpha bundle builder."""

from __future__ import annotations

import gzip
import hashlib
import http.client
import os
import re
import subprocess
import tarfile
from contextlib import closing
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from scripts.alpha_bundle_models import (
    BuildError,
    ManifestError,
    RunSpec,
    WheelSource,
)

_COMMAND_TIMEOUT_SECONDS: Final = 300
_DOWNLOAD_TIMEOUT_SECONDS: Final = 60
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


def sha256(path: Path) -> str:
    """Return the SHA-256 digest of one file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run(spec: RunSpec) -> None:
    """Run one bounded bundle build command."""
    try:
        completed = subprocess.run(  # noqa: S603 - constant command assembly.
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


def download(source: WheelSource, destination: Path) -> None:
    """Download one approved wheel to its final destination."""
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


def wheel_sources(pylock: Path, uv_lock: Path) -> tuple[WheelSource, ...]:
    """Resolve target wheels proven by both generated and frozen locks."""
    locked = {
        (match.group("url"), match.group("sha256"))
        for match in _UV_LOCK_WHEEL.finditer(uv_lock.read_text(encoding="utf-8"))
    }
    sources: dict[str, WheelSource] = {}
    for match in _PYLOCK_WHEEL.finditer(pylock.read_text(encoding="utf-8")):
        url, expected = match.group("url"), match.group("sha256")
        filename = Path(urlsplit(url).path).name
        if (url, expected) not in locked:
            raise BuildError(reason="resolved-wheel-is-not-locked")
        previous = sources.get(filename)
        if previous is not None and previous.sha256 != expected:
            raise BuildError(reason="duplicate-wheel-filename")
        sources[filename] = WheelSource(url=url, filename=filename, sha256=expected)
    if not sources:
        raise BuildError(reason="target-resolution-has-no-wheels")
    return tuple(sources[name] for name in sorted(sources))


def _normalize_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.mtime, info.uid, info.gid = 0, 0, 0
    info.uname = info.gname = ""
    info.mode = 0o755 if info.isdir() else 0o644
    return info


def write_archive(root: Path, archive: Path) -> None:
    """Write one deterministic compressed tar archive."""
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


def verify(root: Path) -> int:
    """Verify the staged wheel manifest and return its entry count."""
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
        if sha256(root / "wheels" / filename) != expected:
            raise ManifestError(reason="checksum-mismatch")
    return len(entries)
