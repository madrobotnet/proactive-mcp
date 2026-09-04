"""Trusted discovery of the executable used for the current CLI invocation."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Final

_UNTRUSTED_WRITE_BITS: Final[int] = stat.S_IWOTH
_EXECUTE_BITS: Final[int] = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


def current_executable() -> Path | None:
    """Return the exact trusted absolute executable named by this process."""
    candidate = Path(sys.argv[0])
    if not candidate.is_absolute():
        return None
    if os.name == "nt" and candidate.suffix.casefold() != ".exe":
        candidate = candidate.with_name(f"{candidate.name}.exe")
    flags = getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError:
        return None
    try:
        observed = os.fstat(descriptor)
        current = os.lstat(candidate)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    trusted_owner = True if os.name == "nt" else observed.st_uid in (0, os.getuid())
    executable = True if os.name == "nt" else bool(observed.st_mode & _EXECUTE_BITS)
    trusted_permissions = (
        True if os.name == "nt" else not observed.st_mode & _UNTRUSTED_WRITE_BITS
    )
    trusted_links = observed.st_nlink == current.st_nlink == 1
    trusted = (
        stat.S_ISREG(observed.st_mode)
        and trusted_owner
        and trusted_links
        and trusted_permissions
        and executable
        and (current.st_dev, current.st_ino) == (observed.st_dev, observed.st_ino)
    )
    return candidate if trusted else None
