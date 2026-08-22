"""Process-wide single-instance guards."""
from __future__ import annotations

import os


_handles: dict[str, object] = {}


def acquire_single_instance(name: str) -> bool:
    """Acquire a named Windows mutex for this process lifetime."""
    if os.name != "nt":
        return True
    if name in _handles:
        return False

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    create_mutex.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    mutex_name = f"Local\\{name}"
    handle = create_mutex(None, False, mutex_name)
    if not handle:
        raise OSError(ctypes.get_last_error(), f"CreateMutexW failed: {mutex_name}")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        close_handle(handle)
        return False
    _handles[name] = handle
    return True


def release_single_instance(name: str) -> None:
    """Release a guard explicitly; primarily useful for tests."""
    handle = _handles.pop(name, None)
    if handle is None or os.name != "nt":
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    close_handle(handle)
