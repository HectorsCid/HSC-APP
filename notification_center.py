"""Avisos operativos de HSC, independientes de la entrega push.

La aplicación administrativa comparte la bandeja (no es la bandeja de Partner).
El archivo se reemplaza atómicamente y se respalda en Drive. Un fallo de aviso
nunca convierte un timbrado correcto en un error que invite a repetirlo.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4

from cfdi_drive import backup_json_file, load_json_file

PATH = Path("data/centro_avisos.json")
DRIVE_NAME = "HSC-centro-avisos.json"
LOCK = RLock()
LOG = logging.getLogger(__name__)


def _read():
    if PATH.exists():
        return json.loads(PATH.read_text(encoding="utf-8"))
    value = load_json_file(DRIVE_NAME, default={}) or {}
    if not isinstance(value, dict):
        raise ValueError("El centro de avisos tiene un formato inválido")
    _write_local(value)
    return value


def _write_local(state):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = PATH.with_name(PATH.name + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(PATH)


def _write(state):
    _write_local(state)
    try:
        backup_json_file(DRIVE_NAME, state)
        return True
    except Exception:
        LOG.warning("Centro de avisos guardado localmente; respaldo en Drive pendiente", exc_info=True)
        return False


def publish(title, body, *, category="sistema", key=None, url="/inicio-app", level="info"):
    """Idempotencia por evento; volver a consultar no crea avisos duplicados."""
    try:
        with LOCK:
            state = _read()
            items = state.setdefault("items", [])
            if key and any(row.get("key") == key for row in items):
                return False
            items.insert(0, {
                "id": str(uuid4()), "key": key, "title": str(title)[:160],
                "body": str(body)[:800], "category": category, "level": level,
                "url": url if url.startswith("/") and not url.startswith("//") else "/inicio-app",
                "created_at": datetime.now(timezone.utc).isoformat(), "read": False,
            })
            # Retención acotada por cantidad, sin prometer un plazo de conservación.
            state["items"] = items[:2000]
            _write(state)
            return True
    except Exception:
        LOG.exception("No se pudo registrar el aviso operativo")
        return False


def snapshot():
    with LOCK:
        state = _read()
        items = state.get("items", [])
        return {"items": items, "unread": sum(not row.get("read") for row in items),
                "jobs": state.get("jobs", {})}


def mark_read(item_id):
    with LOCK:
        state = _read()
        found = False
        for row in state.get("items", []):
            if row["id"] == item_id:
                row["read"] = True
                found = True
        return found, _write(state) if found else True


def record_job(name, status, **details):
    try:
        with LOCK:
            state = _read()
            state.setdefault("jobs", {})[name] = {
                "status": status, "checked_at": datetime.now(timezone.utc).isoformat(), **details,
            }
            _write(state)
    except Exception:
        LOG.exception("No se pudo registrar el estado de %s", name)
