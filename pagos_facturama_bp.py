"""Complementos de pago (REP 2.0) para el proveedor fiscal activo."""
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from pathlib import Path

from flask import Blueprint, jsonify, request
import requests

import facturacion_bp as billing


pagos_bp = Blueprint("pagos", __name__, url_prefix="/api/pagos")


def _pick(data, *keys):
    return billing._pick(data, *keys)


def _money(value):
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Importe inválido") from exc


def _method_code(value):
    return str(value or "").strip().upper()[:3]


def _facturama_rows(raw):
    return raw if isinstance(raw, list) else (_pick(raw, "Data", "Items") or [])


def _facturama_detail(invoice_id):
    return billing._fm_request(
        "GET", f"/api/cfdi/{invoice_id}", params={"type": "issued"}, timeout=35
    )


def _facturama_uuid(invoice):
    uuid = _pick(invoice, "Uuid", "FolioFiscal")
    if not uuid:
        complement = _pick(invoice, "Complement", "Complemento") or {}
        uuid = _pick(_pick(complement, "TaxStamp") or {}, "Uuid")
    return str(uuid or "").strip()


def _normalize_facturama_invoice(invoice):
    receiver = _pick(invoice, "Receiver", "Customer") or {}
    return {
        "id": str(_pick(invoice, "Id") or ""),
        "uuid": _facturama_uuid(invoice),
        "date": _pick(invoice, "Date"),
        "status": _pick(invoice, "Status") or (
            "active" if _pick(invoice, "IsActive") is not False else "canceled"
        ),
        "payment_method": _method_code(_pick(invoice, "PaymentMethod")),
        "total": float(_money(_pick(invoice, "Total"))),
        "customer": {
            "legal_name": _pick(receiver, "Name", "LegalName", "TaxName")
                or _pick(invoice, "TaxName") or "",
            "tax_id": _pick(receiver, "Rfc", "TaxId") or _pick(invoice, "Rfc") or "",
            "tax_system": str(_pick(receiver, "FiscalRegime", "TaxSystem") or ""),
            "zip": str(_pick(receiver, "TaxZipCode", "ZipCode") or ""),
        },
        "serie": str(_pick(invoice, "Serie") or ""),
        "folio": str(_pick(invoice, "Folio") or ""),
    }


def _local_customer_fiscal_data(rfc, name=""):
    """Recupera CP y régimen del catálogo HSC cuando el detalle fiscal los omite."""
    wanted_rfc = str(rfc or "").strip().upper()
    for path in (Path("clientes.json"), Path("data/clientes.json")):
        try:
            raw = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        rows = raw.items() if isinstance(raw, dict) else enumerate(raw if isinstance(raw, list) else [])
        for key, value in rows:
            value = value if isinstance(value, dict) else {}
            row_rfc = str(value.get("rfc") or "").strip().upper()
            row_name = str(value.get("razon_social") or value.get("nombre") or key).strip()
            if (wanted_rfc and row_rfc == wanted_rfc) or (not wanted_rfc and row_name == name):
                return {
                    "folder_name": str(key),
                    "tax_system": str(value.get("regimen_fiscal") or "").split(" ", 1)[0],
                    "zip": str(value.get("cp") or value.get("codigo_postal") or ""),
                }
    return {}


def _complete_customer(invoice):
    normalized = _normalize_facturama_invoice(invoice)
    customer = normalized["customer"]
    local = _local_customer_fiscal_data(customer["tax_id"], customer["legal_name"])
    customer["tax_system"] = customer["tax_system"].split(" ", 1)[0] or local.get("tax_system", "")
    customer["zip"] = customer["zip"] or local.get("zip", "")
    customer["folder_name"] = local.get("folder_name") or customer["legal_name"]
    return normalized


def _invoice_payment_state(invoice):
    normalized = _complete_customer(invoice)
    record = billing._read_index().get(normalized["uuid"])
    summary = billing._payment_summary(record, normalized["total"])
    normalized.update({
        "payment_count": summary["payment_count"],
        "paid_amount": summary["paid_amount"],
        "remaining_balance": summary["remaining_balance"],
        "next_partiality_number": summary["payment_count"] + 1,
        "paid": summary["paid"],
    })
    return normalized, summary


