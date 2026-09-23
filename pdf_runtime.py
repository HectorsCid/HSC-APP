"""Coordinación y liberación de memoria para todos los PDF de la aplicación."""

from contextlib import contextmanager
import ctypes
import gc
import logging
import os
import threading
import time

from weasyprint import HTML


class PdfRendererBusy(RuntimeError):
    """Ya existe otro PDF consumiendo el renderizador de la instancia."""


_PDF_RENDER_LOCK = threading.RLock()
_MEMORY_LOG = logging.getLogger(__name__)


def memory_snapshot():
    """Incluye el contenedor completo, no sólo el worker de Gunicorn."""
    result = {"rss_mb": rss_megabytes(), "container_mb": None, "limit_mb": None}
    for used_path, limit_path in (
        ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.max"),
        ("/sys/fs/cgroup/memory/memory.usage_in_bytes", "/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        try:
            with open(used_path, encoding="ascii") as source:
                used = int(source.read().strip())
            with open(limit_path, encoding="ascii") as source:
                limit = source.read().strip()
            result["container_mb"] = round(used / (1024 * 1024), 1)
            if limit != "max" and 0 < int(limit) < 2 ** 60:
                result["limit_mb"] = round(int(limit) / (1024 * 1024), 1)
            break
        except (OSError, ValueError):
            continue
    return result


def log_memory_event(operation, phase):
    snapshot = memory_snapshot()
    # No se registran fotos, clientes, IDs ni contenido de documentos.
    _MEMORY_LOG.warning("HSC_MEMORY operation=%s phase=%s rss_mb=%s container_mb=%s limit_mb=%s",
                        operation, phase, snapshot["rss_mb"], snapshot["container_mb"], snapshot["limit_mb"])


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
    """Comparte turno entre PDF y fotos; es reentrante al preparar un reporte."""
    acquired = _PDF_RENDER_LOCK.acquire(timeout=max(0.0, float(wait_timeout)))
    if not acquired:
        raise PdfRendererBusy("Hay otra foto o PDF procesándose; reintenta en unos segundos.")
    try:
        yield
    finally:
        _PDF_RENDER_LOCK.release()


def render_pdf_bytes(html, *, base_url=None, wait_timeout=5):
    started = time.monotonic()
    before = rss_megabytes()
    with pdf_render_slot(wait_timeout=wait_timeout):
        log_memory_event("pdf", "start")
        try:
            return HTML(string=html, base_url=base_url).write_pdf()
        finally:
            release_pdf_memory()
            log_memory_event("pdf", "end")
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
        log_memory_event("pdf", "start")
        try:
            HTML(string=html, base_url=base_url).write_pdf(destination)
        finally:
            release_pdf_memory()
            log_memory_event("pdf", "end")
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
