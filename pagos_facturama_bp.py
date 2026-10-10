"""Complementos de pago (REP 2.0) para el proveedor fiscal activo."""
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
import copy
import xml.etree.ElementTree as ET
from pathlib import Path

from flask import Blueprint, jsonify, request
import requests

import facturacion_bp as billing
from payment_banks import payer_bank_rfc


pagos_bp = Blueprint("pagos", __name__, url_prefix="/api/pagos")


def _pick(data, *keys):
    return billing._pick(data, *keys)


def _money(value):
    try:
        result = Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if not result.is_finite():
            raise ValueError("Importe inválido")
        return result
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
    record = billing._payment_record(billing._read_index(), normalized["uuid"])
    summary = billing._payment_summary(record, normalized["total"])
    normalized.update({
        "payment_count": summary["payment_count"],
        "paid_amount": summary["paid_amount"],
        "remaining_balance": summary["remaining_balance"],
        "next_partiality_number": summary["payment_count"] + 1,
        "paid": summary["paid"],
    })
    return normalized, summary


def _replacement_records(index, rep_id):
    return [(key, p) for key, record in index.items() for p in billing._payment_entries(record)
            if str(p.get('rep_id')) == str(rep_id)]


def _replacement_payment(rep_id):
    xml = billing._decode_facturama_file(billing._fm_request('GET', f'/Cfdi/xml/issued/{rep_id}'))
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError('No se pudo leer el XML del complemento anterior.') from exc
    payments = root.findall('.//{http://www.sat.gob.mx/Pagos20}Pago')
    if len(payments) != 1 or payments[0].get('MonedaP') != 'MXN':
        raise ValueError('Este asistente admite un solo pago en MXN por complemento. Revisa este caso con tu contador.')
    return payments[0]


@pagos_bp.get('/replacement/<rep_id>')
def replacement_info(rep_id):
    try:
        original = _facturama_detail(rep_id)
        if str(_pick(original, 'CfdiType', 'Type') or '').lower() not in {'p','pago'}:
            raise ValueError('Selecciona un complemento de pago.')
        if billing._facturama_invoice_row(original)['cancellation_status'] in {'canceled','pending'}:
            raise ValueError('Primero consulta y resuelve la cancelación del complemento anterior.')
        billing._sync_facturama_payment_rows([original])
        index = billing._read_index()
        records = _replacement_records(index, rep_id)
        if not records:
            raise ValueError('No se encontraron los pagos activos de este complemento. Consulta su estado primero.')
        if not billing._rep_cancellation_state(rep_id)['is_latest']:
            raise ValueError('Hay parcialidades posteriores: primero resuelve el complemento más reciente.')
        pending = next((p for record in index.values() for p in billing._stored_payment_entries(record)
                        if str(p.get('replaces_id')) == rep_id and p.get('status') == 'replacement_pending'), None)
        if pending:
            return jsonify(ok=False, error='Ya existe un sustituto. No lo emitas otra vez; consulta la cancelación del anterior.', replacement_id=pending['rep_id'], replacement_uuid=pending['rep_uuid']), 409
        documents = []
        for uuid, payment in records:
            raw = billing._fm_request('GET', '/api/cfdi', params={'type':'issued','status':'all','page':0,'uuid':uuid}, timeout=35)
            match = next((row for row in _facturama_rows(raw) if _facturama_uuid(row).lower()==uuid.lower()), None)
            if not match:
                raise ValueError(f'No se encontró la factura {uuid}.')
            normalized = _complete_customer(_facturama_detail(str(_pick(match,'Id'))))
            normalized.update(remaining_balance=round(float(payment['amount'])+float(payment['remaining_balance']),2), next_partiality_number=payment['partiality_number'])
            documents.append(dict(invoice=normalized, amount=payment['amount']))
        pago = _replacement_payment(rep_id)
        return jsonify(ok=True, id=rep_id, uuid=_facturama_uuid(original), documents=documents,
            date=pago.get('FechaPago'), payment_form=pago.get('FormaDePagoP'), reference=pago.get('NumOperacion',''), payer_bank_rfc=pago.get('RfcEmisorCtaOrd','')), 200
    except ValueError as exc:
        return jsonify(ok=False,error=str(exc)),400
    except requests.HTTPError as exc:
        return jsonify(ok=False,error=billing._http_error_detail(exc)),400


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
    if str(_pick(invoice, 'Currency') or 'MXN').upper() != 'MXN':
        raise ValueError('Por ahora sólo se admiten facturas y pagos en MXN.')
    if billing._invoice_cancellation_state(normalized['status']) != 'active':
        raise ValueError('No se puede pagar una factura cancelada o en proceso de cancelación.')
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
    bank_rfc = payer_bank_rfc(body.get('payer_bank_rfc'), payment['PaymentForm'])
    if bank_rfc:
        payment['RfcIssuerPayerAccount'] = bank_rfc

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
    if body.get('replacement_id') and 'documents' not in body:
        return jsonify(ok=False,error='La sustitución requiere seleccionar las facturas relacionadas.'),400
    if 'documents' in body:
        return _create_multiple_payment(body)
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
        if any(p.get('status') == 'replacement_pending' for p in billing._stored_payment_entries(billing._payment_record(index, normalized['uuid']))):
            return jsonify(ok=False,error='Esta factura tiene una sustitución por resolver.'),409
        summary = billing._payment_summary(billing._payment_record(index, normalized["uuid"]), normalized["total"])
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
        payment_key = billing._payment_index_key(index, normalized["uuid"])
        prior = billing._payment_entries(index.get(payment_key))
        index[payment_key] = {
            "status": "active", "payments": [*prior, payment_entry],
            "paid_amount": round(summary["paid_amount"] + payment_entry["amount"], 2),
            "remaining_balance": float(unpaid),
        }
        billing._write_index(index)
        billing.notices.publish("Pago y complemento registrados",
                                f"Factura {normalized['folio'] or normalized['uuid']}: pago de ${payment_entry['amount']:,.2f}; saldo ${float(unpaid):,.2f}.",
                                category="pagos", key=f"payment-{rep_uuid}", url="/facturacion")
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