def _scaled_taxes(invoice, amount, previous_balance):
    """Resume los impuestos originales y los prorratea para el pago recibido."""
    grouped = {}
    has_tax_object = False
    for item in _pick(invoice, "Items") or []:
        if str(_pick(item, "TaxObject", "ObjetoImp") or "") == "02":
            has_tax_object = True
        for tax in _pick(item, "Taxes") or []:
            name = str(_pick(tax, "Name") or "").upper().replace(" RET", "")
            if name not in {"IVA", "ISR", "IEPS"}:
                continue
            rate = Decimal(str(_pick(tax, "Rate") or 0))
            if rate > 1:
                rate /= 100
            retention = bool(_pick(tax, "IsRetention"))
            key = (name, rate, retention)
            bucket = grouped.setdefault(key, [Decimal("0"), Decimal("0")])
            tax_total = Decimal(str(_pick(tax, "Total") or 0))
            tax_base = Decimal(str(_pick(tax, "Base") or 0))
            bucket[0] += tax_base or (tax_total / rate if rate else Decimal("0"))
            bucket[1] += tax_total

    # El detalle de API Web normalmente resume los impuestos a nivel CFDI.
    if not grouped:
        for tax in _pick(invoice, "Taxes") or []:
            name = str(_pick(tax, "Name") or "").upper().replace(" RET", "")
            if name not in {"IVA", "ISR", "IEPS"}:
                continue
            rate = Decimal(str(_pick(tax, "Rate") or 0))
            if rate > 1:
                rate /= 100
            tax_total = Decimal(str(_pick(tax, "Total") or 0))
            tax_type = str(_pick(tax, "Type") or "").lower()
            retention = bool(_pick(tax, "IsRetention")) or tax_type in {
                "retained", "withheld", "retenido", "retencion", "retención"
            }
            grouped[(name, rate, retention)] = [
                tax_total / rate if rate else Decimal("0"), tax_total
            ]

    if not grouped:
        return ("02" if has_tax_object else "01"), []

    invoice_total = _money(_pick(invoice, "Total"))
    denominator = invoice_total or previous_balance
    ratio = min(Decimal("1"), amount / denominator) if denominator else Decimal("1")
    taxes = []
    for (name, rate, retention), (base, total) in grouped.items():
        taxes.append({
            "Name": name,
            "Rate": float(rate),
            "Base": float((base * ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "Total": float((total * ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "IsRetention": retention,
        })
    return "02", taxes


def _build_facturama_payment(invoice, body):
    normalized = _complete_customer(invoice)
    if normalized["payment_method"] != "PPD":
        raise ValueError("La factura origen no es PPD; no requiere complemento de pago")
    if not billing._valid_cfdi_uuid(normalized["uuid"]):
        raise ValueError("La factura no tiene un folio fiscal válido")

    amount = _money(body.get("amount"))
    previous_balance = _money(body.get("previous_balance") or normalized["total"])
    if amount <= 0:
        raise ValueError("El importe pagado debe ser mayor que cero")
    if previous_balance <= 0 or amount > previous_balance:
        raise ValueError("El pago no puede ser mayor que el saldo anterior")
    try:
        partiality = int(body.get("partiality_number") or 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("El número de parcialidad es inválido") from exc
    if partiality < 1:
        raise ValueError("El número de parcialidad debe ser mayor que cero")

    customer = normalized["customer"]
    customer["tax_system"] = str(body.get("tax_system") or customer["tax_system"]).split(" ", 1)[0]
    customer["zip"] = str(body.get("tax_zip") or customer["zip"])
    if not all((customer["legal_name"], customer["tax_id"], customer["tax_system"], customer["zip"])):
        raise ValueError("Facturama no devolvió los datos fiscales completos del receptor")
    expedition, _ = billing._facturama_issuer_locations()
    tax_object, taxes = _scaled_taxes(invoice, amount, previous_balance)
    unpaid = (previous_balance - amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    related = {
        "TaxObject": tax_object,
        "Uuid": normalized["uuid"],
        "Currency": "MXN",
        "PaymentMethod": "PPD",
        "PartialityNumber": partiality,
        "PreviousBalanceAmount": float(previous_balance),
        "AmountPaid": float(amount),
        "ImpSaldoInsoluto": float(unpaid),
    }
    if normalized["serie"]:
        related["Serie"] = normalized["serie"]
    if normalized["folio"]:
        related["Folio"] = normalized["folio"]
    if taxes:
        related["Taxes"] = taxes

    payment = {
        "Date": str(body.get("date") or datetime.now().isoformat(timespec="seconds")),
        "PaymentForm": str(body.get("payment_form") or "03").zfill(2),
        "Currency": "MXN",
        "Amount": float(amount),
        "RelatedDocuments": [related],
    }
    if body.get("reference"):
        payment["OperationNumber"] = str(body["reference"]).strip()[:100]

    return {
        "CfdiType": "P",
        "NameId": "14",
        "ExpeditionPlace": expedition,
        "Receiver": {
            "Rfc": customer["tax_id"],
            "Name": customer["legal_name"],
            "CfdiUse": "CP01",
            "FiscalRegime": customer["tax_system"],
            "TaxZipCode": customer["zip"],
        },
        "Complemento": {"Payments": [payment]},
    }, normalized, unpaid


@pagos_bp.get("/resolve")
def resolve_uuid():
    value = (request.args.get("uuid") or "").strip()
    if not value:
        return jsonify({"ok": False, "error": "UUID requerido"}), 400
    try:
        if billing._provider() == "facturama":
            raw = billing._fm_request("GET", "/api/cfdi", params={
                "type": "issued", "status": "all", "page": 0, "uuid": value,
            }, timeout=35)
            rows = _facturama_rows(raw)
            match = next((row for row in rows if _facturama_uuid(row).lower() == value.lower()), None)
            if not match:
                return jsonify({"ok": False, "error": "UUID no encontrado en Facturama"}), 404
            return jsonify({"ok": True, "id": _pick(match, "Id")}), 200
        rs = billing._fa_get("/invoices", params={"q": value})
        data = rs.get("data") or []
        if not data:
            return jsonify({"ok": False, "error": "UUID no encontrado"}), 404
        return jsonify({"ok": True, "id": data[0]["id"]}), 200
    except requests.HTTPError as exc:
        return jsonify({"ok": False, "error": billing._http_error_detail(exc)}), 400


@pagos_bp.get("/info/<invoice_id>")
def info(invoice_id):
    try:
        if billing._provider() == "facturama":
            normalized, _ = _invoice_payment_state(_facturama_detail(invoice_id))
            return jsonify(normalized), 200
        inv = billing._fa_get(f"/invoices/{invoice_id}")
        customer = inv.get("customer") or {}
        return jsonify({
            "id": inv.get("id"), "uuid": inv.get("uuid"), "date": inv.get("date"),
            "status": inv.get("status"), "payment_method": inv.get("payment_method"),
            "total": inv.get("total"),
            "customer": {"legal_name": customer.get("legal_name"), "tax_id": customer.get("tax_id")},
        }), 200
    except requests.HTTPError as exc:
        return jsonify({"ok": False, "error": billing._http_error_detail(exc)}), 400


@pagos_bp.post("/crear")
def crear_pago():
    body = request.get_json(silent=True) or {}
    invoice_id = str(body.get("invoice_id") or "").strip()
    if not invoice_id:
        return jsonify({"ok": False, "error": "Selecciona una factura"}), 400
    if billing._provider() != "facturama":
        return jsonify({
            "ok": False,
            "error": "Los complementos están habilitados para Facturama; falta configurar ese proveedor",
        }), 503

    try:
        invoice = _facturama_detail(invoice_id)
        index = billing._read_index()
        normalized = _complete_customer(invoice)
        summary = billing._payment_summary(index.get(normalized["uuid"]), normalized["total"])
        if summary["paid"] or summary["remaining_balance"] <= 0:
            return jsonify({"ok": False, "stage": "validation", "field": "amount",
                            "error": "Esta factura ya está totalmente pagada."}), 409
        # El servidor manda: el navegador no puede alterar saldo ni parcialidad.
        body["previous_balance"] = summary["remaining_balance"]
        body["partiality_number"] = summary["payment_count"] + 1
        cfdi, normalized, unpaid = _build_facturama_payment(invoice, body)
        result = billing._fm_request("POST", "/3/cfdis", json_body=cfdi, timeout=75)
        rep_id = str(_pick(result, "Id") or "")
        rep_uuid = _facturama_uuid(result)
        if not rep_id or not billing._valid_cfdi_uuid(rep_uuid):
            return jsonify({
                "ok": False, "stage": "stamp_payment",
                "error": "Facturama no confirmó un folio fiscal válido para el complemento",
            }), 502
        payment_entry = {
            "rep_id": rep_id, "rep_uuid": rep_uuid, "status": "active",
            "amount": float(_money(body.get("amount"))), "remaining_balance": float(unpaid),
            "partiality_number": body["partiality_number"],
            "date": str(body.get("date") or datetime.now().isoformat(timespec="seconds")),
        }
        prior = billing._payment_entries(index.get(normalized["uuid"]))
        index[normalized["uuid"]] = {
            "status": "active", "payments": [*prior, payment_entry],
            "paid_amount": round(summary["paid_amount"] + payment_entry["amount"], 2),
            "remaining_balance": float(unpaid),
        }
        billing._write_index(index)
        folio_folder = normalized["folio"] or normalized["uuid"]
        drive_backup = billing._backup_facturama_cfdi(
            rep_id, rep_uuid, normalized["customer"].get("folder_name") or normalized["customer"]["legal_name"],
            folio_folder, f"Complemento-Pago-P{body['partiality_number']}",
        )
        response = {
            "ok": True, "provider": "facturama", "id": rep_id, "uuid": rep_uuid,
            "remaining_balance": float(unpaid),
            "partiality_number": body["partiality_number"],
            "pdf_url": f"/api/invoices/{rep_id}/pdf",
            "xml_url": f"/api/invoices/{rep_id}/xml",
            "drive_backup": drive_backup,
        }
        if not drive_backup.get("ok"):
            response["warning"] = (
                "El complemento se timbró correctamente, pero Drive no confirmó el respaldo."
            )
        return jsonify(response), 200
    except ValueError as exc:
        message = str(exc)
        lowered = message.lower()
        field = "amount" if any(word in lowered for word in ("importe", "pago", "saldo")) else ""
        return jsonify({"ok": False, "stage": "validation", "field": field, "error": message}), 400
    except requests.HTTPError as exc:
        return jsonify({
            "ok": False, "provider": "facturama", "stage": "facturama",
            "error": billing._http_error_detail(exc),
        }), 400
