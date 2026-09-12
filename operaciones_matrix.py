"""Lectura segura de la Hoja Matriz para la vista previa de Operaciones."""

from __future__ import annotations

import re
import unicodedata


# Se leen columnas abiertas para no truncar datos nuevos agregados a la matriz.
# Sheets sólo devuelve las celdas realmente ocupadas, no las 702 columnas completas.
MATRIX_RANGES = ["Clientes!A1:ZZ", "Equipos!A1:ZZ", "Reportes!A1:ZZ"]


def _text(value):
    return str(value or "").strip()


def _truthy(value):
    return _text(value).casefold() in {"1", "true", "verdadero", "si", "sí", "x", "realizado"}


def _round_number(value):
    match = re.search(r"(?:^|\D)([1-4])(?:\D|$)", _text(value))
    return match.group(1) if match else ""


def _header_key(value):
    value = unicodedata.normalize("NFKD", _text(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _pick(row, *names):
    """Acepta tanto los encabezados originales de AppSheet como los nuevos."""
    for name in names:
        if name in row and _text(row.get(name)):
            return row.get(name)
    normalized = {_header_key(key): value for key, value in row.items()}
    for name in names:
        value = normalized.get(_header_key(name))
        if _text(value):
            return value
    return ""


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


def build_operaciones_bootstrap(value_ranges, *, include_media_refs=False, include_raw=False):
    """Convierte batchGet de Sheets en datos mínimos para la app móvil."""
    by_title = {}
    for item in value_ranges or []:
        title = _text(item.get("range")).split("!", 1)[0].strip("'")
        by_title[title] = _records(item.get("values") or [])

    clients = []
    duplicate_clients = 0
    seen_clients = set()
    for row in by_title.get("Clientes", []):
        client_id = _text(_pick(row, "ID_Cliente", "ID Cliente"))
        if not client_id:
            continue
        if client_id in seen_clients:
            duplicate_clients += 1
            continue
        seen_clients.add(client_id)
        photo = _text(_pick(row, "Foto", "Foto Cliente"))
        client = {
            "id": client_id,
            "name": _text(_pick(row, "NombreCliente", "Nombre de Cliente", "Nombre del cliente")) or client_id,
            "address": _text(_pick(row, "Direccion", "Dirección")),
            "selected_round": _round_number(_pick(row, "RondaSeleccionadaCliente", "Ronda seleccionada")),
            "has_photo": bool(photo),
        }
        if include_media_refs and photo:
            client["_photo_ref"] = photo
        if include_raw:
            client["_raw"] = dict(row)
        clients.append(client)

    equipment = []
    duplicate_equipment = 0
    seen_equipment = set()
    for row in by_title.get("Equipos", []):
        equipment_id = _text(_pick(row, "ID_Equipo", "ID Equipo"))
        client_id = _text(_pick(row, "ID_Cliente", "ID Cliente"))
        if not equipment_id or not client_id:
            continue
        if equipment_id in seen_equipment:
            duplicate_equipment += 1
            continue
        seen_equipment.add(equipment_id)
        photo = _text(_pick(row, "Foto", "Foto Equipo"))
        item = {
            "id": equipment_id,
            "client_id": client_id,
            "name": _text(_pick(row, "NombreEquipo", "Nombre de Equipo", "Nombre del equipo")) or equipment_id,
            "brand": _text(_pick(row, "Marca")),
            "model": _text(_pick(row, "Modelo")),
            "serial": _text(_pick(row, "NoSerie", "No. Serie", "Número de serie")),
            "status": _text(_pick(row, "Estatus", "Estado")),
            "location": _text(_pick(row, "Ubicacion", "Ubicación")),
            "department": _text(_pick(row, "Departamento")),
            "has_photo": bool(photo),
        }
        if include_media_refs and photo:
            item["_photo_ref"] = photo
        if include_raw:
            item["_raw"] = dict(row)
        equipment.append(item)

    reports = []
    duplicate_reports = 0
    seen_reports = set()
    evidence_count = 0
    for row in by_title.get("Reportes", []):
        report_id = _text(_pick(row, "ID_Reporte", "ID Reporte"))
        equipment_id = _text(_pick(row, "ID_Equipo", "ID Equipo"))
        client_id = _text(_pick(row, "ID_Cliente", "ID Cliente"))
        if not report_id:
            continue
        if report_id in seen_reports:
            duplicate_reports += 1
            continue
        seen_reports.add(report_id)
        evidence_refs = [_text(_pick(row, f"Foto{index}", f"Foto {index}")) for index in range(1, 7)]
        photo_count = sum(bool(value) for value in evidence_refs)
        evidence_count += photo_count
        report = {
            "id": report_id,
            "equipment_id": equipment_id,
            "client_id": client_id,
            "round": _round_number(_pick(row, "Ronda", "Periodo", "Período") or report_id),
            "start": _text(_pick(row, "FechaInicio", "FECHA DE INICIO", "Fecha de inicio")),
            "end": _text(_pick(row, "FechaFin", "FECHA DE TERMINACIÓN", "Fecha de terminación")),
            "completed": _truthy(_pick(row, "Realizado", "Completado", "Terminado")),
            "photo_count": photo_count,
        }
        if include_media_refs:
            report["_evidence_refs"] = evidence_refs
        if include_raw:
            report["_raw"] = dict(row)
        reports.append(report)

    equipment_client = {item["id"]: item["client_id"] for item in equipment}
    report_links = {item["id"]: item for item in reports}
    faults = []
    duplicate_faults = 0
    seen_faults = set()
    fault_rows = []
    fault_sheet_count = 0
    for title, rows in by_title.items():
        if "falla" in _header_key(title):
            fault_sheet_count += 1
            fault_rows.extend(rows)
    for position, row in enumerate(fault_rows, start=1):
        equipment_id = _text(_pick(row, "ID_Equipo", "ID Equipo", "Equipo"))
        report_id = _text(_pick(row, "ID_Reporte", "ID Reporte", "Reporte"))
        linked_report = report_links.get(report_id, {})
        client_id = _text(_pick(row, "ID_Cliente", "ID Cliente", "Cliente"))
        client_id = client_id or equipment_client.get(equipment_id, "") or linked_report.get("client_id", "")
        equipment_id = equipment_id or linked_report.get("equipment_id", "")
        fault_id = _text(_pick(row, "ID_Falla", "ID Falla", "Folio", "ID"))
        fault_id = fault_id or f"FALLA-{client_id or 'SINCLIENTE'}-{position}"
        if fault_id in seen_faults:
            duplicate_faults += 1
            continue
        seen_faults.add(fault_id)
        description = _text(_pick(
            row, "DescripcionFalla", "Descripción de la falla", "Descripcion de la falla",
            "Falla", "Detalle", "Observaciones", "Reporte de falla",
        ))
        # No importar renglones vacíos o auxiliares de AppSheet.
        if not any((client_id, equipment_id, report_id, description)):
            continue
        fault = {
            "id": fault_id,
            "client_id": client_id,
            "equipment_id": equipment_id,
            "report_id": report_id,
            "description": description or "Falla reportada",
            "priority": _text(_pick(row, "Prioridad", "Gravedad", "Nivel")) or "Alta",
            "status": _text(_pick(row, "Estatus", "Estado", "Status")) or "Reportada",
            "reported_at": _text(_pick(row, "FechaReporte", "Fecha de reporte", "Fecha", "FechaHora")),
        }
        if include_raw:
            fault["_raw"] = dict(row)
        faults.append(fault)

    client_ids = {item["id"] for item in clients}
    equipment_ids = {item["id"] for item in equipment}
    return {
        "clients": clients,
        "equipment": equipment,
        "reports": reports,
        "faults": faults,
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
            "faults": len(faults),
            "duplicate_faults": duplicate_faults,
            "fault_sheets": fault_sheet_count,
        },
    }


def read_operaciones_matrix(service, spreadsheet_id, *, include_media_refs=False, include_raw=False):
    ranges = list(MATRIX_RANGES)
    try:
        metadata = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties.title",
        ).execute()
        fault_titles = []
        for sheet in metadata.get("sheets") or []:
            title = _text((sheet.get("properties") or {}).get("title"))
            if title and "falla" in _header_key(title):
                fault_titles.append(title)
        ranges.extend(f"'{title}'!A1:ZZ" for title in fault_titles)
    except Exception:
        # La lectura principal sigue disponible aunque la cuenta no permita metadatos.
        pass
    response = service.spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id,
        ranges=ranges,
        majorDimension="ROWS",
    ).execute()
    return build_operaciones_bootstrap(
        response.get("valueRanges") or [], include_media_refs=include_media_refs,
        include_raw=include_raw,
    )
