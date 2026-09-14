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


def publish(title, body, *, category="sistema", key=None, url="/inicio-app", level="info", audience="admin", client_id="", user_id="", exclude_user_id=""):
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
                "audience": audience, "client_id": client_id, "user_id": user_id, "exclude_user_id": exclude_user_id,
            })
            # Retención acotada por cantidad, sin prometer un plazo de conservación.
            state["items"] = items[:2000]
            _write(state)
            return True
    except Exception:
        LOG.exception("No se pudo registrar el aviso operativo")
        return False


def visible(row, role="admin", client_id="", user_id=""):
    return (row.get("audience", "admin") == role
            and user_id not in row.get('deleted_by', [])
            and (not row.get('exclude_user_id') or row['exclude_user_id'] != user_id)
            and (not row.get("client_id") or row.get("client_id") == client_id)
            and (not row.get("user_id") or row.get("user_id") == user_id))


def snapshot(role="admin", client_id="", user_id=""):
    with LOCK:
        state = _read()
        items = [dict(row, read=(user_id in row['read_by'] if 'read_by' in row else bool(row.get('read')))) for row in state.get("items", []) if visible(row, role, client_id, user_id)]
        return {"items": items, "unread": sum(not row.get("read") for row in items),
                "jobs": state.get("jobs", {}) if role == "admin" else {}}


def mark_read(item_id, role="admin", client_id="", user_id=""):
    with LOCK:
        state = _read()
        found = False
        for row in state.get("items", []):
            if row["id"] == item_id and visible(row, role, client_id, user_id):
                readers=row.setdefault('read_by', [])
                if user_id not in readers:
                    readers.append(user_id)
                found = True
        return found, _write(state) if found else True


def delete_for_user(item_ids, role='admin', client_id='', user_id=''):
    with LOCK:
        state=_read()
        changed=0
        for row in state.get('items',[]):
            if row['id'] in item_ids and visible(row,role,client_id,user_id):
                row.setdefault('deleted_by',[]).append(user_id)
                for job in state.get('push_queue',[]):
                    if job.get('tag')==row.get('key'):
                        for target in job.get('targets',[]):
                            if target.get('user_id')==user_id and target.get('status')=='pending':
                                target.update(status='cancelled',error='Aviso eliminado por esta cuenta.')
                changed+=1
        if changed:
            _write(state)
        return changed


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
