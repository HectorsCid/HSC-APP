"""Persistencia operativa para desacoplar la app móvil de Google Sheets.

SQLite se usa únicamente para desarrollo local. En Render, la persistencia se
activa al definir ``OPERACIONES_DATABASE_URL`` con la URL interna de Postgres.
El esquema evita ORM a propósito: usa SQL portable y deja pequeña esta primera
etapa de la transición.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import uuid


SCHEMA_VERSION = "4"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value):
    return str(value or "").strip()


class OperationsStore:
    """Almacén relacional compatible con SQLite y PostgreSQL."""

    def __init__(self, database_url=None, *, local_path=None):
        explicit_url = _text(database_url or os.environ.get("OPERACIONES_DATABASE_URL"))
        self.database_url = explicit_url
        self.local_path = Path(local_path) if local_path else None
        self.dialect = "postgres" if explicit_url.startswith(("postgres://", "postgresql://")) else "sqlite"
        self.enabled = bool(explicit_url or self.local_path)
        self._initialized = False

    @classmethod
    def from_environment(cls, *, is_render=False, project_root=None):
        url = _text(os.environ.get("OPERACIONES_DATABASE_URL"))
        if url:
            return cls(url)
        if is_render:
            # Nunca aparentar persistencia con el disco efímero de Render.
            return cls()
        root = Path(project_root or Path.cwd())
        return cls(local_path=root / "data" / "operaciones_dev.sqlite3")

    @contextmanager
    def connection(self):
        if not self.enabled:
            raise RuntimeError("La base operativa no está configurada.")
        if self.dialect == "postgres":
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover - sólo ocurre al desplegar mal
                raise RuntimeError("Falta instalar psycopg para conectar PostgreSQL.") from exc
            conn = psycopg.connect(self.database_url)
        else:
            self.local_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.local_path, timeout=15)
            conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @property
    def placeholder(self):
        return "%s" if self.dialect == "postgres" else "?"

    def initialize(self):
        if not self.enabled or self._initialized:
            return
        blob_type = "BYTEA" if self.dialect == "postgres" else "BLOB"
        statements = [
            """CREATE TABLE IF NOT EXISTS operations_clients (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL DEFAULT '',
                matrix_id TEXT NOT NULL DEFAULT '',
                selected_round TEXT NOT NULL DEFAULT '', policy_active INTEGER NOT NULL DEFAULT 1,
                has_photo INTEGER NOT NULL DEFAULT 0, photo_ref TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'sheets', raw_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS operations_equipment (
                id TEXT PRIMARY KEY, client_id TEXT NOT NULL, name TEXT NOT NULL,
                brand TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
                serial TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '', department TEXT NOT NULL DEFAULT '',
                equipment_type TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                has_photo INTEGER NOT NULL DEFAULT 0, photo_ref TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'sheets', raw_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                FOREIGN KEY(client_id) REFERENCES operations_clients(id)
            )""",
            """CREATE TABLE IF NOT EXISTS operations_reports (
                id TEXT PRIMARY KEY, equipment_id TEXT NOT NULL DEFAULT '',
                client_id TEXT NOT NULL DEFAULT '', round_number TEXT NOT NULL DEFAULT '',
                start_at TEXT NOT NULL DEFAULT '', end_at TEXT NOT NULL DEFAULT '',
                completed INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT 'sheets',
                matrix_id TEXT NOT NULL DEFAULT '', report_type TEXT NOT NULL DEFAULT 'refrigeration',
                payload_json TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'imported',
                sync_status TEXT NOT NULL DEFAULT 'synced', updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS operations_evidence (
                id TEXT PRIMARY KEY, report_id TEXT NOT NULL, position INTEGER NOT NULL,
                storage_ref TEXT NOT NULL DEFAULT '', drive_ref TEXT NOT NULL DEFAULT '',
                sync_status TEXT NOT NULL DEFAULT 'synced', updated_at TEXT NOT NULL,
                UNIQUE(report_id, position),
                FOREIGN KEY(report_id) REFERENCES operations_reports(id)
            )""",
            """CREATE TABLE IF NOT EXISTS operations_sync_outbox (
                id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
                destination TEXT NOT NULL, action TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(entity_type, entity_id, destination, action)
            )""",
            """CREATE TABLE IF NOT EXISTS operations_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            )""",
            f"""CREATE TABLE IF NOT EXISTS operations_media_cache (
                kind TEXT NOT NULL, record_id TEXT NOT NULL, source_ref TEXT NOT NULL,
                content {blob_type} NOT NULL, mime_type TEXT NOT NULL DEFAULT 'image/webp',
                width INTEGER NOT NULL DEFAULT 0, height INTEGER NOT NULL DEFAULT 0,
                byte_size INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
                PRIMARY KEY(kind, record_id)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_operations_equipment_client ON operations_equipment(client_id)",
            "CREATE INDEX IF NOT EXISTS idx_operations_reports_equipment ON operations_reports(equipment_id)",
            "CREATE INDEX IF NOT EXISTS idx_operations_reports_client ON operations_reports(client_id)",
            "CREATE INDEX IF NOT EXISTS idx_operations_outbox_status ON operations_sync_outbox(status)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_operations_clients_matrix_id ON operations_clients(matrix_id) WHERE matrix_id <> ''",
        ]
        with self.connection() as conn:
            for statement in statements:
                conn.execute(statement)
            self._ensure_column(conn, "operations_clients", "raw_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "operations_equipment", "raw_json", "TEXT NOT NULL DEFAULT '{}'")
            self._upsert_meta(conn, "schema_version", SCHEMA_VERSION)
        self._initialized = True

    def _ensure_column(self, conn, table, column, definition):
        """Actualiza bases locales existentes sin depender de un ORM."""
        if self.dialect == "postgres":
            p = self.placeholder
            found = conn.execute(
                f"SELECT 1 FROM information_schema.columns WHERE table_name={p} AND column_name={p}",
                (table, column),
            ).fetchone()
        else:
            found = next((row for row in conn.execute(f"PRAGMA table_info({table})") if row[1] == column), None)
        if not found:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _upsert_meta(self, conn, key, value):
        p = self.placeholder
        conn.execute(
            f"INSERT INTO operations_meta(key,value,updated_at) VALUES ({p},{p},{p}) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, _text(value), _now()),
        )

    def _upsert_many(self, conn, table, columns, rows, update_columns, *, update_where=""):
        if not rows:
            return
        p = self.placeholder
        placeholders = ",".join([p] * len(columns))
        updates = ",".join(f"{column}=excluded.{column}" for column in update_columns)
        sql = (
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}"
            + (f" WHERE {update_where}" if update_where else "")
        )
        cursor = conn.cursor()
        try:
            cursor.executemany(sql, rows)
        finally:
            cursor.close()

    def import_matrix_snapshot(self, payload):
        """Importa el batch de Sheets en una transacción, conservando sus IDs."""
        self.initialize()
        stamp = _now()
        clients = [(
            _text(item.get("id")), _text(item.get("name")) or _text(item.get("id")),
            _text(item.get("address")), _text(item.get("id")), _text(item.get("selected_round")),
            int(item.get("policy_active", True) is not False),
            int(bool(item.get("has_photo"))), _text(item.get("_photo_ref")), "sheets",
            json.dumps(item.get("_raw") or {}, ensure_ascii=False), stamp,
        ) for item in payload.get("clients", []) if _text(item.get("id"))]
        equipment = [(
            _text(item.get("id")), _text(item.get("client_id")),
            _text(item.get("name")) or _text(item.get("id")), _text(item.get("brand")),
            _text(item.get("model")), _text(item.get("serial")), _text(item.get("status")),
            _text(item.get("location")), _text(item.get("department")),
            _text(item.get("equipment_type")), _text(item.get("notes")),
            int(bool(item.get("has_photo"))), _text(item.get("_photo_ref")), "sheets",
            json.dumps(item.get("_raw") or {}, ensure_ascii=False), stamp,
        ) for item in payload.get("equipment", [])
          if _text(item.get("id")) and _text(item.get("client_id"))]
        reports = [(
            _text(item.get("id")), _text(item.get("equipment_id")), _text(item.get("client_id")),
            _text(item.get("round")), _text(item.get("start")), _text(item.get("end")),
            int(bool(item.get("completed"))), "sheets", _text(item.get("id")),
            "refrigeration", json.dumps(item.get("_raw") or {}, ensure_ascii=False),
            "completed" if item.get("completed") else "imported",
            "synced", stamp,
        ) for item in payload.get("reports", []) if _text(item.get("id"))]
        with self.connection() as conn:
            self._upsert_many(conn, "operations_clients",
                ["id","name","address","matrix_id","selected_round","policy_active","has_photo","photo_ref","source","raw_json","updated_at"], clients,
                ["name","address","matrix_id","selected_round","has_photo","photo_ref","source","raw_json","updated_at"],
                update_where="operations_clients.source='sheets'")
            self._upsert_many(conn, "operations_equipment",
                ["id","client_id","name","brand","model","serial","status","location","department","equipment_type","notes","has_photo","photo_ref","source","raw_json","updated_at"], equipment,
                ["client_id","name","brand","model","serial","status","location","department","equipment_type","notes","has_photo","photo_ref","source","raw_json","updated_at"],
                update_where="operations_equipment.source='sheets'")
            self._upsert_many(conn, "operations_reports",
                ["id","equipment_id","client_id","round_number","start_at","end_at","completed","source","matrix_id","report_type","payload_json","state","sync_status","updated_at"], reports,
                ["equipment_id","client_id","round_number","start_at","end_at","completed","source","matrix_id","report_type","payload_json","state","sync_status","updated_at"],
                update_where="operations_reports.source='sheets'")
            # La matriz conserva Foto1..Foto6; se materializan como filas para la app.
            for item in payload.get("reports", []):
                report_id = _text(item.get("id"))
                evidence_refs = list(item.get("_evidence_refs") or [])[:6]
                if not evidence_refs:
                    evidence_refs = [""] * int(item.get("photo_count") or 0)
                p = self.placeholder
                source_row = conn.execute(
                    f"SELECT source FROM operations_reports WHERE id={p}", (report_id,)
                ).fetchone()
                if not source_row or source_row[0] != "sheets":
                    # Un refresco nunca debe borrar evidencia local todavía pendiente.
                    continue
                conn.execute(
                    f"DELETE FROM operations_evidence WHERE report_id={p} AND id LIKE {p}",
                    (report_id, f"{report_id}:foto:%"),
                )
                for position, storage_ref in enumerate(evidence_refs, start=1):
                    if item.get("_evidence_refs") is not None and not _text(storage_ref):
                        continue
                    evidence_id = f"{report_id}:foto:{position}"
                    conn.execute(
                        f"INSERT INTO operations_evidence(id,report_id,position,storage_ref,sync_status,updated_at) "
                        f"VALUES ({p},{p},{p},{p},{p},{p}) ON CONFLICT(id) DO UPDATE SET "
                        "position=excluded.position,storage_ref=excluded.storage_ref,"
                        "sync_status=excluded.sync_status,updated_at=excluded.updated_at",
                        (evidence_id, report_id, position, _text(storage_ref), "synced", stamp),
                    )
            self._upsert_meta(conn, "last_matrix_import", stamp)
        return {"clients": len(clients), "equipment": len(equipment), "reports": len(reports)}

    def has_data(self):
        if not self.enabled:
            return False
        self.initialize()
        with self.connection() as conn:
            return bool(conn.execute("SELECT 1 FROM operations_clients LIMIT 1").fetchone())

    def snapshot(self):
        self.initialize()
        with self.connection() as conn:
            clients = conn.execute(
                "SELECT id,name,address,matrix_id,selected_round,policy_active,has_photo,photo_ref FROM operations_clients ORDER BY name,id"
            ).fetchall()
            equipment = conn.execute(
                "SELECT id,client_id,name,brand,model,serial,status,location,department,equipment_type,notes,has_photo,photo_ref FROM operations_equipment ORDER BY client_id,name,id"
            ).fetchall()
            reports = conn.execute(
                "SELECT r.id,r.equipment_id,r.client_id,r.round_number,r.start_at,r.end_at,r.completed,"
                "r.matrix_id,r.report_type,r.state,r.sync_status,"
                "(SELECT COUNT(*) FROM operations_evidence e WHERE e.report_id=r.id) "
                "FROM operations_reports r ORDER BY r.start_at,r.id"
            ).fetchall()
            meta_rows = conn.execute("SELECT key,value FROM operations_meta").fetchall()
            pending = conn.execute("SELECT COUNT(*) FROM operations_sync_outbox WHERE status!='synced'").fetchone()[0]
            cached_thumbnails = conn.execute("SELECT COUNT(*) FROM operations_media_cache").fetchone()[0]
        result_clients = [{
            "id": row[0], "name": row[1], "address": row[2], "matrix_id": row[3],
            "selected_round": row[4], "policy_active": bool(row[5]),
            "has_photo": bool(row[6]), "_photo_ref": row[7],
        } for row in clients]
        result_equipment = [{
            "id": row[0], "client_id": row[1], "name": row[2], "brand": row[3],
            "model": row[4], "serial": row[5], "status": row[6], "location": row[7],
            "department": row[8], "equipment_type": row[9], "notes": row[10],
            "has_photo": bool(row[11]), "_photo_ref": row[12],
        } for row in equipment]
        result_reports = [{
            "id": row[0], "equipment_id": row[1], "client_id": row[2], "round": row[3],
            "start": row[4], "end": row[5], "completed": bool(row[6]), "matrix_id": row[7],
            "report_type": row[8], "state": row[9], "sync_status": row[10], "photo_count": row[11],
        } for row in reports]
        client_ids = {item["id"] for item in result_clients}
        equipment_ids = {item["id"] for item in result_equipment}
        return {
            "clients": result_clients, "equipment": result_equipment, "reports": result_reports,
            "stats": {
                "clients": len(result_clients), "equipment": len(result_equipment), "reports": len(result_reports),
                "client_photos": sum(item["has_photo"] for item in result_clients),
                "equipment_photos": sum(item["has_photo"] for item in result_equipment),
                "evidence_photos": sum(item["photo_count"] for item in result_reports),
                "orphan_equipment": sum(item["client_id"] not in client_ids for item in result_equipment),
                "orphan_reports": sum(bool(item["equipment_id"]) and item["equipment_id"] not in equipment_ids for item in result_reports),
                "duplicate_clients": 0, "duplicate_equipment": 0, "duplicate_reports": 0,
                "pending_sync": pending,
                "cached_thumbnails": cached_thumbnails,
            },
            "meta": dict(meta_rows),
        }

    def status(self):
        if not self.enabled:
            return {"enabled": False, "engine": None, "has_data": False}
        self.initialize()
        snapshot = self.snapshot()
        return {
            "enabled": True, "engine": "postgresql" if self.dialect == "postgres" else "sqlite-local",
            "has_data": bool(snapshot["clients"]), "stats": snapshot["stats"],
            "last_matrix_import": snapshot.get("meta", {}).get("last_matrix_import"),
        }

    def get_media_ref(self, kind, record_id):
        """Obtiene la referencia original sin depender del caché en memoria del proceso."""
        self.initialize()
        table = "operations_clients" if kind == "client" else "operations_equipment"
        p = self.placeholder
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT photo_ref FROM {table} WHERE id={p}", (_text(record_id),)
            ).fetchone()
        return _text(row[0]) if row else ""

    def get_cached_thumbnail(self, kind, record_id, source_ref):
        """Devuelve una miniatura sólo si corresponde a la foto original actual."""
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT content,mime_type,width,height FROM operations_media_cache "
                f"WHERE kind={p} AND record_id={p} AND source_ref={p}",
                (_text(kind), _text(record_id), _text(source_ref)),
            ).fetchone()
        if not row:
            return None
        return {
            "content": bytes(row[0]), "mime_type": row[1],
            "width": int(row[2] or 0), "height": int(row[3] or 0),
        }

    def save_cached_thumbnail(self, kind, record_id, source_ref, content, mime_type, width, height):
        """Guarda una versión ligera; cambiar la referencia reemplaza el caché anterior."""
        self.initialize()
        p = self.placeholder
        binary = bytes(content)
        with self.connection() as conn:
            conn.execute(
                f"INSERT INTO operations_media_cache"
                f"(kind,record_id,source_ref,content,mime_type,width,height,byte_size,updated_at) "
                f"VALUES ({','.join([p] * 9)}) ON CONFLICT(kind,record_id) DO UPDATE SET "
                "source_ref=excluded.source_ref,content=excluded.content,mime_type=excluded.mime_type,"
                "width=excluded.width,height=excluded.height,byte_size=excluded.byte_size,updated_at=excluded.updated_at",
                (_text(kind), _text(record_id), _text(source_ref), binary, _text(mime_type),
                 int(width or 0), int(height or 0), len(binary), _now()),
            )

    def save_client(self, item):
        """Crea o edita un cliente sin cambiar su ID interno."""
        self.initialize()
        client_id = _text(item.get("id"))
        name = _text(item.get("name"))
        matrix_id = _text(item.get("matrix_id")).upper()
        if not client_id or not name:
            raise ValueError("El ID y el nombre del cliente son obligatorios.")
        current = self.snapshot()
        normalized = " ".join(name.casefold().split())
        for other in current["clients"]:
            if other["id"] != client_id and " ".join(other["name"].casefold().split()) == normalized:
                raise ValueError("Ya existe un cliente con ese nombre.")
            if matrix_id and other["id"] != client_id and _text(other.get("matrix_id")).upper() == matrix_id:
                raise ValueError("Ese ID de matriz ya está vinculado a otro cliente.")
        stamp = _now()
        p = self.placeholder
        with self.connection() as conn:
            conn.execute(
                f"INSERT INTO operations_clients(id,name,address,matrix_id,selected_round,policy_active,has_photo,photo_ref,source,raw_json,updated_at) "
                f"VALUES ({','.join([p] * 11)}) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name,address=excluded.address,matrix_id=excluded.matrix_id,"
                "selected_round=excluded.selected_round,policy_active=excluded.policy_active,source='app',updated_at=excluded.updated_at",
                (client_id, name, _text(item.get("address")), matrix_id,
                 _text(item.get("selected_round")), int(bool(item.get("policy_active", True))),
                 0, "", "app", "{}", stamp),
            )
        self.queue_sync("client", client_id, "sheets", "upsert", {"matrix_id": matrix_id})
        return next(client for client in self.snapshot()["clients"] if client["id"] == client_id)

    def save_equipment(self, items):
        """Guarda uno o varios equipos como una sola operación."""
        self.initialize()
        rows = list(items or [])
        if not rows or len(rows) > 100:
            raise ValueError("Debes enviar entre 1 y 100 equipos.")
        snapshot = self.snapshot()
        client_ids = {client["id"] for client in snapshot["clients"]}
        request_ids = set()
        prepared = []
        stamp = _now()
        for item in rows:
            equipment_id = _text(item.get("id"))
            client_id = _text(item.get("client_id"))
            name = _text(item.get("name"))
            if not equipment_id or not client_id or not name:
                raise ValueError("ID, cliente y nombre son obligatorios para cada equipo.")
            if client_id not in client_ids:
                raise ValueError("El cliente seleccionado no existe en la base operativa.")
            if equipment_id in request_ids:
                raise ValueError("El lote contiene un ID de equipo repetido.")
            request_ids.add(equipment_id)
            prepared.append((
                equipment_id, client_id, name, _text(item.get("brand")),
                _text(item.get("model")), _text(item.get("serial")),
                "Activo" if item.get("active", True) else "Inactivo",
                _text(item.get("location")), _text(item.get("department")),
                _text(item.get("equipment_type")), _text(item.get("notes")),
                0, "", "app", "{}", stamp,
            ))
        with self.connection() as conn:
            self._upsert_many(conn, "operations_equipment",
                ["id","client_id","name","brand","model","serial","status","location","department","equipment_type","notes","has_photo","photo_ref","source","raw_json","updated_at"],
                prepared,
                ["client_id","name","brand","model","serial","status","location","department","equipment_type","notes","source","updated_at"])
        for equipment_id in request_ids:
            self.queue_sync("equipment", equipment_id, "sheets", "upsert")
        current = self.snapshot()["equipment"]
        return [item for item in current if item["id"] in request_ids]

    def save_report_draft(self, item):
        """Guarda el texto del reporte; las evidencias se almacenan por separado."""
        self.initialize()
        equipment_id = _text(item.get("equipment_id"))
        client_id = _text(item.get("client_id"))
        round_number = _text(item.get("round"))
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if not equipment_id or not client_id or round_number not in {"1", "2", "3", "4"}:
            raise ValueError("Cliente, equipo y ronda son obligatorios.")
        equipment = next((row for row in self.snapshot()["equipment"] if row["id"] == equipment_id), None)
        if not equipment or equipment["client_id"] != client_id:
            raise ValueError("El equipo no pertenece al cliente seleccionado.")
        p = self.placeholder
        report_id = _text(item.get("id"))
        with self.connection() as conn:
            if report_id:
                found = conn.execute(
                    f"SELECT id FROM operations_reports WHERE id={p} AND source='app' AND state='draft'",
                    (report_id,),
                ).fetchone()
                if not found:
                    raise ValueError("El borrador indicado ya no existe o ya fue finalizado.")
            else:
                found = conn.execute(
                    f"SELECT id FROM operations_reports WHERE equipment_id={p} AND round_number={p} "
                    "AND source='app' AND state='draft' ORDER BY updated_at DESC LIMIT 1",
                    (equipment_id, round_number),
                ).fetchone()
                report_id = found[0] if found else f"RPT_{uuid.uuid4().hex[:16].upper()}"
            stamp = _now()
            conn.execute(
                f"INSERT INTO operations_reports(id,equipment_id,client_id,round_number,start_at,end_at,completed,source,matrix_id,report_type,payload_json,state,sync_status,updated_at) "
                f"VALUES ({','.join([p] * 14)}) ON CONFLICT(id) DO UPDATE SET "
                "equipment_id=excluded.equipment_id,client_id=excluded.client_id,round_number=excluded.round_number,"
                "start_at=excluded.start_at,end_at=excluded.end_at,report_type=excluded.report_type,"
                "payload_json=excluded.payload_json,state='draft',sync_status='local_only',updated_at=excluded.updated_at",
                (report_id, equipment_id, client_id, round_number, _text(payload.get("inicio")),
                 _text(payload.get("fin")), 0, "app", "", _text(item.get("report_type")) or "refrigeration",
                 json.dumps(payload, ensure_ascii=False), "draft", "local_only", stamp),
            )
        return self.get_report_draft(equipment_id, round_number)

    def get_report_draft(self, equipment_id, round_number):
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT id,client_id,equipment_id,round_number,report_type,payload_json,updated_at "
                f"FROM operations_reports WHERE equipment_id={p} AND round_number={p} "
                "AND source='app' AND state='draft' ORDER BY updated_at DESC LIMIT 1",
                (_text(equipment_id), _text(round_number)),
            ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "client_id": row[1], "equipment_id": row[2], "round": row[3],
            "report_type": row[4], "payload": json.loads(row[5] or "{}"), "updated_at": row[6],
        }

    def queue_sync(self, entity_type, entity_id, destination, action, payload=None):
        """Encola una operación idempotente hacia Drive o Sheets."""
        self.initialize()
        stamp = _now()
        operation_id = uuid.uuid4().hex
        p = self.placeholder
        with self.connection() as conn:
            conn.execute(
                f"INSERT INTO operations_sync_outbox"
                f"(id,entity_type,entity_id,destination,action,payload_json,status,attempts,last_error,created_at,updated_at) "
                f"VALUES ({','.join([p] * 11)}) ON CONFLICT(entity_type,entity_id,destination,action) "
                "DO UPDATE SET payload_json=excluded.payload_json,status='pending',last_error='',updated_at=excluded.updated_at",
                (operation_id, _text(entity_type), _text(entity_id), _text(destination),
                 _text(action), json.dumps(payload or {}, ensure_ascii=False), "pending", 0, "", stamp, stamp),
            )
        return operation_id

    def pending_sync(self, limit=50):
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT id,entity_type,entity_id,destination,action,payload_json,attempts,last_error "
                f"FROM operations_sync_outbox WHERE status!='synced' ORDER BY created_at LIMIT {p}",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [{
            "id": row[0], "entity_type": row[1], "entity_id": row[2],
            "destination": row[3], "action": row[4], "payload": json.loads(row[5] or "{}"),
            "attempts": row[6], "last_error": row[7],
        } for row in rows]
