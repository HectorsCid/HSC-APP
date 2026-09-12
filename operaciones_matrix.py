"""Lectura segura de la Hoja Matriz para la vista previa de Operaciones."""

from __future__ import annotations

import re


MATRIX_RANGES = ["Clientes!A1:H", "Equipos!A1:N", "Reportes!A1:AL"]


def _text(value):
    return str(value or "").strip()


def _truthy(value):
    return _text(value).casefold() in {"1", "true", "verdadero", "si", "sí", "x", "realizado"}


def _round_number(value):
    match = re.search(r"(?:^|\D)([1-4])(?:\D|$)", _text(value))
    return match.group(1) if match else ""


def _records(rows):
    if not rows:
        return []
    headers = [_text(value) for value in rows[0]]
    records = []
    for raw in rows[1:]:
        values = list(raw) + [""] * max(0, len(headers) - len(raw))
        record = {header: values[index] for index, header in enumerate(headers) if header}
        if any(_text(value) for value in raw):
            records.append(record)
    return records


def build_operaciones_bootstrap(value_ranges):
    """Convierte batchGet de Sheets en datos mínimos para la app móvil."""
    by_title = {}
    for item in value_ranges or []:
        title = _text(item.get("range")).split("!", 1)[0].strip("'")
        by_title[title] = _records(item.get("values") or [])

    clients = []
    duplicate_clients = 0
    seen_clients = set()
    for row in by_title.get("Clientes", []):
        client_id = _text(row.get("ID_Cliente"))
        if not client_id:
            continue
        if client_id in seen_clients:
            duplicate_clients += 1
            continue
        seen_clients.add(client_id)
        photo = _text(row.get("Foto"))
        clients.append({
            "id": client_id,
            "name": _text(row.get("NombreCliente")) or client_id,
            "address": _text(row.get("Direccion")),
            "selected_round": _round_number(row.get("RondaSeleccionadaCliente")),
            "has_photo": bool(photo),
        })

    equipment = []
    duplicate_equipment = 0
    seen_equipment = set()
    for row in by_title.get("Equipos", []):
        equipment_id = _text(row.get("ID_Equipo"))
        client_id = _text(row.get("ID_Cliente"))
        if not equipment_id or not client_id:
            continue
        if equipment_id in seen_equipment:
            duplicate_equipment += 1
            continue
        seen_equipment.add(equipment_id)
        photo = _text(row.get("Foto"))
        equipment.append({
            "id": equipment_id,
            "client_id": client_id,
            "name": _text(row.get("NombreEquipo")) or equipment_id,
            "brand": _text(row.get("Marca")),
            "model": _text(row.get("Modelo")),
            "serial": _text(row.get("NoSerie")),
            "status": _text(row.get("Estatus")),
            "location": _text(row.get("Ubicacion")),
            "department": _text(row.get("Departamento")),
            "has_photo": bool(photo),
        })

    reports = []
    duplicate_reports = 0
    seen_reports = set()
    evidence_count = 0
    for row in by_title.get("Reportes", []):
        report_id = _text(row.get("ID_Reporte"))
        equipment_id = _text(row.get("ID_Equipo"))
        client_id = _text(row.get("ID_Cliente"))
        if not report_id:
            continue
        if report_id in seen_reports:
            duplicate_reports += 1
            continue
        seen_reports.add(report_id)
        photo_count = sum(bool(_text(row.get(f"Foto{index}"))) for index in range(1, 7))
        evidence_count += photo_count
        reports.append({
            "id": report_id,
            "equipment_id": equipment_id,
            "client_id": client_id,
            "round": _round_number(row.get("Ronda")),
            "start": _text(row.get("FechaInicio")),
            "end": _text(row.get("FechaFin")),
            "completed": _truthy(row.get("Realizado")),
            "photo_count": photo_count,
        })

    client_ids = {item["id"] for item in clients}
    equipment_ids = {item["id"] for item in equipment}
    return {
        "clients": clients,
        "equipment": equipment,
        "reports": reports,
        "stats": {
            "clients": len(clients),
            "equipment": len(equipment),
            "reports": len(reports),
            "client_photos": sum(item["has_photo"] for item in clients),
            "equipment_photos": sum(item["has_photo"] for item in equipment),
            "evidence_photos": evidence_count,
            "orphan_equipment": sum(item["client_id"] not in client_ids for item in equipment),
            "orphan_reports": sum(
                bool(item["equipment_id"]) and item["equipment_id"] not in equipment_ids
                for item in reports
            ),
            "duplicate_clients": duplicate_clients,
            "duplicate_equipment": duplicate_equipment,
            "duplicate_reports": duplicate_reports,
        },
    }


def read_operaciones_matrix(service, spreadsheet_id):
    response = service.spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id,
        ranges=MATRIX_RANGES,
        majorDimension="ROWS",
    ).execute()
    return build_operaciones_bootstrap(response.get("valueRanges") or [])
