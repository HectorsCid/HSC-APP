"""Listener IMAP IDLE para avisos inmediatos de correo nuevo."""
from __future__ import annotations

import imaplib
import logging
import os
import socket
import threading
import time
from datetime import datetime, timezone

import mail_client


LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()
_STARTED = False
_STATE = {
    "enabled": False,
    "running": False,
    "connected": False,
    "last_uid": 0,
    "last_event_at": "",
    "last_connected_at": "",
    "last_error": "",
}


def _enabled():
    default = "1" if os.getenv("RENDER") else "0"
    return os.getenv("MAIL_IDLE_ENABLED", default).strip().lower() in {"1", "true", "yes", "on"}


def listener_status():
    with _LOCK:
        return dict(_STATE)


def _update(**values):
    with _LOCK:
        _STATE.update(values)


def _latest_uid(mailbox):
    status, data = mailbox.uid("search", None, "ALL")
    if status != "OK" or not data or not data[0]:
        return 0
    values = [int(value) for value in data[0].split() if value.isdigit()]
    return max(values, default=0)


def _new_uids(mailbox, last_uid):
    status, data = mailbox.uid("search", None, f"UID {max(1, int(last_uid) + 1)}:*")
    if status != "OK" or not data or not data[0]:
        return []
    return [int(value) for value in data[0].split() if value.isdigit() and int(value) > int(last_uid)]


def _idle_once(mailbox, timeout=240):
    """Espera un cambio sin depender del helper IDLE añadido en Python 3.14."""
    tag = mailbox._new_tag()  # imaplib no ofrece API publica para IDLE en Python 3.11.
    mailbox.send(tag + b" IDLE\r\n")
    continuation = mailbox.readline()
    if not continuation.startswith(b"+"):
        raise imaplib.IMAP4.error(f"El servidor rechazo IDLE: {continuation!r}")
    changed = False
    previous_timeout = mailbox.sock.gettimeout()
    mailbox.sock.settimeout(max(10, int(timeout)))
    try:
        try:
            line = mailbox.readline()
            if not line:
                raise ConnectionError("El servidor IMAP cerro la conexion IDLE.")
            upper = line.upper()
            changed = b" EXISTS" in upper or b" RECENT" in upper
        except (TimeoutError, socket.timeout):
            changed = False
    finally:
        mailbox.send(b"DONE\r\n")
        mailbox.sock.settimeout(previous_timeout)
        while True:
            response = mailbox.readline()
            if not response or response.startswith(tag):
                break
        mailbox.tagged_commands.pop(tag, None)
    return changed


def _notify_new_message(app, uid):
    try:
        message = mail_client.get_message("INBOX", str(uid), mark_seen=False)
        sender = message.get("from") or []
        sender_name = (sender[0].get("name") or sender[0].get("email")) if sender else "Nuevo remitente"
        subject = message.get("subject") or "(Sin asunto)"
        with app.app_context():
            from facturacion_bp import _send_push_notifications
            _send_push_notifications(
                f"Correo de {sender_name}", subject,
                url=f"/correo?folder=INBOX&uid={uid}", tag=f"hsc-mail-{uid}",
            )
        _update(last_event_at=datetime.now(timezone.utc).isoformat(), last_error="")
    except Exception as exc:
        LOG.exception("No se pudo preparar el aviso del correo UID %s", uid)
        _update(last_error=str(exc)[:300])


def _run(app):
    backoff = 5
    last_uid = 0
    _update(enabled=True, running=True)
    while True:
        cfg = mail_client.mail_config()
        if not cfg["configured"]:
            _update(connected=False, last_error="Faltan credenciales IMAP.")
            time.sleep(60)
            continue
        mailbox = None
        try:
            mailbox = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"], timeout=30)
            mailbox.login(cfg["username"], cfg["password"])
            status, _ = mailbox.select(mail_client._imap_quote("INBOX"), readonly=True)
            if status != "OK":
                raise RuntimeError("No se pudo vigilar la bandeja de entrada.")
            current_uid = _latest_uid(mailbox)
            if last_uid == 0:
                last_uid = current_uid
            _update(
                connected=True, last_uid=last_uid, last_error="",
                last_connected_at=datetime.now(timezone.utc).isoformat(),
            )
            backoff = 5
            while True:
                if _idle_once(mailbox):
                    fresh = _new_uids(mailbox, last_uid)
                    for uid in fresh[-10:]:
                        _notify_new_message(app, uid)
                    if fresh:
                        last_uid = max(fresh)
                        _update(last_uid=last_uid)
        except Exception as exc:
            LOG.warning("Listener IMAP desconectado; se reintentara: %s", exc)
            _update(connected=False, last_error=str(exc)[:300])
            time.sleep(backoff)
            backoff = min(120, backoff * 2)
        finally:
            if mailbox is not None:
                try:
                    mailbox.logout()
                except Exception:
                    pass


def start_mail_idle_listener(app):
    global _STARTED
    enabled = _enabled()
    _update(enabled=enabled)
    if not enabled:
        return False
    with _LOCK:
        if _STARTED:
            return False
        _STARTED = True
    thread = threading.Thread(target=_run, args=(app,), daemon=True, name="hsc-mail-imap-idle")
    thread.start()
    return True
