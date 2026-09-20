"""Lectura segura de la Hoja Matriz para la vista previa de Operaciones."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone


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
    fault_signatures = {}
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
        source_fault_id = _text(_pick(
            row, "ID_ReporteFalla", "ID Reporte Falla", "ID_Falla", "ID Falla", "Folio", "ID",
        ))
        generated_id = not bool(source_fault_id)
        fault_id = source_fault_id or f"FALLA-{client_id or 'SINCLIENTE'}-{position}"
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
            "priority": _text(_pick(row, "TipoFalla", "Tipo Falla", "Prioridad", "Gravedad", "Nivel")) or "Alta",
            "status": _text(_pick(row, "Estatus", "Estado", "Status")) or "Reportada",
            "reported_at": _text(_pick(row, "FechaReporte", "Fecha de reporte", "Fecha", "FechaHora")),
            "resolved_at": _text(_pick(row, "FechaAtendida", "Fecha atendida", "Fecha de atención", "FechaResolucion")),
            "resolved_by": _text(_pick(row, "AtendidaPor", "Atendida por", "Responsable", "Resuelto por")),
            "resolution_notes": _text(_pick(row, "Solucion", "Solución", "Trabajo realizado", "Observaciones de cierre")),
        }
        if include_raw:
            fault["_raw"] = dict(row)
        signature = (
            client_id.casefold(), equipment_id.casefold(), report_id.casefold(),
            " ".join(fault["description"].casefold().split()), fault["reported_at"].casefold(),
            fault["status"].casefold(),
        )
        previous = fault_signatures.get(signature)
        if previous:
            previous_index, previous_generated = previous
            # Algunas matrices conservan la misma fila en una pestaña antigua
            # sin ID y en la tabla actual de AppSheet con UNIQUEID(). Se conserva
            # siempre la fila real de AppSheet, no el identificador provisional.
            if previous_generated and not generated_id:
                seen_faults.discard(faults[previous_index]["id"])
                faults[previous_index] = fault
                seen_faults.add(fault_id)
                fault_signatures[signature] = (previous_index, False)
            duplicate_faults += 1
            continue
        fault_signatures[signature] = (len(faults), generated_id)
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
    read_started_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
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
    payload = build_operaciones_bootstrap(
        response.get("valueRanges") or [], include_media_refs=include_media_refs,
        include_raw=include_raw,
    )
    payload['_read_started_at'] = read_started_at
    return payload


def delete_operaciones_client_rows(service, spreadsheet_id, client_ids):
    """Borra de la matriz sólo filas relacionadas con clientes explícitos.

    Primero descubre equipos y reportes relacionados y después elimina, de abajo
    hacia arriba, las filas coincidentes de cualquier pestaña que use IDs de
    Cliente, Equipo o Reporte. Así también cubre hojas auxiliares de AppSheet.
    """
    targets = {_text(value).casefold() for value in client_ids if _text(value)}
    if not targets:
        return {"clients": [], "equipment": [], "reports": [], "rows_deleted": 0, "sheets": {}}

    metadata = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties(sheetId,title)",
    ).execute()
    properties = [item.get("properties") or {} for item in metadata.get("sheets") or []]
    titles = [_text(item.get("title")) for item in properties if _text(item.get("title"))]
    if not titles:
        return {"clients": sorted(targets), "equipment": [], "reports": [], "rows_deleted": 0, "sheets": {}}

    header_ranges = [f"'{title.replace(chr(39), chr(39) * 2)}'!1:1" for title in titles]
    header_response = service.spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id, ranges=header_ranges, majorDimension="ROWS",
    ).execute()
    header_values = header_response.get("valueRanges") or []
    candidate_titles = []
    headers_by_title = {}
    interesting = {_header_key(value) for value in ("ID_Cliente", "ID_Equipo", "ID_Reporte")}
    for title, item in zip(titles, header_values):
        headers = list(((item.get("values") or [[]])[0]))
        if interesting & {_header_key(value) for value in headers}:
            candidate_titles.append(title)
            headers_by_title[title] = headers

    data_ranges = [f"'{title.replace(chr(39), chr(39) * 2)}'!A2:ZZ" for title in candidate_titles]
    data_response = service.spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id, ranges=data_ranges, majorDimension="ROWS",
    ).execute() if data_ranges else {"valueRanges": []}
    rows_by_title = {
        title: list(item.get("values") or [])
        for title, item in zip(candidate_titles, data_response.get("valueRanges") or [])
    }

    equipment_ids, report_ids = set(), set()
    normalized_rows = {}
    for title in candidate_titles:
        headers = headers_by_title[title]
        records = []
        for row_number, values in enumerate(rows_by_title.get(title, []), start=2):
            padded = list(values) + [""] * max(0, len(headers) - len(values))
            record = {_header_key(header): _text(padded[index]) for index, header in enumerate(headers) if _text(header)}
            records.append((row_number, record))
        normalized_rows[title] = records

    client_key, equipment_key, report_key = map(_header_key, ("ID_Cliente", "ID_Equipo", "ID_Reporte"))
    changed = True
    while changed:
        changed = False
        for records in normalized_rows.values():
            for _, record in records:
                client_id = record.get(client_key, "").casefold()
                equipment_id = record.get(equipment_key, "").casefold()
                report_id = record.get(report_key, "").casefold()
                related = client_id in targets or equipment_id in equipment_ids or report_id in report_ids
                if not related:
                    continue
                if equipment_id and equipment_id not in equipment_ids:
                    equipment_ids.add(equipment_id)
                    changed = True
                if report_id and report_id not in report_ids:
                    report_ids.add(report_id)
                    changed = True

    rows_to_delete = {}
    for title, records in normalized_rows.items():
        matches = []
        for row_number, record in records:
            client_id = record.get(client_key, "").casefold()
            equipment_id = record.get(equipment_key, "").casefold()
            report_id = record.get(report_key, "").casefold()
            if client_id in targets or equipment_id in equipment_ids or report_id in report_ids:
                matches.append(row_number)
        if matches:
            rows_to_delete[title] = matches

    sheet_ids = {_text(item.get("title")): item.get("sheetId") for item in properties}
    requests = []
    for title, row_numbers in rows_to_delete.items():
        for row_number in sorted(row_numbers, reverse=True):
            requests.append({"deleteDimension": {"range": {
                "sheetId": sheet_ids[title], "dimension": "ROWS",
                "startIndex": row_number - 1, "endIndex": row_number,
            }}})
    if requests:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": requests},
        ).execute()
    return {
        "clients": sorted(targets), "equipment": sorted(equipment_ids),
        "reports": sorted(report_ids), "rows_deleted": len(requests),
        "sheets": {title: len(rows) for title, rows in rows_to_delete.items()},
    }
