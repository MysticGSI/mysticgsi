import contextlib
import os

try:
    import fcntl
except ImportError:
    fcntl = None
    import msvcrt

LOCK_PATH = os.environ.get("MYSTICGSI_LOCK", ".build.lock")


def _try_lock(fh) -> bool:
    try:
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except (BlockingIOError, PermissionError, OSError):
        return False


def _lock(fh):
    if fcntl:
        fcntl.flock(fh, fcntl.LOCK_EX)
        return
    # LK_LOCK gives up after ~10 s, so keep retrying until the holder exits.
    while True:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            return
        except OSError:
            pass


def _unlock(fh):
    if fcntl:
        fcntl.flock(fh, fcntl.LOCK_UN)
    else:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


@contextlib.contextmanager
def hold(on_busy=None, path=None):
    fh = open(path or LOCK_PATH, "a+")
    try:
        fh.seek(0)
        if not _try_lock(fh):
            if on_busy:
                on_busy()
            _lock(fh)
        try:
            yield
        finally:
            _unlock(fh)
    finally:
        fh.close()
