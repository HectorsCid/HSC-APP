"""Cliente IMAP/SMTP ligero para la bandeja de correo de HSC.

IMAP sigue siendo la fuente de verdad. Este modulo no conserva copias locales de
los mensajes: cada lectura y cada cambio se ejecutan contra el servidor.
"""
from __future__ import annotations

import base64
import html
import imaplib
import mimetypes
import os
import re
import smtplib
import ssl
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email import policy
from email.header import decode_header
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import formataddr, formatdate, getaddresses, make_msgid, parsedate_to_datetime


_CRLF = re.compile(r"[\r\n]")
_REPLY_PREFIX = re.compile(r"^\s*((re|rv|fw|fwd)\s*:\s*)+", re.IGNORECASE)


def mail_config():
    imap_username = os.getenv("IMAP_USER", os.getenv("SMTP_USER", "")).strip()
    imap_password = os.getenv("IMAP_PASSWORD", os.getenv("SMTP_PASSWORD", ""))
    smtp_username = os.getenv("SMTP_USER", imap_username).strip()
    smtp_password = os.getenv("SMTP_PASSWORD", imap_password)
    return {
        "imap_host": os.getenv("IMAP_HOST", os.getenv("SMTP_HOST", "mailc75.carrierzone.com")).strip(),
        "imap_port": _integer_env("IMAP_PORT", 993),
        "smtp_host": os.getenv("SMTP_HOST", "mailc75.carrierzone.com").strip(),
        "smtp_port": _integer_env("SMTP_PORT", 465),
        "username": imap_username,
        "password": imap_password,
        "smtp_username": smtp_username,
        "smtp_password": smtp_password,
        "from_email": os.getenv("SMTP_FROM", smtp_username).strip(),
        "from_name": os.getenv("SMTP_FROM_NAME", "HSC Refrigeracion").strip(),
        "configured": bool(imap_username and imap_password and smtp_username and smtp_password),
    }


def _integer_env(name, fallback):
    try:
        return int(os.getenv(name, str(fallback)).strip())
    except (TypeError, ValueError):
        return fallback


@contextmanager
def imap_connection(*, readonly=False, folder=None):
    cfg = mail_config()
    if not cfg["configured"]:
        raise RuntimeError("Falta configurar la cuenta de correo de HSC en Render.")
    mailbox = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"], timeout=30)
    try:
        mailbox.login(cfg["username"], cfg["password"])
        if folder is not None:
            status, _ = mailbox.select(_imap_quote(folder), readonly=readonly)
            if status != "OK":
                raise RuntimeError("El servidor no permitio abrir esa carpeta.")
        yield mailbox
    finally:
        try:
            mailbox.logout()
        except Exception:
            pass


def server_capabilities():
    cfg = mail_config()
    with imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"], timeout=15) as mailbox:
        status, values = mailbox.capability()
    capabilities = sorted({token for row in values or [] for token in row.decode("ascii", "ignore").upper().split()})
    return {"ok": status == "OK", "capabilities": capabilities, "idle": "IDLE" in capabilities}


