"""Small standard-library cross-platform advisory file lock."""
from __future__ import annotations

import importlib
import errno
import os
import time
from contextlib import contextmanager


def _lock_windows(handle):
    msvcrt = importlib.import_module("msvcrt")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write("\0")
        handle.flush()
    handle.seek(0)
    while True:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            time.sleep(0.05)


def _unlock_windows(handle):
    msvcrt = importlib.import_module("msvcrt")
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def lock_file(handle, *, platform=None):
    """Acquire a blocking exclusive advisory lock on an open file."""
    platform = platform or os.name
    if platform == "nt":
        _lock_windows(handle)
        return
    if platform == "posix":
        fcntl = importlib.import_module("fcntl")
        fcntl.flock(handle, fcntl.LOCK_EX)
        return
    raise OSError(f"unsupported file-lock platform: {platform}")


def unlock_file(handle, *, platform=None):
    """Release a lock previously acquired with :func:`lock_file`."""
    platform = platform or os.name
    if platform == "nt":
        _unlock_windows(handle)
        return
    if platform == "posix":
        fcntl = importlib.import_module("fcntl")
        fcntl.flock(handle, fcntl.LOCK_UN)
        return
    raise OSError(f"unsupported file-lock platform: {platform}")


@contextmanager
def exclusive_file_lock(handle, *, platform=None):
    lock_file(handle, platform=platform)
    try:
        yield handle
    finally:
        unlock_file(handle, platform=platform)
