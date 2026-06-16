"""
Single-instance lock. Stops a second copy of the app from running at once, using
a lock file (msvcrt on Windows, fcntl elsewhere). Never blocks launch on error.
"""

import atexit
import os


def acquire(lock_file):
    """Try to take the single-instance lock.

    Returns True if we got it (or if the check itself could not run), and False if
    another instance already holds it.
    """
    try:
        handle = open(lock_file, "w")
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                handle.close()
                return False
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                handle.close()
                return False
        handle.write(str(os.getpid()))
        handle.flush()

        def cleanup():
            try:
                handle.close()
                if os.path.exists(lock_file):
                    os.remove(lock_file)
            except OSError:
                pass

        atexit.register(cleanup)
        return True
    except Exception as exc:  # never block launch on lock failure
        print(f"Single-instance check failed: {exc}")
        return True