def _imap_quote(value):
    value = str(value or "")
    if _CRLF.search(value):
        raise ValueError("Nombre de carpeta invalido.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _decode_modified_utf7(value):
    def replace(match):
        token = match.group(1)
        if token == "":
            return "&"
        raw = token.replace(",", "/")
        raw += "=" * ((4 - len(raw) % 4) % 4)
        try:
            return base64.b64decode(raw).decode("utf-16-be")
        except Exception:
            return "&" + token + "-"
    return re.sub(r"&([^-]*)-", replace, value)


def _decode_header(value):
    parts = []
    for chunk, charset in decode_header(str(value or "")):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(charset or "utf-8", "replace"))
            except (LookupError, UnicodeError):
                parts.append(chunk.decode("utf-8", "replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def _parse_list_row(raw):
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    match = re.match(r"^\((?P<flags>[^)]*)\)\s+(?P<delimiter>NIL|\"(?:\\.|[^\"])*\")\s+(?P<name>.+)$", text)
    if not match:
        return None
    raw_name = match.group("name").strip()
    if raw_name.startswith('"') and raw_name.endswith('"'):
        raw_name = raw_name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    flags = match.group("flags").split()
    role = "folder"
    upper_flags = {flag.upper() for flag in flags}
    for flag, candidate in (("\\INBOX", "inbox"), ("\\SENT", "sent"), ("\\DRAFTS", "drafts"),
                            ("\\TRASH", "trash"), ("\\JUNK", "junk"), ("\\SPAM", "junk"),
                            ("\\ARCHIVE", "archive")):
        if flag in upper_flags:
            role = candidate
            break
    if raw_name.upper() == "INBOX":
        role = "inbox"
    if role == "folder":
        lowered = _decode_modified_utf7(raw_name).casefold().replace("_", "-")
        leaf = re.split(r"[/\\.]", lowered)[-1]
        if leaf in {"sent", "sent-mail", "sent items", "enviados"}:
            role = "sent"
        elif leaf in {"drafts", "borradores"}:
            role = "drafts"
        elif leaf in {"trash", "deleted", "deleted items", "papelera"}:
            role = "trash"
        elif leaf in {"junk", "spam", "junk e-mail", "correo no deseado"}:
            role = "junk"
        elif leaf in {"archive", "archives", "archivo"}:
            role = "archive"
    return {"name": raw_name, "label": _decode_modified_utf7(raw_name), "flags": flags, "role": role,
            "selectable": "\\NOSELECT" not in upper_flags}


def list_folders():
    with imap_connection() as mailbox:
        status, rows = mailbox.list()
    if status != "OK":
        raise RuntimeError("No se pudieron consultar las carpetas del correo.")
    folders = [parsed for row in rows or [] if (parsed := _parse_list_row(row)) and parsed["selectable"]]
    priority = {"inbox": 0, "sent": 1, "drafts": 2, "archive": 3, "junk": 4, "trash": 5, "folder": 6}
    folders.sort(key=lambda row: (priority.get(row["role"], 6), row["label"].casefold()))
    return folders


def resolve_folder(requested, *, role=None):
    folders = list_folders()
    if requested:
        exact = next((row for row in folders if row["name"] == requested), None)
        if exact:
            return exact["name"]
        raise ValueError("La carpeta solicitada no existe.")
    if role:
        match = next((row for row in folders if row["role"] == role), None)
        if match:
            return match["name"]
    if role == "inbox" or not role:
        return "INBOX"
    raise ValueError(f"El servidor no anuncio una carpeta de tipo {role}.")


def _addresses(message, header):
    return [{"name": _decode_header(name), "email": address} for name, address in getaddresses(message.get_all(header, [])) if address]


def _message_date(message):
    try:
        value = parsedate_to_datetime(message.get("Date"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    except Exception:
        return ""


def _body_text(message):
    candidate = None
    if message.is_multipart():
        for part in message.walk():
            disposition = (part.get_content_disposition() or "").lower()
            if disposition == "attachment":
                continue
            if part.get_content_type() == "text/plain":
                candidate = part
                break
        if candidate is None:
            candidate = next((part for part in message.walk() if part.get_content_type() == "text/html"), None)
    else:
        candidate = message
    if candidate is None:
        return ""
    try:
        content = candidate.get_content()
    except Exception:
        payload = candidate.get_payload(decode=True) or b""
        content = payload.decode(candidate.get_content_charset() or "utf-8", "replace")
    if candidate.get_content_type() == "text/html":
        content = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", str(content))
        content = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", content)
        content = re.sub(r"(?s)<[^>]+>", "", content)
        content = html.unescape(content)
    return str(content).replace("\r\n", "\n").replace("\r", "\n").strip()


def _attachments(message):
    result = []
    for index, part in enumerate(message.walk()):
        filename = _decode_header(part.get_filename())
        if filename or part.get_content_disposition() == "attachment":
            result.append({"part": index, "filename": filename or "archivo", "content_type": part.get_content_type(),
                           "size": len(part.get_payload(decode=True) or b"")})
    return result


def _summary(uid, raw_message, flags=b"", has_attachments=None):
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    subject = _decode_header(message.get("Subject")) or "(Sin asunto)"
    normalized = _REPLY_PREFIX.sub("", subject).strip().casefold()
    return {
        "uid": str(uid), "subject": subject, "thread_key": normalized,
        "from": _addresses(message, "From"), "to": _addresses(message, "To"),
        "date": _message_date(message), "message_id": str(message.get("Message-ID") or "").strip(),
        "references": str(message.get("References") or "").strip(),
        "in_reply_to": str(message.get("In-Reply-To") or "").strip(),
        "seen": b"\\Seen" in flags, "flagged": b"\\Flagged" in flags,
        "has_attachments": bool(_attachments(message)) if has_attachments is None else bool(has_attachments),
    }


def list_messages(folder="INBOX", *, page=1, page_size=40, query=""):
    folder = resolve_folder(folder)
    page = max(1, int(page))
    page_size = min(80, max(10, int(page_size)))
    with imap_connection(readonly=True, folder=folder) as mailbox:
        criteria = "ALL"
        if str(query or "").strip():
            safe = str(query).replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")[:120]
            criteria = f'(OR SUBJECT "{safe}" FROM "{safe}")'
        status, data = mailbox.uid("search", None, criteria)
        if status != "OK":
            raise RuntimeError("No se pudo consultar la carpeta.")
        uids = (data[0] or b"").split()
        total = len(uids)
        selected = list(reversed(uids))[((page - 1) * page_size):(page * page_size)]
        items_by_uid = {}
        if selected:
            uid_set = b",".join(selected).decode("ascii")
            status, rows = mailbox.uid("fetch", uid_set, "(UID FLAGS BODYSTRUCTURE BODY.PEEK[HEADER])")
            if status == "OK":
                for row in rows or []:
                    if not isinstance(row, tuple):
                        continue
                    uid_match = re.search(rb"\bUID\s+(\d+)\b", row[0], re.IGNORECASE)
                    if not uid_match:
                        continue
                    uid = uid_match.group(1).decode("ascii")
                    metadata = row[0].upper()
                    has_attachments = b"ATTACHMENT" in metadata or b"FILENAME" in metadata
                    items_by_uid[uid] = _summary(uid, row[1], row[0], has_attachments)
        items = [items_by_uid[uid.decode("ascii")] for uid in selected if uid.decode("ascii") in items_by_uid]
    return {"folder": folder, "items": items, "page": page, "page_size": page_size, "total": total,
            "has_more": page * page_size < total}


def get_message(folder, uid, *, mark_seen=True):
    folder = resolve_folder(folder)
    command = "RFC822" if mark_seen else "BODY.PEEK[]"
    status, rows = _fetch_with_reconnect(folder, uid, f"({command} FLAGS)", readonly=not mark_seen)
    if status != "OK":
        raise LookupError("No se encontro el mensaje.")
    pair = next((row for row in rows if isinstance(row, tuple)), None)
    if not pair:
        raise LookupError("No se encontro el mensaje.")
    response_metadata = b" ".join(row[0] if isinstance(row, tuple) else row for row in rows if isinstance(row, (tuple, bytes)))
    summary = _summary(str(uid), pair[1], response_metadata)
    message = BytesParser(policy=policy.default).parsebytes(pair[1])
    return {**summary, "cc": _addresses(message, "Cc"), "reply_to": _addresses(message, "Reply-To"),
            "body": _body_text(message), "attachments": _attachments(message)}


def get_attachment(folder, uid, part_index):
    folder = resolve_folder(folder)
    status, rows = _fetch_with_reconnect(folder, uid, "(BODY.PEEK[])", readonly=True)
    pair = next((row for row in rows if isinstance(row, tuple)), None) if status == "OK" else None
    if not pair:
        raise LookupError("No se encontro el mensaje.")
    message = BytesParser(policy=policy.default).parsebytes(pair[1])
    parts = list(message.walk())
    if part_index < 0 or part_index >= len(parts):
        raise LookupError("No se encontro el archivo.")
    part = parts[part_index]
    filename = _decode_header(part.get_filename()) or "archivo"
    return filename, part.get_content_type() or mimetypes.guess_type(filename)[0] or "application/octet-stream", part.get_payload(decode=True) or b""


def _fetch_with_reconnect(folder, uid, command, *, readonly, chunk_size=256 * 1024, max_bytes=30 * 1024 * 1024):
    """Descarga el mensaje por bloques y reabre IMAP una vez ante un corte."""
    from mail_idle import resume_listener, suspend_listener
    suspend_listener()
    time.sleep(0.25)
    try:
        for attempt in range(2):
            try:
                with imap_connection(readonly=readonly, folder=folder) as mailbox:
                    section = "BODY.PEEK[]" if readonly else "BODY[]"
                    chunks = []
                    metadata = b""
                    offset = 0
                    while offset < max_bytes:
                        status, rows = mailbox.uid(
                            "fetch", str(uid), f"({section}<{offset}.{chunk_size}> FLAGS)",
                        )
                        if status != "OK":
                            return status, rows
                        pairs = [row for row in rows or [] if isinstance(row, tuple)]
                        if not pairs:
                            break
                        if not metadata:
                            metadata = pairs[0][0]
                        block = b"".join(row[1] for row in pairs if isinstance(row[1], bytes))
                        chunks.append(block)
                        if len(block) < chunk_size:
                            return "OK", [(metadata, b"".join(chunks))]
                        offset += len(block)
                    if chunks and offset >= max_bytes:
                        raise RuntimeError("El correo supera el limite de lectura de 30 MB.")
                    return "OK", []
            except (imaplib.IMAP4.abort, ConnectionError, EOFError, OSError):
                if attempt:
                    raise RuntimeError("El servidor de correo interrumpio la descarga. Intenta nuevamente.")
                time.sleep(0.25)
    finally:
        resume_listener()


def set_seen(folder, uid, seen):
    folder = resolve_folder(folder)
    with imap_connection(folder=folder) as mailbox:
        status, _ = mailbox.uid("store", str(uid), "+FLAGS" if seen else "-FLAGS", "(\\Seen)")
    return status == "OK"


def move_message(folder, uid, destination_role):
    source = resolve_folder(folder)
    destination = resolve_folder(None, role=destination_role)
    with imap_connection(folder=source) as mailbox:
        status, _ = mailbox.uid("MOVE", str(uid), _imap_quote(destination))
        if status != "OK":
            status, _ = mailbox.uid("COPY", str(uid), _imap_quote(destination))
            if status == "OK":
                mailbox.uid("store", str(uid), "+FLAGS", "(\\Deleted)")
                mailbox.expunge()
    if status != "OK":
        raise RuntimeError("El servidor no pudo mover el mensaje.")
    return destination


def send_message(*, to, subject, body, cc="", bcc="", attachments=None, in_reply_to="", references=""):
    from smtp_mailer import parse_optional_recipients, parse_recipients, _save_sent_copy

    cfg = mail_config()
    recipients = parse_recipients(to)
    cc_recipients = parse_optional_recipients(cc)
    bcc_recipients = parse_optional_recipients(bcc)
    recipient_keys = {item.casefold() for item in recipients}
    cc_recipients = [item for item in cc_recipients if item.casefold() not in recipient_keys]
    visible_keys = recipient_keys | {item.casefold() for item in cc_recipients}
    bcc_recipients = [item for item in bcc_recipients if item.casefold() not in visible_keys]
    subject = str(subject or "").strip()
    body = str(body or "").strip()
    if not subject or not body:
        raise ValueError("Escribe el asunto y el mensaje.")
    message = EmailMessage()
    message["From"] = formataddr((cfg["from_name"], cfg["from_email"]))
    message["To"] = ", ".join(recipients)
    if cc_recipients:
        message["Cc"] = ", ".join(cc_recipients)
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=cfg["from_email"].partition("@")[2] or None)
    message["Subject"] = subject.replace("\r", " ").replace("\n", " ")[:500]
    if in_reply_to and not _CRLF.search(in_reply_to):
        message["In-Reply-To"] = in_reply_to[:998]
    if references and not _CRLF.search(references):
        message["References"] = references[:4000]
    message.set_content(body)
    total_size = 0
    for upload in attachments or []:
        filename = str(getattr(upload, "filename", "") or "archivo")
        data = upload.read()
        total_size += len(data)
        if total_size > 20 * 1024 * 1024:
            raise ValueError("Los archivos superan 20 MB.")
        content_type = getattr(upload, "mimetype", "") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        maintype, subtype = content_type.split("/", 1)
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], context=context, timeout=30) as server:
        server.login(cfg["smtp_username"], cfg["smtp_password"])
        server.send_message(message, to_addrs=recipients + cc_recipients + bcc_recipients)
    smtp_cfg = {"host": cfg["imap_host"], "username": cfg["username"], "password": cfg["password"]}
    return {"message_id": str(message["Message-ID"]), "sent_copy_saved": _save_sent_copy(message, smtp_cfg)}
