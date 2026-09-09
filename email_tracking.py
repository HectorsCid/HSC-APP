from datetime import datetime
from pathlib import Path
from threading import Lock
import json

from cfdi_drive import backup_json_file, load_json_file


DATA_FILE = Path("data") / "envios_correo.json"
DRIVE_FILE = "HSC-envios-correo.json"
_LOCK = Lock()


def _key(document_type, document_id):
    return f"{str(document_type or '').strip().lower()}:{str(document_id or '').strip()}"


def read_email_deliveries(refresh=False):
    with _LOCK:
        if not refresh and DATA_FILE.exists():
            try:
                value = json.loads(DATA_FILE.read_text("utf-8"))
                if isinstance(value, dict):
                    return value
            except (OSError, ValueError):
                pass
        try:
            value = load_json_file(DRIVE_FILE, default={})
            value = value if isinstance(value, dict) else {}
            DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            DATA_FILE.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            return value
        except Exception:
            return {}


def delivery_status(document_type, document_id, deliveries=None):
    deliveries = deliveries if isinstance(deliveries, dict) else read_email_deliveries()
    record = deliveries.get(_key(document_type, document_id)) or {}
    return {
        "sent": bool(record.get("sent_count")),
        "sent_count": int(record.get("sent_count") or 0),
        "last_sent_at": str(record.get("last_sent_at") or ""),
        "recipients": list(record.get("recipients") or []),
        "cc": list(record.get("cc") or []),
    }


def record_email_delivery(document_type, document_id, *, recipients=None, cc=None, client_name="", folio=""):
    with _LOCK:
        if DATA_FILE.exists():
            try:
                deliveries = json.loads(DATA_FILE.read_text("utf-8"))
            except (OSError, ValueError):
                deliveries = {}
        else:
            try:
                deliveries = load_json_file(DRIVE_FILE, default={})
            except Exception:
                deliveries = {}
        deliveries = deliveries if isinstance(deliveries, dict) else {}
        key = _key(document_type, document_id)
        prior = deliveries.get(key) or {}
        deliveries[key] = {
            "document_type": str(document_type or "").strip().lower(),
            "document_id": str(document_id or "").strip(),
            "folio": str(folio or "").strip(),
            "client_name": str(client_name or "").strip(),
            "sent_count": int(prior.get("sent_count") or 0) + 1,
            "last_sent_at": datetime.now().isoformat(timespec="seconds"),
            "recipients": list(recipients or []),
            "cc": list(cc or []),
        }
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(json.dumps(deliveries, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            backup_json_file(DRIVE_FILE, deliveries)
        except Exception:
            pass
        return delivery_status(document_type, document_id, deliveries)
