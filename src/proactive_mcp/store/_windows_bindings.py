"""Typed ctypes declarations and Win32 bindings for private storage."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import TYPE_CHECKING, Final, final

if TYPE_CHECKING:
    from collections.abc import Callable

_WINDOWS_ONLY_MESSAGE: Final[str] = (
    "proactive_mcp.store.windows_path is available only on Windows"
)

if sys.platform != "win32":
    raise ImportError(_WINDOWS_ONLY_MESSAGE)

DACL_SECURITY_INFORMATION: Final[int] = 0x00000004
ERROR_FILE_NOT_FOUND: Final[int] = 2
ERROR_INSUFFICIENT_BUFFER: Final[int] = 122
FILE_ATTRIBUTE_DIRECTORY: Final[int] = 0x00000010
FILE_ATTRIBUTE_NORMAL: Final[int] = 0x00000080
FILE_ATTRIBUTE_REPARSE_POINT: Final[int] = 0x00000400
FILE_FLAG_BACKUP_SEMANTICS: Final[int] = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT: Final[int] = 0x00200000
FILE_SHARE_DELETE: Final[int] = 0x00000004
FILE_SHARE_READ: Final[int] = 0x00000001
FILE_SHARE_WRITE: Final[int] = 0x00000002
GENERIC_ALL: Final[int] = 0x10000000
GENERIC_READ: Final[int] = 0x80000000
GENERIC_WRITE: Final[int] = 0x40000000
INVALID_HANDLE_VALUE: Final[int] = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
LOCKFILE_EXCLUSIVE_LOCK: Final[int] = 0x00000002
NO_INHERITANCE: Final[int] = 0
PROTECTED_DACL_SECURITY_INFORMATION: Final[int] = 0x80000000
READ_CONTROL: Final[int] = 0x00020000
SE_FILE_OBJECT: Final[int] = 1
SET_ACCESS: Final[int] = 2
SUB_CONTAINERS_AND_OBJECTS_INHERIT: Final[int] = 3
TOKEN_QUERY: Final[int] = 0x0008
TOKEN_USER: Final[int] = 1
TRUSTEE_IS_SID: Final[int] = 0
TRUSTEE_IS_UNKNOWN: Final[int] = 0
WRITE_DAC: Final[int] = 0x00040000


@final
class _SidAndAttributes(ctypes.Structure):
    sid: int | None
    attributes: int
    _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]

    def __init__(self) -> None:
        """Initialize the typed ctypes fields."""
        super().__init__()
        self.sid = None
        self.attributes = 0


@final
class TokenUser(ctypes.Structure):
    """Represent the TokenUser result from GetTokenInformation."""

    user: _SidAndAttributes
    _fields_ = [("user", _SidAndAttributes)]

    def __init__(self) -> None:
        """Initialize the typed ctypes field."""
        super().__init__()
        self.user = _SidAndAttributes()


@final
class _TrusteeW(ctypes.Structure):
    multiple_trustee: int | None
    multiple_trustee_operation: int
    trustee_form: int
    trustee_type: int
    name: int | None
    _fields_ = [
        ("multiple_trustee", ctypes.c_void_p),
        ("multiple_trustee_operation", wintypes.DWORD),
        ("trustee_form", wintypes.DWORD),
        ("trustee_type", wintypes.DWORD),
        ("name", ctypes.c_void_p),
    ]

    def __init__(self) -> None:
        """Initialize the typed ctypes fields."""
        super().__init__()
        self.multiple_trustee = None
        self.multiple_trustee_operation = 0
        self.trustee_form = 0
        self.trustee_type = 0
        self.name = None


@final
class ExplicitAccessW(ctypes.Structure):
    access_permissions: int
    access_mode: int
    inheritance: int
    trustee: _TrusteeW
    _fields_ = [
        ("access_permissions", wintypes.DWORD),
        ("access_mode", wintypes.DWORD),
        ("inheritance", wintypes.DWORD),
        ("trustee", _TrusteeW),
    ]

    def __init__(self) -> None:
        """Initialize the typed ctypes fields."""
        super().__init__()
        self.access_permissions = 0
        self.access_mode = 0
        self.inheritance = 0
        self.trustee = _TrusteeW()


@final
class Overlapped(ctypes.Structure):
    """Represent the byte range used by LockFileEx and UnlockFileEx."""

    _fields_ = [
        ("internal", ctypes.c_size_t),
        ("internal_high", ctypes.c_size_t),
        ("offset", wintypes.DWORD),
        ("offset_high", wintypes.DWORD),
        ("event", wintypes.HANDLE),
    ]


@final
class ByHandleFileInformation(ctypes.Structure):
    """Represent metadata returned by GetFileInformationByHandle."""

    file_attributes: int
    _fields_ = [
        ("file_attributes", wintypes.DWORD),
        ("creation_time", wintypes.FILETIME),
        ("last_access_time", wintypes.FILETIME),
        ("last_write_time", wintypes.FILETIME),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    ]

    def __init__(self) -> None:
        """Initialize the typed ctypes field."""
        super().__init__()
        self.file_attributes = 0


_rawkernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_rawadvapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

_rawkernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_rawkernel32.CloseHandle.restype = wintypes.BOOL
_rawkernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
_rawkernel32.CreateFileW.restype = wintypes.HANDLE
_rawkernel32.GetCurrentProcess.argtypes = []
_rawkernel32.GetCurrentProcess.restype = wintypes.HANDLE
_rawkernel32.GetFileInformationByHandle.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(ByHandleFileInformation),
]
_rawkernel32.GetFileInformationByHandle.restype = wintypes.BOOL
_rawkernel32.LocalFree.argtypes = [ctypes.c_void_p]
_rawkernel32.LocalFree.restype = ctypes.c_void_p
_rawkernel32.LockFileEx.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(Overlapped),
]
_rawkernel32.LockFileEx.restype = wintypes.BOOL
_rawkernel32.UnlockFileEx.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(Overlapped),
]
_rawkernel32.UnlockFileEx.restype = wintypes.BOOL
_rawadvapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
_rawadvapi32.GetTokenInformation.restype = wintypes.BOOL
_rawadvapi32.OpenProcessToken.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
]
_rawadvapi32.OpenProcessToken.restype = wintypes.BOOL
_rawadvapi32.SetEntriesInAclW.argtypes = [
    wintypes.ULONG,
    ctypes.POINTER(ExplicitAccessW),
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
]
_rawadvapi32.SetEntriesInAclW.restype = wintypes.DWORD
_rawadvapi32.SetSecurityInfo.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
_rawadvapi32.SetSecurityInfo.restype = wintypes.DWORD


def _unavailable(*_arguments: int) -> int:
    raise RuntimeError(_WINDOWS_ONLY_MESSAGE)


class _Kernel32:
    close_handle: Callable[..., int]
    create_file: Callable[..., int]
    current_process: Callable[..., int]
    file_information: Callable[..., int]
    local_free: Callable[..., int | None]
    lock_file: Callable[..., int]
    unlock_file: Callable[..., int]

    def __init__(self) -> None:
        self.close_handle = _unavailable
        self.create_file = _unavailable
        self.current_process = _unavailable
        self.file_information = _unavailable
        self.local_free = _unavailable
        self.lock_file = _unavailable
        self.unlock_file = _unavailable


class _Advapi32:
    get_token_information: Callable[..., int]
    open_process_token: Callable[..., int]
    set_entries_in_acl: Callable[..., int]
    set_security_info: Callable[..., int]

    def __init__(self) -> None:
        self.get_token_information = _unavailable
        self.open_process_token = _unavailable
        self.set_entries_in_acl = _unavailable
        self.set_security_info = _unavailable


def _install_binding(
    library: _Kernel32 | _Advapi32,
    name: str,
    function: Callable[..., int | None],
) -> None:
    setattr(library, name, function)


kernel32 = _Kernel32()
advapi32 = _Advapi32()
_install_binding(kernel32, "close_handle", _rawkernel32.CloseHandle)
_install_binding(kernel32, "create_file", _rawkernel32.CreateFileW)
_install_binding(kernel32, "current_process", _rawkernel32.GetCurrentProcess)
_install_binding(kernel32, "file_information", _rawkernel32.GetFileInformationByHandle)
_install_binding(kernel32, "local_free", _rawkernel32.LocalFree)
_install_binding(kernel32, "lock_file", _rawkernel32.LockFileEx)
_install_binding(kernel32, "unlock_file", _rawkernel32.UnlockFileEx)
_install_binding(advapi32, "get_token_information", _rawadvapi32.GetTokenInformation)
_install_binding(advapi32, "open_process_token", _rawadvapi32.OpenProcessToken)
_install_binding(advapi32, "set_entries_in_acl", _rawadvapi32.SetEntriesInAclW)
_install_binding(advapi32, "set_security_info", _rawadvapi32.SetSecurityInfo)
