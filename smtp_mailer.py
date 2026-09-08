import os
import re
import smtplib
import ssl
import hmac
import hashlib
import mimetypes
from email.message import EmailMessage
from email.utils import formataddr, parseaddr


_HEADER_BREAKS = re.compile(r"[\r\n]+")


def smtp_config():
    host = os.getenv("SMTP_HOST", "mailc75.carrierzone.com").strip()
    try:
        port = int(os.getenv("SMTP_PORT", "465").strip())
    except ValueError:
        port = 465
    username = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    from_email = os.getenv("SMTP_FROM", username).strip()
    from_name = os.getenv("SMTP_FROM_NAME", "HSC Refrigeración").strip()
    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "from_email": from_email,
        "from_name": from_name,
        "configured": bool(host and port and username and password and from_email),
        "send_key": os.getenv("SMTP_SEND_KEY", ""),
    }


def trusted_device_token():
    expected = smtp_config()["send_key"]
    if len(expected) < 12:
        return ""
    return hmac.new(expected.encode("utf-8"), b"hsc-mail-device-v1", hashlib.sha256).hexdigest()


def authorized_to_send(candidate="", trusted_token=""):
    expected = smtp_config()["send_key"]
    supplied = str(candidate or "")
    token = trusted_device_token()
    return len(expected) >= 12 and (
        hmac.compare_digest(supplied, expected)
        or bool(token and hmac.compare_digest(str(trusted_token or ""), token))
    )


def valid_email(value):
    value = str(value or "").strip()
    _, address = parseaddr(value)
    return bool(address and address == value and "@" in address and "\n" not in value and "\r" not in value)


def parse_recipients(value):
    parts = [item.strip() for item in re.split(r"[,;\n]+", str(value or "")) if item.strip()]
    if not parts:
        raise ValueError("Escribe al menos un correo electrónico.")
    if len(parts) > 10:
        raise ValueError("Puedes enviar a un máximo de 10 destinatarios a la vez.")
    invalid = [item for item in parts if not valid_email(item)]
    if invalid:
        raise ValueError(f"Escribe un correo electrónico válido. Revisa: {invalid[0]}")
    # Conserva el orden y evita mandar dos veces a la misma dirección.
    return list(dict.fromkeys(parts))


def _safe_header(value, fallback):
    cleaned = _HEADER_BREAKS.sub(" ", str(value or "")).strip()
    return cleaned[:240] or fallback


def send_email_with_attachments(*, recipient, subject, body, attachments):
    cfg = smtp_config()
    if not cfg["configured"]:
        raise RuntimeError("Falta configurar el correo de salida de HSC en Render.")
    recipients = parse_recipients(recipient)
    if not str(subject or "").strip():
        raise ValueError("Escribe el asunto del correo.")
    if not str(body or "").strip():
        raise ValueError("Escribe el mensaje del correo.")
    if not attachments:
        raise ValueError("No hay archivos preparados para enviar.")

    message = EmailMessage()
    message["From"] = formataddr((cfg["from_name"], cfg["from_email"]))
    message["To"] = ", ".join(recipients)
    message["Subject"] = _safe_header(subject, "Documento de HSC Refrigeración")
    message.set_content(str(body).strip())
    for attachment in attachments:
        data = attachment.get("data") or b""
        filename = str(attachment.get("filename") or "archivo").strip()
        if not data:
            raise ValueError(f"El archivo {filename} está vacío.")
        content_type = str(attachment.get("content_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream")
        maintype, _, subtype = content_type.partition("/")
        message.add_attachment(
            data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=filename,
        )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=context, timeout=30) as server:
        server.login(cfg["username"], cfg["password"])
        server.send_message(message)
    return {
        "recipient": ", ".join(recipients),
        "recipients": recipients,
        "from": cfg["from_email"],
        "attachments": len(attachments),
    }


def send_cfdi_email(*, recipient, subject, body, pdf_bytes, xml_bytes, folio, extra_attachments=None):
    if not pdf_bytes or not xml_bytes:
        raise ValueError("No se pudieron preparar el PDF y XML de la factura.")

    safe_folio = re.sub(r"[^A-Za-z0-9._-]+", "-", str(folio or "CFDI")).strip("-.") or "CFDI"
    attachments = [
        {"data": pdf_bytes, "content_type": "application/pdf", "filename": f"Factura-{safe_folio}.pdf"},
        {"data": xml_bytes, "content_type": "application/xml", "filename": f"Factura-{safe_folio}.xml"},
    ]
    attachments.extend(extra_attachments or [])
    return send_email_with_attachments(
        recipient=recipient,
        subject=subject or f"Factura HSC {safe_folio}",
        body=body or (
            "Buen día, estimado cliente. Envío la factura solicitada.\n\n"
            "De antemano muchas gracias.\n\n"
            "Quedo a sus órdenes.\n\n"
            "Ing. Héctor Silva Cid\n\n"
            "Cel: 5527605496"
        ),
        attachments=attachments,
    )


def send_quote_email(*, recipient, subject, body, pdf_bytes, folio, extra_attachments=None):
    if not pdf_bytes:
        raise ValueError("No se pudo preparar el PDF de la cotización.")
    safe_folio = re.sub(r"[^A-Za-z0-9._-]+", "-", str(folio or "Cotizacion")).strip("-.") or "Cotizacion"
    attachments = [{
        "data": pdf_bytes,
        "content_type": "application/pdf",
        "filename": f"Cotizacion-{safe_folio}.pdf",
    }]
    attachments.extend(extra_attachments or [])
    return send_email_with_attachments(
        recipient=recipient,
        subject=subject,
        body=body,
        attachments=attachments,
    )
