"""Rutas web de la bandeja IMAP de HSC."""
from io import BytesIO

from flask import Blueprint, jsonify, render_template, request, send_file

import mail_client


mail_bp = Blueprint("mail", __name__)


def _error(exc, status=502):
    return jsonify({"ok": False, "error": str(exc)}), status


@mail_bp.get("/correo")
def inbox_page():
    return render_template("correo.html")


@mail_bp.get("/api/correo/status")
def status():
    cfg = mail_client.mail_config()
    if not cfg["configured"]:
        return jsonify({"ok": False, "configured": False, "error": "Falta configurar la cuenta de correo."}), 503
    try:
        capability = mail_client.server_capabilities()
        from mail_idle import listener_status
        return jsonify({"ok": True, "configured": True, "account": cfg["from_email"],
                        "listener": listener_status(), **capability})
    except Exception as exc:
        return _error(exc)


@mail_bp.get("/api/correo/folders")
def folders():
    try:
        return jsonify({"ok": True, "folders": mail_client.list_folders()})
    except Exception as exc:
        return _error(exc)


@mail_bp.get("/api/correo/messages")
def messages():
    try:
        result = mail_client.list_messages(
            request.args.get("folder", "INBOX"), page=request.args.get("page", 1),
            page_size=request.args.get("page_size", 40), query=request.args.get("q", ""),
        )
        return jsonify({"ok": True, **result})
    except (TypeError, ValueError) as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc)


@mail_bp.get("/api/correo/message/<uid>")
def message(uid):
    try:
        return jsonify({"ok": True, "message": mail_client.get_message(request.args.get("folder", "INBOX"), uid)})
    except (LookupError, ValueError) as exc:
        return _error(exc, 404)
    except Exception as exc:
        return _error(exc)


@mail_bp.get("/api/correo/message/<uid>/attachment/<int:part_index>")
def attachment(uid, part_index):
    try:
        filename, content_type, data = mail_client.get_attachment(request.args.get("folder", "INBOX"), uid, part_index)
        return send_file(BytesIO(data), mimetype=content_type, as_attachment=True, download_name=filename, max_age=0)
    except (LookupError, ValueError) as exc:
        return _error(exc, 404)
    except Exception as exc:
        return _error(exc)


@mail_bp.post("/api/correo/message/<uid>/seen")
def seen(uid):
    body = request.get_json(silent=True) or {}
    try:
        ok = mail_client.set_seen(body.get("folder", "INBOX"), uid, bool(body.get("seen", True)))
        return jsonify({"ok": ok})
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc)


@mail_bp.post("/api/correo/message/<uid>/move")
def move(uid):
    body = request.get_json(silent=True) or {}
    role = str(body.get("destination") or "").lower()
    if role not in {"trash", "junk", "archive"}:
        return _error(ValueError("Destino no permitido."), 400)
    try:
        destination = mail_client.move_message(body.get("folder", "INBOX"), uid, role)
        return jsonify({"ok": True, "destination": destination})
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc)


@mail_bp.post("/api/correo/send")
def send():
    uploads = [item for item in request.files.getlist("attachments") if item and item.filename]
    if sum(item.content_length or 0 for item in uploads) > 20 * 1024 * 1024:
        return _error(ValueError("Los archivos superan 20 MB."), 413)
    try:
        result = mail_client.send_message(
            to=request.form.get("to", ""), cc=request.form.get("cc", ""), bcc=request.form.get("bcc", ""),
            subject=request.form.get("subject", ""), body=request.form.get("body", ""), attachments=uploads,
            in_reply_to=request.form.get("in_reply_to", ""), references=request.form.get("references", ""),
        )
        return jsonify({"ok": True, **result})
    except ValueError as exc:
        return _error(exc, 400)
    except Exception as exc:
        return _error(exc)
