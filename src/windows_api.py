"""Windows API layer — ctypes wrappers for Win32 functions + psutil helpers."""

import ctypes
from ctypes import wintypes
from typing import Optional

import psutil

# --- ctypes setup ---

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# --- Foreground window ---

def get_foreground_window() -> int:
    """Get the handle (HWND) of the current foreground window."""
    return user32.GetForegroundWindow()


def get_window_text(hwnd: int) -> str:
    """Get the title text of a window."""
    if hwnd == 0:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        # Try GetWindowTextW directly — some windows return 0 length but have text
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buffer, 256)
        return buffer.value or ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value or ""


def get_window_process_id(hwnd: int) -> int:
    """Get the process ID (PID) of the process that owns a window."""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


# --- Idle detection ---

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


def get_last_input_ticks() -> int:
    """
    Returns milliseconds since the last user input (keyboard/mouse).
    This is the system-wide idle time, not per-application.
    """
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0
    return kernel32.GetTickCount() - lii.dwTime


# --- Process helpers (psutil) ---

def get_process_name(pid: int) -> str:
    """Get the executable name for a PID. Returns friendly fallback on failure."""
    if pid == 0:
        return "System Idle Process"
    try:
        proc = psutil.Process(pid)
        return proc.name() or "unknown.exe"
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return "<exited>"


def get_process_path(pid: int) -> Optional[str]:
    """Get the full executable path for a PID. Returns None on failure."""
    if pid == 0:
        return None
    try:
        proc = psutil.Process(pid)
        return proc.exe()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


# --- Boot time ---

def get_boot_time() -> Optional[str]:
    """Get system boot time as ISO 8601 string."""
    import datetime
    try:
        bt = psutil.boot_time()
        dt = datetime.datetime.fromtimestamp(bt)
        return dt.isoformat(sep=" ", timespec="seconds")
    except Exception:
        return None


# --- System info helpers ---

def get_file_size_bytes(path: str) -> int:
    """Get file size in bytes. Returns 0 if file doesn't exist."""
    import os
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
