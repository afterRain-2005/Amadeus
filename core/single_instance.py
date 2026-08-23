"""Process-wide single-instance guards."""
from __future__ import annotations

import os


_handles: dict[str, object] = {}
_signal_handles: dict[str, object] = {}


def _signal_name(name: str) -> str:
    return f"Local\\{name}.Activate"


def create_instance_signal(name: str) -> None:
    """Create the activation event consumed by the primary instance."""
    if os.name != "nt" or name in _signal_handles:
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_event = kernel32.CreateEventW
    create_event.argtypes = (
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    create_event.restype = wintypes.HANDLE

    handle = create_event(None, False, False, _signal_name(name))
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateEventW failed")
    _signal_handles[name] = handle


def signal_existing_instance(name: str) -> bool:
    """Ask the primary instance to restore its application window."""
    if os.name != "nt":
        return False

    import ctypes
    from ctypes import wintypes

    event_modify_state = 0x0002
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_event = kernel32.OpenEventW
    open_event.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    open_event.restype = wintypes.HANDLE
    set_event = kernel32.SetEvent
    set_event.argtypes = (wintypes.HANDLE,)
    set_event.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_event(event_modify_state, False, _signal_name(name))
    if not handle:
        return False
    try:
        return bool(set_event(handle))
    finally:
        close_handle(handle)


def consume_instance_signal(name: str) -> bool:
    """Return whether an activation request is waiting, without blocking."""
    if os.name != "nt":
        return False
    handle = _signal_handles.get(name)
    if handle is None:
        return False

    import ctypes
    from ctypes import wintypes

    wait_object_0 = 0
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait_for_single_object.restype = wintypes.DWORD
    return wait_for_single_object(handle, 0) == wait_object_0


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


def release_instance_signal(name: str) -> None:
    """Close the activation event handle explicitly; primarily for tests."""
    handle = _signal_handles.pop(name, None)
    if handle is None or os.name != "nt":
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    close_handle(handle)
