"""Coordinación y liberación de memoria para todos los PDF de la aplicación."""

from contextlib import contextmanager
import ctypes
import gc
import os
import threading
import time

from weasyprint import HTML


class PdfRendererBusy(RuntimeError):
    """Ya existe otro PDF consumiendo el renderizador de la instancia."""


_PDF_RENDER_LOCK = threading.RLock()


def rss_megabytes():
    """Memoria residente actual; devuelve None donde no esté disponible."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except Exception:
        return None
    return None


def release_pdf_memory():
    """Libera objetos Python y devuelve páginas libres a glibc en Render/Linux."""
    gc.collect()
    if os.name != "posix":
        return
    try:
        libc = ctypes.CDLL("libc.so.6")
        malloc_trim = getattr(libc, "malloc_trim", None)
        if malloc_trim is not None:
            malloc_trim.argtypes = [ctypes.c_size_t]
            malloc_trim.restype = ctypes.c_int
            malloc_trim(0)
    except Exception:
        pass


@contextmanager
def pdf_render_slot(wait_timeout=5):
    """Garantiza que WeasyPrint nunca se ejecute dos veces simultáneamente."""
    acquired = _PDF_RENDER_LOCK.acquire(timeout=max(0.0, float(wait_timeout)))
    if not acquired:
        raise PdfRendererBusy("Hay otro PDF procesándose en este momento")
    try:
        yield
    finally:
        _PDF_RENDER_LOCK.release()


def render_pdf_bytes(html, *, base_url=None, wait_timeout=5):
    started = time.monotonic()
    before = rss_megabytes()
    with pdf_render_slot(wait_timeout=wait_timeout):
        try:
            return HTML(string=html, base_url=base_url).write_pdf()
        finally:
            release_pdf_memory()
            after = rss_megabytes()
            try:
                print(
                    "PDF renderizado "
                    f"en {time.monotonic() - started:.1f}s; "
                    f"memoria={before if before is not None else '?'}->"
                    f"{after if after is not None else '?'} MB",
                    flush=True,
                )
            except Exception:
                pass


def render_pdf_file(html, destination, *, base_url=None, wait_timeout=5):
    started = time.monotonic()
    before = rss_megabytes()
    with pdf_render_slot(wait_timeout=wait_timeout):
        try:
            HTML(string=html, base_url=base_url).write_pdf(destination)
        finally:
            release_pdf_memory()
            after = rss_megabytes()
            try:
                print(
                    "PDF renderizado "
                    f"en {time.monotonic() - started:.1f}s; "
                    f"memoria={before if before is not None else '?'}->"
                    f"{after if after is not None else '?'} MB",
                    flush=True,
                )
            except Exception:
                pass
