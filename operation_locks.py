"""Cross-process resource locks; PostgreSQL in production, OS locks for SQLite."""
from contextlib import contextmanager
import hashlib
import os
import time


@contextmanager
def resource_lock(store, resource, timeout=35):
    digest = hashlib.sha256(resource.encode('utf-8')).digest()
    deadline = time.monotonic() + timeout
    if store.dialect == 'postgres':
        # Session locks survive commits used to persist a remote-operation intent.
        # PostgreSQL releases them when the owning connection/process disappears.
        key = int.from_bytes(digest[:8], 'big', signed=True)
        with store.connection() as conn:
            while not conn.execute('SELECT pg_try_advisory_lock(%s)', (key,)).fetchone()[0]:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Otra operación sigue en curso. Vuelve a intentar.')
                time.sleep(.05)
            try:
                yield
            finally:
                conn.execute('SELECT pg_advisory_unlock(%s)', (key,))
        return
    folder = store.local_path.parent / (store.local_path.name + '.locks')
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / digest.hex(), 'a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        while True:
            try:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Otra operación sigue en curso. Vuelve a intentar.')
                time.sleep(.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
