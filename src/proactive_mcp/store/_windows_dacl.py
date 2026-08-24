"""Current-user protected DACL construction for Windows private storage."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from pathlib import Path

from ._windows_bindings import (
    DACL_SECURITY_INFORMATION,
    ERROR_INSUFFICIENT_BUFFER,
    GENERIC_ALL,
    NO_INHERITANCE,
    OWNER_SECURITY_INFORMATION,
    PROTECTED_DACL_SECURITY_INFORMATION,
    SE_FILE_OBJECT,
    SET_ACCESS,
    TOKEN_OWNER,
    TOKEN_QUERY,
    TOKEN_USER,
    TRUSTEE_IS_SID,
    TRUSTEE_IS_UNKNOWN,
    ExplicitAccessW,
    TokenOwner,
    TokenUser,
    advapi32,
    kernel32,
)
from .storage_errors import UnsafeDatabasePathError


def set_private_dacl(handle: int, path: Path, *, inheritance: int) -> None:
    """Apply a protected DACL that grants access only to the current user."""
    acl = _current_user_dacl(path, inheritance)
    try:
        error_code = advapi32.set_security_info(
            handle,
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            acl,
            None,
        )
        if error_code != 0:
            _raise_error_code(path, error_code, "Windows ACL cannot be restricted")
    except UnsafeDatabasePathError:
        _ = kernel32.local_free(acl)
        raise
    if kernel32.local_free(acl) is not None:
        _raise_last_error(path, "Windows ACL memory cannot be released")


def require_current_user_owner(handle: int, path: Path) -> None:
    """Reject an existing object whose owner SID is not the current user."""
    token = wintypes.HANDLE()
    if not advapi32.open_process_token(
        kernel32.current_process(),
        TOKEN_QUERY,
        ctypes.byref(token),
    ):
        _raise_last_error(path, "current user token cannot be opened")
    token_value = token.value
    if token_value is None:
        raise UnsafeDatabasePathError(path, "current user token is invalid")
    descriptor = ctypes.c_void_p()
    try:
        user_buffer = _token_information(
            token_value,
            TOKEN_USER,
            path,
        )
        owner_buffer = _token_information(
            token_value,
            TOKEN_OWNER,
            path,
        )
        token_user = ctypes.cast(user_buffer, ctypes.POINTER(TokenUser)).contents
        token_owner = ctypes.cast(owner_buffer, ctypes.POINTER(TokenOwner)).contents
        owner = ctypes.c_void_p()
        error_code = advapi32.get_security_info(
            handle,
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            None,
            None,
            ctypes.byref(descriptor),
        )
        if error_code != 0:
            _raise_error_code(path, error_code, "Windows owner cannot be read")
        if (
            owner.value is None
            or token_user.user.sid is None
            or token_owner.owner is None
        ):
            raise UnsafeDatabasePathError(path, "current user identity is invalid")
        owner_matches_user = advapi32.equal_sid(owner, token_user.user.sid)
        owner_matches_default = advapi32.equal_sid(owner, token_owner.owner)
        if not (owner_matches_user or owner_matches_default):
            raise UnsafeDatabasePathError(path, "private path has a foreign owner")
    finally:
        if descriptor.value is not None:
            _ = kernel32.local_free(descriptor)
        _close_handle(token_value, path)


def _token_information(
    token: int,
    information_class: int,
    path: Path,
) -> ctypes.Array[ctypes.c_char]:
    required_size = wintypes.DWORD()
    _ = advapi32.get_token_information(
        token,
        information_class,
        None,
        0,
        ctypes.byref(required_size),
    )
    if ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER:
        _raise_last_error(path, "current user identity cannot be read")
    token_buffer = ctypes.create_string_buffer(required_size.value)
    if not advapi32.get_token_information(
        token,
        information_class,
        token_buffer,
        required_size,
        ctypes.byref(required_size),
    ):
        _raise_last_error(path, "current user identity cannot be read")
    return token_buffer


def _current_user_dacl(path: Path, inheritance: int) -> int:
    token = wintypes.HANDLE()
    if not advapi32.open_process_token(
        kernel32.current_process(),
        TOKEN_QUERY,
        ctypes.byref(token),
    ):
        _raise_last_error(path, "current user token cannot be opened")
    token_value = token.value
    if token_value is None:
        raise UnsafeDatabasePathError(path, "current user token is invalid")
    try:
        required_size = wintypes.DWORD()
        if advapi32.get_token_information(
            token_value,
            TOKEN_USER,
            None,
            0,
            ctypes.byref(required_size),
        ):
            raise UnsafeDatabasePathError(
                path,
                "current user security identifier is invalid",
            )
        if ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER:
            _raise_last_error(path, "current user security identifier cannot be read")
        token_information = ctypes.create_string_buffer(required_size.value)
        if not advapi32.get_token_information(
            token_value,
            TOKEN_USER,
            token_information,
            required_size.value,
            ctypes.byref(required_size),
        ):
            _raise_last_error(path, "current user security identifier cannot be read")
        sid = TokenUser.from_buffer(token_information).user.sid
        if sid is None:
            raise UnsafeDatabasePathError(
                path,
                "current user security identifier is invalid",
            )
    finally:
        _close_handle(token_value, path)
    entry = ExplicitAccessW()
    entry.access_permissions = GENERIC_ALL
    entry.access_mode = SET_ACCESS
    entry.inheritance = inheritance
    entry.trustee.multiple_trustee = None
    entry.trustee.multiple_trustee_operation = NO_INHERITANCE
    entry.trustee.trustee_form = TRUSTEE_IS_SID
    entry.trustee.trustee_type = TRUSTEE_IS_UNKNOWN
    entry.trustee.name = sid
    acl = ctypes.c_void_p()
    error_code = advapi32.set_entries_in_acl(
        1,
        ctypes.byref(entry),
        None,
        ctypes.byref(acl),
    )
    if error_code != 0:
        _raise_error_code(path, error_code, "current user ACL cannot be created")
    if acl.value is None:
        raise UnsafeDatabasePathError(path, "current user ACL cannot be created")
    return acl.value


def _close_handle(handle: int, path: Path) -> None:
    if not kernel32.close_handle(handle):
        _raise_last_error(path, "Windows handle cannot be closed")


def _raise_last_error(path: Path, reason: str) -> NoReturn:
    _raise_error_code(path, ctypes.get_last_error(), reason)


def _raise_error_code(path: Path, error_code: int, reason: str) -> NoReturn:
    error = OSError(error_code, "Win32 operation failed")
    raise UnsafeDatabasePathError(path, reason) from error
