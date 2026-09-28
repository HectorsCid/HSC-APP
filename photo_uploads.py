"""Renew only a live server-side photo attempt; late attempts are fenced by SQL."""
from contextlib import contextmanager
import logging
import threading


@contextmanager
def evidence_lease(store, report_id, mutation_id, token, interval=20):
    stop = threading.Event()

    def renew():
        while not stop.wait(interval):
            try:
                if not store.renew_report_evidence(report_id, mutation_id, token):
                    return
            except Exception:
                logging.getLogger(__name__).exception('No se pudo renovar el intento de fotografía')
                return  # Completion must prove this token still owns an unexpired lease.

    worker = threading.Thread(target=renew, name='photo-lease', daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=1)
