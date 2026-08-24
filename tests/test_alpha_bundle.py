from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from scripts.build_alpha_bundle import (
    BuildConfig,
    ManifestError,
    RunSpec,
    WheelSource,
    build_bundle,
    verify_bundle,
)
from typing_extensions import TypedDict

_DEPENDENCY_BYTES = b"locked dependency wheel"
_DEPENDENCY_HASH = hashlib.sha256(_DEPENDENCY_BYTES).hexdigest()
_DEPENDENCY_NAME = "dependency-1.0-py3-none-any.whl"
_DEPENDENCY_URL = f"https://files.pythonhosted.org/packages/{_DEPENDENCY_NAME}"
_PROJECT_WHEEL = "proactive_mcp-0.1.0-py3-none-any.whl"


class TargetMetadata(TypedDict):
    implementation: str
    platform: str
    python_version: str


class BundleMetadata(TypedDict):
    install_command: list[str]
    target: TargetMetadata


class FakeUvRunner:
    """Record uv commands while materializing their declared outputs."""

    def __init__(self) -> None:
        self.calls: list[RunSpec] = []

    def __call__(self, spec: RunSpec) -> None:
        self.calls.append(spec)
        if spec.step == "build-project-wheel":
            output = Path(spec.argv[spec.argv.index("--out-dir") + 1])
            output.mkdir(parents=True)
            _ = (output / _PROJECT_WHEEL).write_bytes(b"project wheel")
        if spec.step == "export-locked-requirements":
            _ = Path(spec.argv[spec.argv.index("--output-file") + 1]).write_text(
                "dependency==1.0\n",
                encoding="utf-8",
            )
        if spec.step == "resolve-target-wheels":
            _ = Path(spec.argv[spec.argv.index("--output-file") + 1]).write_text(
                "\n".join(
                    (
                        'lock-version = "1.0"',
                        "[[packages]]",
                        'name = "dependency"',
                        'version = "1.0"',
                        "".join(("wheels = [{ ",
                                 f'url = "{_DEPENDENCY_URL}", ',
                                 "hashes = { sha256 = ",
                                 f'"{_DEPENDENCY_HASH}" }} }}]')),
                    )
                ),
                encoding="utf-8",
            )


class FakeDownloader:
    """Materialize the one locked wheel without network access."""

    def __init__(self) -> None:
        self.sources: list[WheelSource] = []

    def __call__(self, source: WheelSource, destination: Path) -> None:
        self.sources.append(source)
        _ = destination.write_bytes(_DEPENDENCY_BYTES)


def _build(tmp_path: Path) -> tuple[Path, FakeUvRunner, FakeDownloader]:
    project = tmp_path / "project"
    project.mkdir(parents=True)
    _ = (project / "uv.lock").write_text(
        "".join(("wheels = [{ ", f'url = "{_DEPENDENCY_URL}", ',
                 f'hash = "sha256:{_DEPENDENCY_HASH}" }}]\n')),
        encoding="utf-8",
    )
    runner = FakeUvRunner()
    downloader = FakeDownloader()

    archive = build_bundle(
        BuildConfig(project_root=project, output_directory=tmp_path / "output"),
        runner,
        downloader,
    )

    return archive, runner, downloader


def _extract(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, mode="r:gz") as bundle:
        bundle.extractall(destination, filter="data")
    return destination / "proactive-mcp-alpha"


def test_build_emits_one_archive_with_installable_layout(tmp_path: Path) -> None:
    # Given: a locked dependency and hermetic uv/downloader fakes.
    # When: the alpha bundle is built.
    archive, _, _ = _build(tmp_path)
    root = _extract(archive, tmp_path / "extracted")

    # Then: one rooted archive contains wheels, checksums, and machine metadata.
    assert [path.name for path in archive.parent.iterdir()] == [archive.name]
    assert sorted(path.name for path in (root / "wheels").iterdir()) == [
        _DEPENDENCY_NAME,
        _PROJECT_WHEEL,
    ]
    metadata = TypeAdapter(BundleMetadata).validate_json(
        (root / "bundle-metadata.json").read_text("utf-8")
    )
    assert metadata["target"] == {
        "implementation": "cpython",
        "platform": "manylinux_2_28_aarch64",
        "python_version": "3.11",
    }
    assert metadata["install_command"] == [
        "uv",
        "pip",
        "install",
        "--offline",
        "--no-index",
        "--find-links",
        "wheels",
        "proactive-mcp",
    ]
    assert verify_bundle(root) == 2


def test_builder_uses_frozen_target_specific_uv_commands(tmp_path: Path) -> None:
    # Given: a recorder standing in for uv.
    # When: the alpha bundle is built.
    _, runner, downloader = _build(tmp_path)

    # Then: uv builds locally and resolves only locked CPython 3.11/aarch64 wheels.
    assert [call.step for call in runner.calls] == [
        "build-project-wheel",
        "export-locked-requirements",
        "resolve-target-wheels",
    ]
    assert runner.calls[0].argv[:3] == ("uv", "build", "--wheel")
    assert "--frozen" in runner.calls[1].argv
    assert "--no-dev" in runner.calls[1].argv
    assert "--no-emit-project" in runner.calls[1].argv
    version_index = runner.calls[2].argv.index("--python-version")
    assert runner.calls[2].argv[version_index + 1] == "3.11"
    assert "aarch64-manylinux_2_28" in runner.calls[2].argv
    binary_index = runner.calls[2].argv.index("--only-binary")
    assert runner.calls[2].argv[
        binary_index : binary_index + 2
    ] == ("--only-binary", ":all:")
    assert [source.filename for source in downloader.sources] == [_DEPENDENCY_NAME]


def test_verify_rejects_a_tampered_wheel_before_install(tmp_path: Path) -> None:
    # Given: an extracted bundle whose dependency wheel has been modified.
    archive, _, _ = _build(tmp_path)
    root = _extract(archive, tmp_path / "tampered")
    with (root / "wheels" / _DEPENDENCY_NAME).open("ab") as wheel:
        _ = wheel.write(b"tampered")

    # When/Then: verification fails instead of accepting the wheel for installation.
    with pytest.raises(ManifestError, match="checksum-mismatch"):
        _ = verify_bundle(root)


def test_repeated_builds_are_byte_identical(tmp_path: Path) -> None:
    # Given: two builds with identical project and dependency inputs.
    # When: each build emits its archive independently.
    first, _, _ = _build(tmp_path / "first")
    second, _, _ = _build(tmp_path / "second")

    # Then: the complete compressed archives are reproducible.
    assert first.read_bytes() == second.read_bytes()
