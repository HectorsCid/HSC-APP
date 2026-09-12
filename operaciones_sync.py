"""Salida idempotente de la base operativa hacia la Hoja Matriz.

Las escrituras siempre se resuelven por encabezado e ID. Una fila de Reportes
se escribe primero sin ``Realizado``; sólo después de confirmar todos los datos
y Foto1–Foto6 se activa esa celda para no disparar PDFs incompletos.
"""

from __future__ import annotations

import re
import unicodedata


def _text(value):
    return str(value or "").strip()


def _key(value):
    plain = unicodedata.normalize("NFKD", _text(value)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", plain.casefold())


def _quote_sheet(title):
    return "'{}'".format(_text(title).replace("'", "''"))


def _column_name(index):
    value, result = int(index) + 1, ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _round_label(value):
    match = re.search(r"([1-4])", _text(value))
    return f"R {match.group(1)}" if match else ""


def _entity_snapshot(store, operation):
    entity_type, entity_id = operation["entity_type"], operation["entity_id"]
    snapshot = store.snapshot()
    clients = {row["id"]: row for row in snapshot.get("clients", [])}
    equipment = {row["id"]: row for row in snapshot.get("equipment", [])}
    if entity_type == "client":
        item = clients.get(entity_id)
        if not item:
            raise ValueError("El cliente ya no existe en la base operativa.")
        sheet_id = _text(item.get("matrix_id")) or item["id"]
        return "Clientes", "ID_Cliente", sheet_id, {
            "ID_Cliente": sheet_id,
            "NombreCliente": item.get("name"),
            "Direccion": item.get("address"),
            "RondaSeleccionadaCliente": _round_label(item.get("selected_round")),
        }
    if entity_type == "equipment":
        item = equipment.get(entity_id)
        if not item:
            raise ValueError("El equipo ya no existe en la base operativa.")
        client = clients.get(item.get("client_id"), {})
        client_sheet_id = _text(client.get("matrix_id")) or _text(item.get("client_id"))
        return "Equipos", "ID_Equipo", item["id"], {
            "ID_Equipo": item.get("id"), "ID_Cliente": client_sheet_id,
            "NombreEquipo": item.get("name"), "Marca": item.get("brand"),
            "Modelo": item.get("model"), "NoSerie": item.get("serial"),
            "Estatus": item.get("status"), "Ubicacion": item.get("location"),
            "Departamento": item.get("department"),
        }
    if entity_type == "report":
        item = store.get_report_detail(entity_id)
        if not item:
            raise ValueError("El reporte ya no existe en la base operativa.")
        equip = equipment.get(item.get("equipment_id"), {})
        client = clients.get(item.get("client_id"), {})
        payload = item.get("payload") or {}
        client_sheet_id = _text(client.get("matrix_id")) or _text(item.get("client_id"))
        values = {
            "ID_Reporte": item.get("id"), "ID_Equipo": item.get("equipment_id"),
            "ID_Cliente": client_sheet_id, "FechaInicio": payload.get("inicio") or item.get("start"),
            "FechaFin": payload.get("fin") or item.get("end"),
            "PresionCto1": payload.get("p1"), "PresionCto2": payload.get("p2"),
            "TempCto1": payload.get("t1"), "TempCto2": payload.get("t2"),
            "Amperaje1": payload.get("a1"), "Amperaje2": payload.get("a2"),
            "ObsElectrico": payload.get("electrico"), "OBsElectrónico": payload.get("electronico"),
            "ObsMecanico": payload.get("mecanico"), "Comentarios": payload.get("notas"),
            "MtoCorrectivo": payload.get("correctivo"), "PartesUtilizadas": payload.get("partes"),
            "NombreEquipo": equip.get("name"), "Marca": equip.get("brand"),
            "Direccion": client.get("address"), "Ronda": _round_label(item.get("round")),
            "Modelo": equip.get("model"), "NoSerie": equip.get("serial"),
            "Ubicacion": equip.get("location"), "Departamento": equip.get("department"),
            "Responsable": payload.get("revisor"), "Realizado": True,
        }
        for evidence in item.get("evidence", []):
            position = int(evidence.get("position") or 0)
            if position in range(1, 7) and _text(evidence.get("drive_ref") or evidence.get("storage_ref")):
                values[f"Foto{position}"] = evidence.get("drive_ref") or evidence.get("storage_ref")
        return "Reportes", "ID_Reporte", item["id"], values
    if entity_type == "fault":
        item = next((row for row in snapshot.get("faults", []) if row["id"] == entity_id), None)
        if not item:
            raise ValueError("La falla ya no existe en la base operativa.")
        client = clients.get(item.get("client_id"), {})
        client_sheet_id = _text(client.get("matrix_id")) or _text(item.get("client_id"))
        return "ReportesFalla", "ID_ReporteFalla", item["id"], {
            "ID_ReporteFalla": item.get("id"), "ID_Cliente": client_sheet_id,
            "ID_Equipo": item.get("equipment_id"), "Fecha": item.get("reported_at"),
            "DescripcionFalla": item.get("description"), "Estado": item.get("status"),
            "ID_Reporte": item.get("report_id"), "TipoFalla": item.get("priority"),
            "MostrarCliente": True, "Origen": "Cliente" if item.get("source") == "client" else "Reporte técnico",
        }
    raise ValueError(f"Tipo de salida no compatible: {entity_type}.")


def _get_sheet_titles(service, spreadsheet_id):
    response = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id, fields="sheets.properties.title",
    ).execute()
    return [_text((row.get("properties") or {}).get("title")) for row in response.get("sheets") or []]


