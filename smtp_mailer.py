import os
import re
import smtplib
import ssl
import hmac
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


def authorized_to_send(candidate):
    expected = smtp_config()["send_key"]
    supplied = str(candidate or "")
    return len(expected) >= 12 and hmac.compare_digest(supplied, expected)


def valid_email(value):
    value = str(value or "").strip()
    _, address = parseaddr(value)
    return bool(address and address == value and "@" in address and "\n" not in value and "\r" not in value)


def _safe_header(value, fallback):
    cleaned = _HEADER_BREAKS.sub(" ", str(value or "")).strip()
    return cleaned[:240] or fallback


def send_cfdi_email(*, recipient, subject, body, pdf_bytes, xml_bytes, folio):
    cfg = smtp_config()
    if not cfg["configured"]:
        raise RuntimeError("Falta configurar el correo de salida de HSC en Render.")
    recipient = str(recipient or "").strip()
    if not valid_email(recipient):
        raise ValueError("Escribe un correo electrónico válido.")
    if not pdf_bytes or not xml_bytes:
        raise ValueError("No se pudieron preparar el PDF y XML de la factura.")

    safe_folio = re.sub(r"[^A-Za-z0-9._-]+", "-", str(folio or "CFDI")).strip("-.") or "CFDI"
    message = EmailMessage()
    message["From"] = formataddr((cfg["from_name"], cfg["from_email"]))
    message["To"] = recipient
    message["Subject"] = _safe_header(subject, f"Factura {safe_folio} — HSC Refrigeración")
    message.set_content(str(body or "Adjuntamos su comprobante fiscal en formatos PDF y XML.\n\nHSC Refrigeración"))
    message.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=f"Factura-{safe_folio}.pdf")
    message.add_attachment(xml_bytes, maintype="application", subtype="xml", filename=f"Factura-{safe_folio}.xml")

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=context, timeout=30) as server:
        server.login(cfg["username"], cfg["password"])
        server.send_message(message)
    return {"recipient": recipient, "from": cfg["from_email"]}