def _create_multiple_payment(body):
    """Valida todo el lote antes de solicitar un único timbrado al PAC."""
    if billing._provider() != 'facturama':
        return jsonify(ok=False, error='Los complementos requieren Facturama.'), 503
    try:
        documents = body.get('documents')
        if not isinstance(documents, list) or not 1 <= len(documents) <= 50:
            raise ValueError('Selecciona entre 1 y 50 facturas.')
        index = copy.deepcopy(billing._read_index())
        replacement_id = str(body.get('replacement_id') or '').strip()
        originals = []
        original_uuid = ''
        if replacement_id:
            if any(p.get('status') == 'replacement_pending' and str(p.get('replaces_id')) == replacement_id
                   for record in index.values() for p in billing._stored_payment_entries(record)):
                raise ValueError('Ya existe un sustituto. Consulta la cancelación del anterior; no vuelvas a emitirlo.')
            original = _facturama_detail(replacement_id)
            original_uuid = _facturama_uuid(original)
            if str(_pick(original, 'CfdiType','Type') or '').lower() not in {'p','pago'} or not billing._valid_cfdi_uuid(original_uuid):
                raise ValueError('El documento a sustituir no es un complemento válido.')
            if billing._facturama_invoice_row(original)['cancellation_status'] in {'canceled','pending'}:
                raise ValueError('El complemento anterior está cancelado o tiene una cancelación pendiente. Consulta su estado antes de continuar.')
            _replacement_payment(replacement_id)
            originals = _replacement_records(index, replacement_id)
            if not originals or not billing._rep_cancellation_state(replacement_id)['is_latest']:
                raise ValueError('No se puede sustituir: faltan pagos registrados o existen parcialidades posteriores.')
            for key, old in originals:
                index[key]['payments'] = [p for p in billing._stored_payment_entries(index[key]) if str(p.get('rep_id')) != replacement_id]
                remaining = billing._payment_entries(index[key])
                if not remaining:
                    index.pop(key)
        prepared, seen_ids, seen_uuids = [], set(), set()
        cfdi = None
        for item in documents:
            if not isinstance(item, dict):
                raise ValueError('Selección de facturas inválida.')
            invoice_id = str(item.get('invoice_id') or '').strip()
            if not invoice_id or invoice_id in seen_ids:
                raise ValueError('Hay una factura vacía o repetida.')
            seen_ids.add(invoice_id)
            invoice = _facturama_detail(invoice_id)
            normalized = _complete_customer(invoice)
            uuid = normalized['uuid'].lower()
            if uuid in seen_uuids:
                raise ValueError('Una factura no puede aparecer dos veces en el complemento.')
            seen_uuids.add(uuid)
            summary = billing._payment_summary(billing._payment_record(index, normalized['uuid']), normalized['total'])
            if replacement_id:
                old = next((old for key,old in originals if key.casefold()==normalized['uuid'].casefold()), None)
                if not old:
                    raise ValueError('La sustitución debe conservar las mismas facturas.')
                previous = _money(old['amount']) + _money(old['remaining_balance'])
                if _money(summary['remaining_balance']) != previous or summary['payment_count'] + 1 != int(old['partiality_number']):
                    raise ValueError('El historial de parcialidades está incompleto. Actualiza los complementos anteriores antes de sustituirlo.')
            if any(p.get('status') == 'replacement_pending' for p in billing._stored_payment_entries(billing._payment_record(index, normalized['uuid']))):
                raise ValueError('Esta factura tiene una sustitución por resolver. Consulta la cancelación del complemento anterior.')
            if summary['paid'] or summary['remaining_balance'] <= 0:
                raise ValueError(f"La factura {normalized['folio'] or normalized['uuid']} ya está pagada.")
            part_body = dict(body, amount=item.get('amount'), previous_balance=summary['remaining_balance'], partiality_number=summary['payment_count'] + 1)
            part, normalized, unpaid = _build_facturama_payment(invoice, part_body)
            if cfdi is None:
                cfdi = part
            else:
                if part['Receiver']['Rfc'].strip().upper() != cfdi['Receiver']['Rfc'].strip().upper():
                    raise ValueError('Todas las facturas deben pertenecer al mismo RFC receptor.')
                cfdi['Complemento']['Payments'][0]['RelatedDocuments'].extend(part['Complemento']['Payments'][0]['RelatedDocuments'])
            prepared.append((normalized, summary, part_body, unpaid))
        payment = cfdi['Complemento']['Payments'][0]
        payment['Amount'] = float(sum((_money(item['amount']) for item in documents), Decimal('0')))
        if replacement_id:
            if seen_uuids != {str(uuid).lower() for uuid,_ in originals}:
                raise ValueError('La sustitución debe conservar las mismas facturas. Para cambiar la relación de facturas, revisa el caso antes de timbrar.')
            cfdi['Relations'] = {'Type':'04', 'Cfdis':[{'Uuid':original_uuid}]}
        result = billing._fm_request('POST', '/3/cfdis', json_body=cfdi, timeout=75)
        rep_id, rep_uuid = str(_pick(result, 'Id') or ''), _facturama_uuid(result)
        if not rep_id or not billing._valid_cfdi_uuid(rep_uuid):
            return jsonify(ok=False, stage='stamp_payment', error='Facturama no confirmó el complemento. Revisa el listado antes de reintentar.'), 502
        applied = []
        for normalized, summary, part_body, unpaid in prepared:
            entry = dict(rep_id=rep_id, rep_uuid=rep_uuid, status='active', amount=float(_money(part_body['amount'])), remaining_balance=float(unpaid), partiality_number=part_body['partiality_number'], date=payment['Date'])
            key = billing._payment_index_key(index, normalized['uuid'])
            if replacement_id:
                entry.update(status='replacement_pending', replaces_id=replacement_id, replaces_uuid=original_uuid)
                old = next(old for uuid,old in originals if uuid.lower()==normalized['uuid'].lower())
                index[key] = dict(status='active',payments=[*billing._stored_payment_entries(index.get(key)),old,entry])
                applied.append(dict(invoice_id=normalized['id'], uuid=normalized['uuid'], folio=normalized['folio'], **entry))
                continue
            index[key] = dict(status='active', payments=[*billing._payment_entries(index.get(key)), entry], paid_amount=round(summary['paid_amount'] + entry['amount'], 2), remaining_balance=float(unpaid))
            applied.append(dict(invoice_id=normalized['id'], uuid=normalized['uuid'], folio=normalized['folio'], **entry))
        warnings = []
        try:
            billing._write_index(index)
        except Exception:
            warnings.append('El complemento ya se timbró, pero no se confirmó la actualización de saldos. No lo vuelvas a emitir; revisa su UUID en Facturama.')
        first = prepared[0][0]
        folder = (first['folio'] or first['uuid']) if len(prepared) == 1 else f'Complemento-{rep_uuid}'
        try:
            backup = billing._backup_facturama_cfdi(rep_id, rep_uuid, first['customer'].get('folder_name') or first['customer']['legal_name'], folder, 'Complemento-Pago')
        except Exception:
            backup = {'ok':False}
        try:
            billing.notices.publish('Pago y complemento registrados', f"{len(prepared)} factura(s): pago de ${payment['Amount']:,.2f}.", category='pagos', key=f'payment-{rep_uuid}', url='/facturacion')
        except Exception:
            pass  # Un aviso fallido no convierte un timbrado confirmado en un rechazo.
        response = dict(ok=True, provider='facturama', id=rep_id, uuid=rep_uuid, documents=applied, amount=payment['Amount'], drive_backup=backup, pdf_url=f'/api/invoices/{rep_id}/pdf', xml_url=f'/api/invoices/{rep_id}/xml')
        if not backup.get('ok'):
            warnings.append('Complemento timbrado; Drive no confirmó el respaldo.')
        if warnings:
            response['warning'] = ' '.join(warnings)
        if replacement_id:
            response.update(replaces_id=replacement_id, replaces_uuid=original_uuid,
                next_action='Solicita la cancelación del anterior con motivo 01 y el UUID del nuevo. El pago no se contará dos veces.')
        return jsonify(response), 200
    except ValueError as exc:
        return jsonify(ok=False, stage='validation', error=str(exc)), 400
    except requests.HTTPError as exc:
        return jsonify(ok=False, stage='facturama', error=billing._http_error_detail(exc)), 400