def _resolve_title(titles, requested):
    exact = next((title for title in titles if _key(title) == _key(requested)), None)
    if exact:
        return exact
    if "falla" in _key(requested):
        match = next((title for title in titles if "falla" in _key(title)), None)
        if match:
            return match
    raise ValueError(f"No existe la pestaña {requested} en la Hoja Matriz.")


def _read_index(service, spreadsheet_id, title, id_header):
    quoted = _quote_sheet(title)
    response = service.spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id,
        ranges=[f"{quoted}!1:1", f"{quoted}!A2:ZZ"],
        majorDimension="ROWS",
    ).execute()
    ranges = response.get("valueRanges") or []
    headers = list(((ranges[0].get("values") or [[]])[0])) if ranges else []
    header_index = {_key(value): index for index, value in enumerate(headers) if _text(value)}
    id_index = header_index.get(_key(id_header))
    if id_index is None:
        raise ValueError(f"La pestaña {title} no contiene {id_header}.")
    rows = (ranges[1].get("values") or []) if len(ranges) > 1 else []
    matches = {}
    for row_number, row in enumerate(rows, start=2):
        value = _text(row[id_index]) if id_index < len(row) else ""
        if value:
            matches.setdefault(value, []).append(row_number)
    return headers, header_index, matches


def _plan_operation(store, operation, service, spreadsheet_id, titles, indexes):
    requested_title, id_header, entity_id, values = _entity_snapshot(store, operation)
    title = _resolve_title(titles, requested_title)
    index_key = (title, id_header)
    if index_key not in indexes:
        indexes[index_key] = _read_index(service, spreadsheet_id, title, id_header)
    headers, header_index, matches = indexes[index_key]
    row_matches = matches.get(_text(entity_id), [])
    if len(row_matches) > 1:
        raise ValueError(f"{title} contiene el ID duplicado {entity_id}; no se escribió nada.")
    row_number = row_matches[0] if row_matches else None
    resolved = []
    for header, value in values.items():
        column = header_index.get(_key(header))
        if column is not None:
            resolved.append((column, headers[column], "" if value is None else value))
    required = {_key(id_header), _key("ID_Cliente") if operation["entity_type"] != "client" else _key(id_header)}
    present = {_key(header) for _, header, _ in resolved}
    if not required.issubset(present):
        raise ValueError(f"Faltan encabezados obligatorios en {title}.")
    return {
        "operation": operation, "title": title, "headers": headers,
        "entity_id": entity_id, "row_number": row_number, "values": resolved,
    }


def _write_plan(service, spreadsheet_id, plan):
    title, quoted = plan["title"], _quote_sheet(plan["title"])
    row_number, values = plan["row_number"], plan["values"]
    completed = [entry for entry in values if _key(entry[1]) == _key("Realizado")]
    initial = [entry for entry in values if _key(entry[1]) != _key("Realizado")]
    api = service.spreadsheets().values()
    if row_number is None:
        row = [""] * len(plan["headers"])
        for column, _, value in initial:
            row[column] = value
        result = api.append(
            spreadsheetId=spreadsheet_id, range=f"{quoted}!A:{_column_name(len(row)-1)}",
            valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS", body={"values": [row]},
        ).execute()
        updated_range = _text((result.get("updates") or {}).get("updatedRange"))
        match = re.search(r"![A-Z]+(\d+)(?::[A-Z]+\d+)?$", updated_range)
        if not match:
            raise RuntimeError(f"Sheets no confirmó la fila nueva en {title}.")
        row_number = int(match.group(1))
    elif initial:
        api.batchUpdate(
            spreadsheetId=spreadsheet_id, body={
                "valueInputOption": "USER_ENTERED",
                "data": [{"range": f"{quoted}!{_column_name(column)}{row_number}", "values": [[value]]}
                         for column, _, value in initial],
            },
        ).execute()
    # Realizado va en una petición final. Si algo anterior falla, el monitor de
    # PDFs no verá un reporte terminado a medias.
    if completed:
        column, _, value = completed[0]
        api.update(
            spreadsheetId=spreadsheet_id,
            range=f"{quoted}!{_column_name(column)}{row_number}",
            valueInputOption="USER_ENTERED", body={"values": [[value]]},
        ).execute()
    return row_number


def sync_operations_outbox(store, service, spreadsheet_id, *, limit=25, dry_run=False, max_attempts=6):
    """Procesa la cola; en dry-run sólo lee y devuelve el plan."""
    operations = [
        row for row in store.pending_sync(limit)
        if row.get("destination") == "sheets"
        and (dry_run or int(row.get("attempts") or 0) < int(max_attempts))
    ]
    result = {"dry_run": bool(dry_run), "pending": len(operations), "synced": 0, "failed": 0, "items": []}
    if not operations:
        return result
    titles = _get_sheet_titles(service, spreadsheet_id)
    indexes = {}
    for operation in operations:
        try:
            plan = _plan_operation(store, operation, service, spreadsheet_id, titles, indexes)
            item = {
                "operation_id": operation["id"], "entity_type": operation["entity_type"],
                "entity_id": operation["entity_id"], "sheet": plan["title"],
                "action": "update" if plan["row_number"] else "append",
                "row": plan["row_number"], "columns": [header for _, header, _ in plan["values"]],
            }
            if not dry_run:
                item["row"] = _write_plan(service, spreadsheet_id, plan)
                store.mark_sync_success(operation["id"], operation["entity_type"], operation["entity_id"])
                result["synced"] += 1
            result["items"].append(item)
        except Exception as exc:
            result["failed"] += 1
            result["items"].append({
                "operation_id": operation["id"], "entity_type": operation["entity_type"],
                "entity_id": operation["entity_id"], "error": _text(exc),
            })
            if not dry_run:
                store.mark_sync_failure(operation["id"], exc, operation["entity_type"], operation["entity_id"])
    return result
