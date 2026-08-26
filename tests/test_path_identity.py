from pathlib import Path

import pytest

from proactive_mcp.paths import ProactivePaths, resolve_paths
from proactive_mcp.sources.credentials import CredentialStore
from proactive_mcp.store.private_file import read_private_text, write_private_text


def test_relative_database_root_is_normalized_before_paths_are_derived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    paths = resolve_paths({"PROACTIVE_DATABASE": "profile/state/proactive.db"})

    assert paths.database == tmp_path / "profile/state/proactive.db"
    assert paths.config == tmp_path / "profile/state/config.toml"
    assert paths.state_directory.is_absolute()


def test_direct_path_and_credential_roots_are_normalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    paths = ProactivePaths.for_database(Path("first/state/proactive.db"))
    credentials = CredentialStore(Path("second/state"))

    assert paths.database == tmp_path / "first/state/proactive.db"
    assert credentials.state_directory == tmp_path / "second/state"
    assert credentials.file_path.is_absolute()


def test_relative_credential_profiles_use_distinct_declared_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    first = CredentialStore(Path("first/shared/state"))
    second = CredentialStore(Path("second/shared/state"))

    write_private_text(first.file_path, "first-profile")
    write_private_text(second.file_path, "second-profile")

    assert first.file_path != second.file_path
    assert first.file_path.exists()
    assert second.file_path.exists()
    assert read_private_text(first.file_path) == "first-profile"
    assert read_private_text(second.file_path) == "second-profile"
