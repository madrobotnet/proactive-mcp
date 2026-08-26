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

import json
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from scripts.alpha_bundle_models import (
    BuildConfig,
    BuildError,
    ManifestError,
    RunSpec,
    WheelSource,
)
from scripts.alpha_bundle_support import (
    download as _download,
)
from scripts.alpha_bundle_support import (
    run as _run,
)
from scripts.alpha_bundle_support import (
    sha256 as _sha256,
)
from scripts.alpha_bundle_support import (
    verify as _verify_bundle,
)
from scripts.alpha_bundle_support import (
    wheel_sources as _wheel_sources,
)
from scripts.alpha_bundle_support import (
    write_archive as _write_archive,
)

_ARCHIVE_NAME: Final = "proactive-mcp-alpha-linux-aarch64-py311.tar.gz"
_ARCHIVE_ROOT: Final = "proactive-mcp-alpha"
_EXPECTED_ARGUMENT_COUNT: Final = 1


for _public_type in (BuildConfig, RunSpec, WheelSource, BuildError, ManifestError):
    _public_type.__module__ = __name__
del _public_type

Runner = Callable[[RunSpec], None]
Downloader = Callable[[WheelSource, Path], None]

__all__ = [
    "BuildConfig",
    "BuildError",
    "ManifestError",
    "RunSpec",
    "WheelSource",
    "build_bundle",
    "main",
    "verify_bundle",
]


def verify_bundle(root: Path) -> int:
    """Verify that every staged wheel matches the deterministic manifest."""
    return _verify_bundle(root)


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
