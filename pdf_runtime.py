"""Coordinacion y aislamiento de memoria para todos los PDF de la aplicacion.

Los motores de PDF cargan bibliotecas nativas y fuentes que no siempre devuelven
memoria al proceso de Gunicorn. Por eso el servidor web no importa WeasyPrint ni
ReportLab: cada documento se genera en un proceso corto que desaparece al
terminar. El candado conserva una sola impresion a la vez en la instancia.
"""

from contextlib import contextmanager
import ctypes
import gc
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time


class PdfRendererBusy(RuntimeError):
    """Ya existe otro PDF consumiendo el renderizador de la instancia."""


class PdfRenderError(RuntimeError):
    """El proceso aislado no pudo producir un PDF valido."""


_PDF_RENDER_LOCK = threading.RLock()
_MEMORY_LOG = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parent
_WEASY_WORKER = _ROOT / "pdf_worker.py"
_INVOICE_WORKER = _ROOT / "factura_pdf_hsc.py"


def memory_snapshot():
    """Incluye el contenedor completo, no solo el worker de Gunicorn."""
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
    _MEMORY_LOG.warning(
        "HSC_MEMORY operation=%s phase=%s rss_mb=%s container_mb=%s limit_mb=%s",
        operation, phase, snapshot["rss_mb"], snapshot["container_mb"], snapshot["limit_mb"],
    )


def rss_megabytes():
    """Memoria residente actual; devuelve None donde no este disponible."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except Exception:
        return None
    return None


def release_pdf_memory():
    """Libera objetos Python y devuelve paginas libres a glibc en Render/Linux."""
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
        raise PdfRendererBusy("Hay otra foto o PDF procesandose; reintenta en unos segundos.")
    try:
        yield
    finally:
        _PDF_RENDER_LOCK.release()


def _worker_environment():
    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("MALLOC_ARENA_MAX", "2")
    return env


def _pdf_timeout():
    try:
        return max(15.0, float(os.getenv("PDF_RENDER_TIMEOUT_SECONDS", "105")))
    except (TypeError, ValueError):
        return 105.0


def _run_pdf_worker(command, *, operation):
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_pdf_timeout(), env=_worker_environment(), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfRenderError("La impresion tardo demasiado y se cancelo sin afectar la aplicacion.") from exc
    except OSError as exc:
        raise PdfRenderError("No se pudo iniciar el motor de impresion.") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        safe_detail = detail[-1][:500] if detail else f"codigo {result.returncode}"
        _MEMORY_LOG.error("HSC_PDF_WORKER operation=%s failed=%s", operation, safe_detail)
        raise PdfRenderError(f"No se pudo generar el PDF ({safe_detail}).")


def _validate_pdf(path):
    try:
        with open(path, "rb") as source:
            header = source.read(5)
            source.seek(0, os.SEEK_END)
            size = source.tell()
    except OSError as exc:
        raise PdfRenderError("El motor termino sin entregar el archivo PDF.") from exc
    if header != b"%PDF-" or size < 100:
        raise PdfRenderError("El motor entrego un archivo PDF incompleto.")


def _finish_log(started, before, operation="pdf"):
    release_pdf_memory()
    log_memory_event(operation, "end")
    after = rss_megabytes()
    try:
        print(
            f"{operation} aislado renderizado en {time.monotonic() - started:.1f}s; "
            f"memoria_web={before if before is not None else '?'}->"
            f"{after if after is not None else '?'} MB",
            flush=True,
        )
    except Exception:
        pass


def _render_html_to_temp(html, output_path, *, base_url):
    html_path = Path(output_path).with_suffix(".html")
    html_path.write_text(str(html), encoding="utf-8")
    command = [
        sys.executable, str(_WEASY_WORKER), "--html", str(html_path), "--output",
        str(output_path), "--app-root", str(_ROOT),
    ]
    if base_url:
        command.extend(["--base-url", str(base_url)])
    _run_pdf_worker(command, operation="html")
    _validate_pdf(output_path)


def render_pdf_bytes(html, *, base_url=None, wait_timeout=5):
    started = time.monotonic()
    before = rss_megabytes()
    with pdf_render_slot(wait_timeout=wait_timeout):
        release_pdf_memory()
        log_memory_event("pdf", "start")
        try:
            with tempfile.TemporaryDirectory(prefix="hsc-pdf-") as temp_dir:
                output_path = Path(temp_dir) / "document.pdf"
                _render_html_to_temp(html, output_path, base_url=base_url)
                return output_path.read_bytes()
        finally:
            _finish_log(started, before)


def render_pdf_file(html, destination, *, base_url=None, wait_timeout=5):
    started = time.monotonic()
    before = rss_megabytes()
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with pdf_render_slot(wait_timeout=wait_timeout):
        release_pdf_memory()
        log_memory_event("pdf", "start")
        try:
            # El temporal vive junto al destino para que os.replace sea atomico
            # incluso cuando /tmp y el proyecto estan en montajes distintos.
            with tempfile.TemporaryDirectory(prefix=".hsc-pdf-", dir=destination.parent) as temp_dir:
                output_path = Path(temp_dir) / "document.pdf"
                _render_html_to_temp(html, output_path, base_url=base_url)
                os.replace(output_path, destination)
        finally:
            _finish_log(started, before)


def render_invoice_pdf_bytes(
    xml_bytes, *, internal_folio="", order_number="", quote_folio="", wait_timeout=10,
):
    """Genera la representacion HSC con ReportLab fuera del proceso web."""
    started = time.monotonic()
    before = rss_megabytes()
    with pdf_render_slot(wait_timeout=wait_timeout):
        release_pdf_memory()
        log_memory_event("invoice_pdf", "start")
        try:
            with tempfile.TemporaryDirectory(prefix="hsc-invoice-") as temp_dir:
                temp_dir = Path(temp_dir)
                xml_path = temp_dir / "invoice.xml"
                output_path = temp_dir / "invoice.pdf"
                xml_path.write_bytes(bytes(xml_bytes))
                command = [
                    sys.executable, str(_INVOICE_WORKER), str(xml_path), str(output_path),
                    "--folio-interno", str(internal_folio or ""),
                    "--orden-compra", str(order_number or ""),
                    "--folio-cotizacion", str(quote_folio or ""),
                ]
                _run_pdf_worker(command, operation="invoice")
                _validate_pdf(output_path)
                return output_path.read_bytes()
        finally:
            _finish_log(started, before, operation="invoice_pdf")
