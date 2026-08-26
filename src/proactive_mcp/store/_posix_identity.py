"""Stable POSIX file identity checks shared by private store operations."""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING

from .storage_errors import UnsafeDatabasePathError

if TYPE_CHECKING:
    from pathlib import Path

Identity = tuple[int, int]


def identity(observed: os.stat_result) -> Identity:
    """Return the filesystem identity carried by stat metadata."""
    return observed.st_dev, observed.st_ino


def verify_private_file(
    observed: os.stat_result,
    path: Path,
    label: str,
) -> None:
    """Require a current-user, single-link regular file."""
    if not stat.S_ISREG(observed.st_mode):
        raise UnsafeDatabasePathError(path, f"{label} is not a regular file")
    if observed.st_uid != os.getuid():
        raise UnsafeDatabasePathError(path, f"{label} is not user-owned")
    if observed.st_nlink != 1:
        raise UnsafeDatabasePathError(path, f"{label} has an ambiguous identity")


def verify_open_name(
    directory_fd: int,
    name: str,
    descriptor: int,
    path: Path,
    label: str,
) -> os.stat_result:
    """Revalidate an open descriptor and its pinned directory entry."""
    observed = os.fstat(descriptor)
    verify_private_file(observed, path, label)
    current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    verify_private_file(current, path, label)
    if identity(current) != identity(observed):
        raise UnsafeDatabasePathError(path, f"{label} identity changed")
    return observed
