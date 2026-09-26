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
import re
import sqlite3
import uuid


SCHEMA_VERSION = "19"


DEFAULT_OBSERVATION_OPTIONS = {
    "electrico": [
        "Cables dañados.",
        "Falla de luz.",
        "Variación de voltaje.",
        "Cortocircuito en el sistema.",
        "Asistente de arranque en el sistema.",
        "Equipo trabaja correctamente.",
        "Se recomienda instalar una pastilla termomagnética.",
        "Se recomienda mejorar y asegurar la instalación eléctrica debido a conexiones expuestas o protección inadecuada, a fin de prevenir cortocircuitos y fallas eléctricas.",
        "Tapa de caja de interruptor sin tornillos.",
        "Falla en cableado.",
        "Falla en componente eléctrico.",
    ],
    "electronico": [
        "Falla en control de temperatura.",
        "Tarjeta electrónica deshabilitada.",
        "Tarjeta electrónica dañada.",
        "Se recomienda sustituir el control.",
        "Se sugiere barnizado de transformador.",
        "Se detecta defecto en el display indicador del minisplit, con segmentos de iluminación irregular o incompleta.",
        "Se realizó sustitución del sensor térmico.",
        "Capacitor con daño físico.",
        "Se detecta control remoto sin funcionamiento; no emite señal ni permite controlar el encendido, apagado o cambio de parámetros. Se recomienda sustituirlo.",
        "Falla en tarjeta electrónica.",
    ],
    "mecanico": [
        "Falta de gas refrigerante.",
        "Compresor amarrado.",
        "Compresor aterrizado.",
        "Motor ventilador dañado.",
        "Presostato dañado.",
        "No cuenta con válvula de servicio.",
        "Presión de gas por debajo de lo recomendado.",
        "Presión de gas ligeramente por debajo de lo recomendado.",
        "Requiere prueba de hermeticidad o carga de gas.",
        "Empaques de puerta dañados.",
        "Falta la tapa superior de la condensadora.",
        "Puerta de hielera en mal estado.",
        "Se recomienda realizar una prueba de eficiencia al compresor.",
        "Se recomienda colocar aislante y cinta para tuberías.",
        "Filtro dañado.",
        "Se detectaron daños en las uniones de los ductos.",
        "Se detectaron fracturas en los puntos de sujeción de la tapa frontal de la evaporadora.",
        "Corrosión en la protección de la condensadora; se recomienda aplicar recubrimiento.",
        "Se encontró una fuga ligera en la válvula de servicio y se realizó un ajuste en su núcleo.",
        "Se realizó carga de gas refrigerante.",
        "Se recomienda cambiar los empaques.",
        "Corrosión en la tubería de descarga; se recomienda aplicar recubrimiento para evitar futuras fugas.",
        "Se recomienda sustituir el presostato.",
        "Requiere calibración de gas refrigerante.",
        "Se detectaron indicios de falla en la válvula solenoide; se recomienda evaluar su sustitución.",
        "Requiere cambio de tubería de desagüe.",
        "Corrosión en la salida de desagüe.",
    ],
    "notas": [
        "Se realizó mantenimiento preventivo y chequeo de parámetros. Se deja el equipo trabajando bien.",
    ],
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _text(value):
    return str(value or "").strip()


def _observation_key(value):
    """Iguala mayúsculas, espacios y puntuación final para no repetir sugerencias."""
    normalized = re.sub(r"\s+", " ", _text(value)).casefold()
    return re.sub(r"[.!?;:,]+$", "", normalized).strip()


def _valid_date(value):
    try:
        datetime.strptime(_text(value), "%Y-%m-%d")
        return True
    except ValueError:
        return False


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
                billing_rfc TEXT NOT NULL DEFAULT '',
                selected_round TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0,
                policy_active INTEGER NOT NULL DEFAULT 1,
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
                sync_status TEXT NOT NULL DEFAULT 'synced', revision INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS operations_evidence (
                id TEXT PRIMARY KEY, report_id TEXT NOT NULL, position INTEGER NOT NULL,
                storage_ref TEXT NOT NULL DEFAULT '', drive_ref TEXT NOT NULL DEFAULT '',
                sync_status TEXT NOT NULL DEFAULT 'synced', actor_id TEXT NOT NULL DEFAULT '',
                actor_name TEXT NOT NULL DEFAULT '', mutation_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
                UNIQUE(report_id, position),
                FOREIGN KEY(report_id) REFERENCES operations_reports(id)
            )""",
            """CREATE TABLE IF NOT EXISTS operations_report_presence (
                report_id TEXT NOT NULL, user_id TEXT NOT NULL, user_name TEXT NOT NULL DEFAULT '',
                last_seen_at TEXT NOT NULL, PRIMARY KEY(report_id,user_id),
                FOREIGN KEY(report_id) REFERENCES operations_reports(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS operations_faults (
                id TEXT PRIMARY KEY, client_id TEXT NOT NULL DEFAULT '',
                equipment_id TEXT NOT NULL DEFAULT '', report_id TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'Alta',
                status TEXT NOT NULL DEFAULT 'Reportada', reported_at TEXT NOT NULL DEFAULT '',
                resolved_at TEXT NOT NULL DEFAULT '', resolved_by TEXT NOT NULL DEFAULT '',
                resolution_notes TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'sheets', raw_json TEXT NOT NULL DEFAULT '{}',
                sync_status TEXT NOT NULL DEFAULT 'synced', updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS operations_fault_evidence (
                id TEXT PRIMARY KEY, fault_id TEXT NOT NULL, position INTEGER NOT NULL,
                storage_ref TEXT NOT NULL DEFAULT '', drive_ref TEXT NOT NULL DEFAULT '',
                sync_status TEXT NOT NULL DEFAULT 'synced', updated_at TEXT NOT NULL,
                UNIQUE(fault_id, position),
                FOREIGN KEY(fault_id) REFERENCES operations_faults(id)
            )""",
            """CREATE TABLE IF NOT EXISTS operations_users (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '', role TEXT NOT NULL,
                client_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
                permissions_json TEXT NOT NULL DEFAULT '{}', password_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS operations_invites (
                id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', role TEXT NOT NULL,
                client_id TEXT NOT NULL DEFAULT '', permissions_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending', expires_at TEXT NOT NULL,
                used_at TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS operations_tasks (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, details TEXT NOT NULL DEFAULT '',
                scheduled_date TEXT NOT NULL, scheduled_time TEXT NOT NULL DEFAULT '',
                client_id TEXT NOT NULL DEFAULT '', equipment_id TEXT NOT NULL DEFAULT '',
                assigned_user_id TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'Normal',
                status TEXT NOT NULL DEFAULT 'Pendiente', completed_at TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS operations_task_assignees (
                task_id TEXT NOT NULL, user_id TEXT NOT NULL,
                PRIMARY KEY(task_id,user_id),
                FOREIGN KEY(task_id) REFERENCES operations_tasks(id) ON DELETE CASCADE
            )""",
            """CREATE TABLE IF NOT EXISTS operations_expenses (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, technician_name TEXT NOT NULL DEFAULT '',
                client_id TEXT NOT NULL DEFAULT '', equipment_id TEXT NOT NULL DEFAULT '',
                repair_id TEXT NOT NULL DEFAULT '', expense_date TEXT NOT NULL,
                amount REAL NOT NULL DEFAULT 0, category TEXT NOT NULL DEFAULT 'Otro',
                concept TEXT NOT NULL, payment_method TEXT NOT NULL DEFAULT 'Tarjeta propia',
                reimbursable INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'Pendiente',
                admin_notes TEXT NOT NULL DEFAULT '', receipt_ref TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS operations_notice_outbox (
                id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
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
            """CREATE TABLE IF NOT EXISTS operations_observation_options (
                category TEXT NOT NULL, normalized_value TEXT NOT NULL,
                value TEXT NOT NULL, use_count INTEGER NOT NULL DEFAULT 1,
                last_used_at TEXT NOT NULL, PRIMARY KEY(category, normalized_value)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_operations_equipment_client ON operations_equipment(client_id)",
            "CREATE TABLE IF NOT EXISTS operations_worklists (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS operations_worklist_changes (id TEXT PRIMARY KEY, list_id TEXT NOT NULL, actor_id TEXT NOT NULL, saved_at TEXT NOT NULL, payload_json TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_worklist_changes_list ON operations_worklist_changes(list_id,saved_at)",
            "CREATE TABLE IF NOT EXISTS operations_repairs (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, owner_id TEXT NOT NULL, client_key TEXT NOT NULL, equipment_key TEXT NOT NULL, service_date TEXT NOT NULL, state TEXT NOT NULL, payload_json TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_repairs_equipment ON operations_repairs(equipment_key,service_date)",
            "CREATE INDEX IF NOT EXISTS idx_repairs_date ON operations_repairs(service_date,id)",
            "CREATE TABLE IF NOT EXISTS operations_repair_sequence (id INTEGER PRIMARY KEY, next_value INTEGER NOT NULL)",
            "CREATE TABLE IF NOT EXISTS operations_pay_accounts (user_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, name TEXT NOT NULL, plans_json TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS operations_salary_payments (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, week_start TEXT NOT NULL, amount_cents BIGINT NOT NULL, payload_json TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_salary_payments_user ON operations_salary_payments(user_id,week_start)",
            "CREATE TABLE IF NOT EXISTS operations_pay_changes (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL)",
            "INSERT INTO operations_repair_sequence(id,next_value) VALUES (1,0) ON CONFLICT(id) DO NOTHING",
            "CREATE TABLE IF NOT EXISTS operations_repair_changes (id TEXT PRIMARY KEY, repair_id TEXT NOT NULL, actor_id TEXT NOT NULL, saved_at TEXT NOT NULL, payload_json TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_repair_changes ON operations_repair_changes(repair_id,saved_at)",
            "CREATE TABLE IF NOT EXISTS operations_repair_photos (repair_id TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL, drive_ref TEXT NOT NULL, PRIMARY KEY(repair_id,id), FOREIGN KEY(repair_id) REFERENCES operations_repairs(id))",
            "CREATE INDEX IF NOT EXISTS idx_operations_reports_equipment ON operations_reports(equipment_id)",
            "CREATE INDEX IF NOT EXISTS idx_operations_report_presence_seen ON operations_report_presence(report_id,last_seen_at)",
            "CREATE TABLE IF NOT EXISTS operations_report_submissions (draft_id TEXT PRIMARY KEY, report_id TEXT NOT NULL, user_id TEXT NOT NULL, confirmed_at TEXT NOT NULL)",
            "CREATE INDEX IF NOT EXISTS idx_operations_reports_client ON operations_reports(client_id)",
            "CREATE INDEX IF NOT EXISTS idx_operations_faults_client ON operations_faults(client_id)",
            "CREATE INDEX IF NOT EXISTS idx_operations_faults_equipment ON operations_faults(equipment_id)",
            "CREATE INDEX IF NOT EXISTS idx_operations_fault_evidence_fault ON operations_fault_evidence(fault_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_operations_users_email ON operations_users(email) WHERE email <> ''",
            "CREATE INDEX IF NOT EXISTS idx_operations_tasks_date ON operations_tasks(scheduled_date,scheduled_time)",
            "CREATE INDEX IF NOT EXISTS idx_operations_tasks_assignee ON operations_tasks(assigned_user_id)",
            "CREATE INDEX IF NOT EXISTS idx_operations_expenses_user ON operations_expenses(user_id,expense_date)",
            "CREATE INDEX IF NOT EXISTS idx_operations_expenses_status ON operations_expenses(status,expense_date)",
            "CREATE INDEX IF NOT EXISTS idx_operations_outbox_status ON operations_sync_outbox(status)",
            "CREATE INDEX IF NOT EXISTS idx_operations_notice_status ON operations_notice_outbox(status,updated_at)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_operations_clients_matrix_id ON operations_clients(matrix_id) WHERE matrix_id <> ''",
        ]
        with self.connection() as conn:
            for statement in statements:
                conn.execute(statement)
            self._ensure_column(conn, "operations_clients", "raw_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "operations_clients", "sort_order", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "operations_clients", "billing_rfc", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "operations_equipment", "raw_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "operations_faults", "resolved_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "operations_faults", "resolved_by", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "operations_faults", "resolution_notes", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "operations_faults", "deleted_at", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "operations_users", "photo_ref", "TEXT NOT NULL DEFAULT ''")
            added_task_notice_choice = self._ensure_column(conn, "operations_tasks", "notify_client", "INTEGER NOT NULL DEFAULT 0")
            if added_task_notice_choice:
                conn.execute("UPDATE operations_tasks SET notify_client=1 WHERE id LIKE 'VISIT_%' AND status!='Solicitada'")
            self._ensure_column(conn, "operations_tasks", "notice_state", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "operations_reports", "revision", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "operations_evidence", "actor_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "operations_evidence", "actor_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "operations_evidence", "mutation_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "operations_evidence", "created_at", "TEXT NOT NULL DEFAULT ''")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_operations_evidence_mutation ON operations_evidence(report_id,mutation_id) WHERE mutation_id <> ''")
            self._seed_observation_options(conn)
            self._remove_legacy_fault_duplicates(conn)
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
            return True
        return False

    def _upsert_meta(self, conn, key, value):
        p = self.placeholder
        conn.execute(
            f"INSERT INTO operations_meta(key,value,updated_at) VALUES ({p},{p},{p}) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, _text(value), _now()),
        )

    def _seed_observation_options(self, conn):
        """Carga el catálogo inicial sin duplicar opciones aprendidas de reportes reales."""
        p, stamp = self.placeholder, _now()
        for category, values in DEFAULT_OBSERVATION_OPTIONS.items():
            for value in values:
                normalized = _observation_key(value)
                conn.execute(
                    f"INSERT INTO operations_observation_options"
                    f"(category,normalized_value,value,use_count,last_used_at) "
                    f"VALUES ({p},{p},{p},0,{p}) ON CONFLICT(category,normalized_value) DO NOTHING",
                    (category, normalized, value, stamp),
                )

    def _remove_legacy_fault_duplicates(self, conn):
        """Elimina sólo copias provisionales cuando existe la fila real de AppSheet."""
        p = self.placeholder
        if conn.execute(f"SELECT 1 FROM operations_meta WHERE key={p}", ("fault_dedupe_v1",)).fetchone():
            return 0
        rows = conn.execute(
            "SELECT id,client_id,equipment_id,report_id,description,status,reported_at,source "
            "FROM operations_faults"
        ).fetchall()
        groups = {}
        for row in rows:
            if _text(row[7]).casefold() != "sheets":
                continue
            signature = (
                _text(row[1]).casefold(), _text(row[2]).casefold(), _text(row[3]).casefold(),
                " ".join(_text(row[4]).casefold().split()), _text(row[5]).casefold(),
                _text(row[6]).casefold(),
            )
            groups.setdefault(signature, []).append(_text(row[0]))
        removed = 0
        for ids in groups.values():
            real_ids = [value for value in ids if not re.fullmatch(r"FALLA-.+-\d+", value, re.IGNORECASE)]
            provisional_ids = [value for value in ids if re.fullmatch(r"FALLA-.+-\d+", value, re.IGNORECASE)]
            if not real_ids:
                continue
            for fault_id in provisional_ids:
                has_evidence = conn.execute(
                    f"SELECT 1 FROM operations_fault_evidence WHERE fault_id={p} LIMIT 1", (fault_id,),
                ).fetchone()
                if has_evidence:
                    continue
                conn.execute(f"DELETE FROM operations_faults WHERE id={p} AND source='sheets'", (fault_id,))
                removed += 1
        self._upsert_meta(conn, "fault_dedupe_v1", str(removed))
        return removed

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
        # A confirmed export releases the old app-only protection. Pending edits
        # remain authoritative; a stale acknowledgement cannot release a new edit.
        with self.connection() as conn:
            for kind, table in (("client", "operations_clients"), ("equipment", "operations_equipment"),
                                ("report", "operations_reports"), ("fault", "operations_faults")):
                extra = " AND state!='draft'" if kind == "report" else ""
                conn.execute(
                    f"UPDATE {table} SET source='sheets' WHERE source='app'{extra} AND EXISTS ("
                    f"SELECT 1 FROM operations_sync_outbox o WHERE o.entity_type='{kind}' "
                    f"AND o.entity_id={table}.id AND o.destination='sheets' AND o.status='synced' "
                    f"AND o.updated_at>={table}.updated_at AND o.updated_at<{self.placeholder})",
                    (_text(payload.get('_read_started_at')) or stamp,),
                )
            client_map = {}
            for local_id, matrix_id in conn.execute(
                "SELECT id,matrix_id FROM operations_clients WHERE matrix_id!=''"
            ).fetchall():
                previous = client_map.get(matrix_id)
                if not previous or local_id == matrix_id:
                    # Bases heredadas pudieron conservar un UUID local y el ID
                    # real de la matriz. Se prefiere la coincidencia exacta sin
                    # detener la importación del resto de los clientes.
                    client_map[matrix_id] = local_id
            report_map = {}
            for local_id, matrix_id in conn.execute(
                "SELECT id,matrix_id FROM operations_reports WHERE matrix_id!='' AND state!='draft' "
                "ORDER BY updated_at DESC,id"
            ).fetchall():
                previous = report_map.get(matrix_id)
                if not previous:
                    report_map[matrix_id] = local_id
                elif local_id == matrix_id:
                    # Legacy imports sometimes left an app UUID and the real
                    # AppSheet ID pointing at the same matrix folio. Prefer the
                    # exact ID; keep the other record as history instead of
                    # blocking every client/equipment refresh.
                    report_map[matrix_id] = local_id
        payload = dict(payload)
        for collection in ("clients", "equipment", "reports", "faults"):
            transformed = []
            for original in payload.get(collection, []):
                item = dict(original)
                if collection == "clients":
                    item["_matrix_id"] = item.get("id")
                    item["id"] = client_map.get(item.get("id"), item.get("id"))
                if "client_id" in item:
                    item["client_id"] = client_map.get(item["client_id"], item["client_id"])
                if collection == "reports":
                    item["_matrix_id"] = item.get("id")
                    item["id"] = report_map.get(item.get("id"), item.get("id"))
                if item.get("report_id"):
                    item["report_id"] = report_map.get(item["report_id"], item["report_id"])
                transformed.append(item)
            payload[collection] = transformed
        clients = [(
            _text(item.get("id")), _text(item.get("name")) or _text(item.get("id")),
            _text(item.get("address")), _text(item.get("_matrix_id") or item.get("id")), _text(item.get("selected_round")),
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
            int(bool(item.get("completed"))), "sheets", _text(item.get("_matrix_id") or item.get("id")),
            "refrigeration", json.dumps(item.get("_raw") or {}, ensure_ascii=False),
            "completed" if item.get("completed") else "imported",
            "synced", stamp,
        ) for item in payload.get("reports", []) if _text(item.get("id"))]
        faults = [(
            _text(item.get("id")), _text(item.get("client_id")), _text(item.get("equipment_id")),
            _text(item.get("report_id")), _text(item.get("description")),
            _text(item.get("priority")) or "Alta", _text(item.get("status")) or "Reportada",
            _text(item.get("reported_at")), _text(item.get("resolved_at")),
            _text(item.get("resolved_by")), _text(item.get("resolution_notes")), "sheets",
            json.dumps(item.get("_raw") or {}, ensure_ascii=False), "synced", stamp,
        ) for item in payload.get("faults", []) if _text(item.get("id"))]
        with self.connection() as conn:
            self._upsert_many(conn, "operations_clients",
                ["id","name","address","matrix_id","selected_round","policy_active","has_photo","photo_ref","source","raw_json","updated_at"], clients,
                ["name","address","matrix_id","selected_round","has_photo","photo_ref","source","raw_json","updated_at"],
                update_where="operations_clients.source='sheets'")
            self._upsert_many(conn, "operations_equipment",
                ["id","client_id","name","brand","model","serial","status","location","department","equipment_type","notes","has_photo","photo_ref","source","raw_json","updated_at"], equipment,
                ["client_id","name","brand","model","serial","status","location","department","has_photo","photo_ref","source","raw_json","updated_at"],
                update_where="operations_equipment.source='sheets'")
            self._upsert_many(conn, "operations_reports",
                ["id","equipment_id","client_id","round_number","start_at","end_at","completed","source","matrix_id","report_type","payload_json","state","sync_status","updated_at"], reports,
                ["equipment_id","client_id","round_number","start_at","end_at","completed","source","matrix_id","report_type","payload_json","state","sync_status","updated_at"],
                update_where="operations_reports.source='sheets'")
            self._upsert_many(conn, "operations_faults",
                ["id","client_id","equipment_id","report_id","description","priority","status","reported_at","resolved_at","resolved_by","resolution_notes","source","raw_json","sync_status","updated_at"], faults,
                ["client_id","equipment_id","report_id","description","priority","status","reported_at","resolved_at","resolved_by","resolution_notes","source","raw_json","sync_status","updated_at"],
                update_where="operations_faults.source='sheets'")
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
            if int((payload.get("stats") or {}).get("fault_sheets") or 0) > 0:
                self._upsert_meta(conn, "faults_matrix_import", stamp)
        return {"clients": len(clients), "equipment": len(equipment), "reports": len(reports), "faults": len(faults)}

    def has_data(self):
        if not self.enabled:
            return False
        self.initialize()
        with self.connection() as conn:
            return bool(conn.execute("SELECT 1 FROM operations_clients LIMIT 1").fetchone())

    def delete_clients_with_relations(self, client_ids):
        """Elimina clientes concretos y todas sus relaciones de la base operativa."""
        self.initialize()
        targets = sorted({_text(value) for value in client_ids if _text(value)})
        if not targets:
            return {"clients": 0, "equipment": 0, "reports": 0, "faults": 0}
        p = self.placeholder
        markers = ",".join([p] * len(targets))

        def values(conn, sql, params):
            return [str(row[0]) for row in conn.execute(sql, params).fetchall() if _text(row[0])]

        with self.connection() as conn:
            equipment_ids = values(
                conn, f"SELECT id FROM operations_equipment WHERE client_id IN ({markers})", targets,
            )
            equipment_markers = ",".join([p] * len(equipment_ids))
            report_where = f"client_id IN ({markers})"
            report_params = list(targets)
            if equipment_ids:
                report_where += f" OR equipment_id IN ({equipment_markers})"
                report_params.extend(equipment_ids)
            report_ids = values(conn, f"SELECT id FROM operations_reports WHERE {report_where}", report_params)
            report_markers = ",".join([p] * len(report_ids))
            fault_where = f"client_id IN ({markers})"
            fault_params = list(targets)
            if equipment_ids:
                fault_where += f" OR equipment_id IN ({equipment_markers})"
                fault_params.extend(equipment_ids)
            if report_ids:
                fault_where += f" OR report_id IN ({report_markers})"
                fault_params.extend(report_ids)
            fault_ids = values(conn, f"SELECT id FROM operations_faults WHERE {fault_where}", fault_params)

            if fault_ids:
                fault_markers = ",".join([p] * len(fault_ids))
                conn.execute(f"DELETE FROM operations_fault_evidence WHERE fault_id IN ({fault_markers})", fault_ids)
                conn.execute(f"DELETE FROM operations_faults WHERE id IN ({fault_markers})", fault_ids)
            if report_ids:
                conn.execute(f"DELETE FROM operations_evidence WHERE report_id IN ({report_markers})", report_ids)
                conn.execute(f"DELETE FROM operations_reports WHERE id IN ({report_markers})", report_ids)
            relation_where = f"client_id IN ({markers})"
            relation_params = list(targets)
            if equipment_ids:
                relation_where += f" OR equipment_id IN ({equipment_markers})"
                relation_params.extend(equipment_ids)
            conn.execute(f"DELETE FROM operations_tasks WHERE {relation_where}", relation_params)
            conn.execute(f"DELETE FROM operations_expenses WHERE {relation_where}", relation_params)
            conn.execute(f"DELETE FROM operations_invites WHERE client_id IN ({markers})", targets)
            conn.execute(f"DELETE FROM operations_users WHERE client_id IN ({markers})", targets)

            entity_ids = targets + equipment_ids + report_ids + fault_ids
            if entity_ids:
                entity_markers = ",".join([p] * len(entity_ids))
                conn.execute(f"DELETE FROM operations_sync_outbox WHERE entity_id IN ({entity_markers})", entity_ids)
                conn.execute(f"DELETE FROM operations_media_cache WHERE record_id IN ({entity_markers})", entity_ids)
            if equipment_ids:
                conn.execute(f"DELETE FROM operations_equipment WHERE id IN ({equipment_markers})", equipment_ids)
            conn.execute(f"DELETE FROM operations_clients WHERE id IN ({markers})", targets)
        return {
            "clients": len(targets), "equipment": len(equipment_ids),
            "reports": len(report_ids), "faults": len(fault_ids),
        }

    def snapshot(self):
        self.initialize()
        with self.connection() as conn:
            clients = conn.execute(
                "SELECT id,name,address,matrix_id,billing_rfc,selected_round,sort_order,policy_active,has_photo,photo_ref "
                "FROM operations_clients ORDER BY CASE WHEN sort_order>0 THEN 0 ELSE 1 END,sort_order,name,id"
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
            faults = conn.execute(
                "SELECT f.id,f.client_id,f.equipment_id,f.report_id,f.description,f.priority,f.status,f.reported_at,f.resolved_at,f.resolved_by,f.resolution_notes,f.source,f.sync_status,"
                "(SELECT COUNT(*) FROM operations_fault_evidence e WHERE e.fault_id=f.id) "
                "FROM operations_faults f WHERE f.deleted_at='' ORDER BY f.reported_at DESC,f.id DESC"
            ).fetchall()
            tasks = conn.execute(
                "SELECT id,title,details,scheduled_date,scheduled_time,client_id,equipment_id,"
                "assigned_user_id,priority,status,completed_at,created_by,created_at,updated_at,notify_client "
                "FROM operations_tasks ORDER BY scheduled_date,scheduled_time,id"
            ).fetchall()
            task_assignees = {}
            for task_id, user_id in conn.execute("SELECT task_id,user_id FROM operations_task_assignees ORDER BY user_id").fetchall():
                task_assignees.setdefault(task_id, []).append(user_id)
            expenses = conn.execute(
                "SELECT id,user_id,technician_name,client_id,equipment_id,repair_id,expense_date,amount,"
                "category,concept,payment_method,reimbursable,status,admin_notes,receipt_ref,created_by,created_at,updated_at "
                "FROM operations_expenses ORDER BY expense_date DESC,created_at DESC,id DESC"
            ).fetchall()
            meta_rows = conn.execute("SELECT key,value FROM operations_meta").fetchall()
            worklists = [json.loads(row[0]) for row in conn.execute("SELECT payload_json FROM operations_worklists").fetchall()]
            pending = conn.execute("SELECT COUNT(*) FROM operations_sync_outbox WHERE status!='synced'").fetchone()[0]
            cached_thumbnails = conn.execute("SELECT COUNT(*) FROM operations_media_cache").fetchone()[0]
            observation_rows = conn.execute(
                "SELECT category,value,use_count,last_used_at FROM operations_observation_options "
                "ORDER BY category,use_count DESC,last_used_at DESC,value"
            ).fetchall()
        result_clients = [{
            "id": row[0], "name": row[1], "address": row[2], "matrix_id": row[3],
            "billing_rfc": row[4], "selected_round": row[5], "sort_order": int(row[6] or 0),
            "policy_active": bool(row[7]), "has_photo": bool(row[8]), "_photo_ref": row[9],
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
        result_faults = [{
            "id": row[0], "client_id": row[1], "equipment_id": row[2], "report_id": row[3],
            "description": row[4], "priority": row[5], "status": row[6], "reported_at": row[7],
            "resolved_at": row[8], "resolved_by": row[9], "resolution_notes": row[10],
            "source": row[11], "sync_status": row[12],
            "photo_count": int(row[13] or 0),
        } for row in faults]
        result_tasks = [{
            "id": row[0], "title": row[1], "details": row[2], "scheduled_date": row[3],
            "scheduled_time": row[4], "client_id": row[5], "equipment_id": row[6],
            "assigned_user_id": row[7], "priority": row[8], "status": row[9],
            "assigned_user_ids": task_assignees.get(row[0]) or ([row[7]] if row[7] else []),
            "completed_at": row[10], "created_by": row[11], "created_at": row[12],
            "updated_at": row[13], "notify_client": bool(row[14]),
        } for row in tasks]
        result_expenses = [{
            "id": row[0], "user_id": row[1], "technician_name": row[2], "client_id": row[3],
            "equipment_id": row[4], "repair_id": row[5], "expense_date": row[6],
            "amount": float(row[7] or 0), "category": row[8], "concept": row[9],
            "payment_method": row[10], "reimbursable": bool(row[11]), "status": row[12],
            "admin_notes": row[13], "has_receipt": bool(row[14]), "created_by": row[15],
            "created_at": row[16], "updated_at": row[17],
        } for row in expenses]
        client_ids = {item["id"] for item in result_clients}
        equipment_ids = {item["id"] for item in result_equipment}
        return {
            "clients": result_clients, "equipment": result_equipment, "reports": result_reports,
            "faults": result_faults, "tasks": result_tasks, "expenses": result_expenses,
            "worklists": worklists,
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
                "faults": len(result_faults),
                "tasks": len(result_tasks),
                "expenses": len(result_expenses),
                "pending_expenses": sum(item["status"] == "Pendiente" for item in result_expenses),
            },
            "observation_options": [{"category": row[0], "value": row[1], "use_count": int(row[2] or 0),
                                     "last_used_at": row[3]} for row in observation_rows],
            "meta": dict(meta_rows),
        }

    def save_worklist(self, body, *, actor_id, is_admin=False):
        from operation_worklists import save_worklist
        return save_worklist(self, body, actor_id=actor_id, is_admin=is_admin)

    def _remember_observations(self, conn, payload):
        p, stamp = self.placeholder, _now()
        for category in ("electrico", "electronico", "mecanico", "notas"):
            raw = _text((payload or {}).get(category))
            for value in (line.strip(" •-\t") for line in raw.splitlines()):
                if not value:
                    continue
                normalized = _observation_key(value)
                conn.execute(
                    f"INSERT INTO operations_observation_options(category,normalized_value,value,use_count,last_used_at) "
                    f"VALUES ({p},{p},{p},1,{p}) ON CONFLICT(category,normalized_value) DO UPDATE SET "
                    "value=excluded.value,use_count=operations_observation_options.use_count+1,last_used_at=excluded.last_used_at",
                    (category, normalized, value[:500], stamp),
                )

    def status(self):
        if not self.enabled:
            return {"enabled": False, "engine": None, "has_data": False}
        self.initialize()
        snapshot = self.snapshot()
        return {
            "enabled": True, "engine": "postgresql" if self.dialect == "postgres" else "sqlite-local",
            "has_data": bool(snapshot["clients"]), "stats": snapshot["stats"],
            "last_matrix_import": snapshot.get("meta", {}).get("last_matrix_import"),
            "matrix_duplicate_policy": "first_wins",
        }

    def set_matrix_duplicate_policy(self, ignore_duplicates=False):
        """Conserva compatibilidad; la operación continua es la única política."""
        self.initialize()
        value = "first_wins"
        with self.connection() as conn:
            self._upsert_meta(conn, "matrix_duplicate_policy", value)
        return value

    def matrix_duplicates_allowed(self):
        # Compatibilidad con llamadas anteriores: la operación nunca se bloquea
        # globalmente por una fila duplicada.
        return True

    def get_media_ref(self, kind, record_id):
        """Obtiene la referencia original sin depender del caché en memoria del proceso."""
        self.initialize()
        table = {"client": "operations_clients", "equipment": "operations_equipment", "user": "operations_users"}.get(kind)
        if not table:
            return ""
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
                f"INSERT INTO operations_clients(id,name,address,matrix_id,billing_rfc,selected_round,policy_active,has_photo,photo_ref,source,raw_json,updated_at) "
                f"VALUES ({','.join([p] * 12)}) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name,address=excluded.address,matrix_id=excluded.matrix_id,"
                "billing_rfc=excluded.billing_rfc,selected_round=excluded.selected_round,"
                "policy_active=excluded.policy_active,source='app',updated_at=excluded.updated_at",
                (client_id, name, _text(item.get("address")), matrix_id,
                 _text(item.get("billing_rfc")).upper(),
                 _text(item.get("selected_round")), int(bool(item.get("policy_active", True))),
                 0, "", "app", "{}", stamp),
            )
        self.queue_sync("client", client_id, "sheets", "upsert", {"matrix_id": matrix_id})
        return next(client for client in self.snapshot()["clients"] if client["id"] == client_id)

    def reorder_clients(self, client_ids):
        """Guarda una prioridad explícita sin alterar nombres ni claves de la matriz."""
        self.initialize()
        ordered = []
        seen = set()
        for value in client_ids or []:
            client_id = _text(value)
            if client_id and client_id not in seen:
                ordered.append(client_id)
                seen.add(client_id)
        if not ordered:
            raise ValueError("Envía por lo menos un cliente para ordenar.")
        p = self.placeholder
        with self.connection() as conn:
            existing = {row[0] for row in conn.execute("SELECT id FROM operations_clients").fetchall()}
            if any(client_id not in existing for client_id in ordered):
                raise ValueError("La lista contiene un cliente que ya no existe.")
            for position, client_id in enumerate(ordered, start=1):
                conn.execute(
                    f"UPDATE operations_clients SET sort_order={p},updated_at={p} WHERE id={p}",
                    (position, _now(), client_id),
                )
        return ordered

    def set_client_round(self, client_id, round_number):
        """Recuerda la ronda operativa sin modificar los demás datos del cliente."""
        self.initialize()
        client_id = _text(client_id)
        round_number = _text(round_number)
        if round_number not in {"1", "2", "3", "4"}:
            raise ValueError("La ronda debe estar entre R1 y R4.")
        p = self.placeholder
        with self.connection() as conn:
            cursor = conn.execute(
                f"UPDATE operations_clients SET selected_round={p},updated_at={p} WHERE id={p}",
                (round_number, _now(), client_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("El cliente ya no existe.")
        return next(client for client in self.snapshot()["clients"] if client["id"] == client_id)

    def save_equipment(self, items):
        """Guarda uno o varios equipos como una sola operación."""
        self.initialize()
        rows = list(items or [])
        if not rows or len(rows) > 100:
            raise ValueError("Debes enviar entre 1 y 100 equipos.")
        snapshot = self.snapshot()
        client_ids = {client["id"] for client in snapshot["clients"]}
        existing_clients = {row["id"]: row["client_id"] for row in snapshot["equipment"]}
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
            if equipment_id in existing_clients and existing_clients[equipment_id] != client_id:
                raise ValueError("Ese ID_Equipo ya pertenece a otro cliente; no se reasignó.")
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

    def save_equipment_photo(self, equipment_id, photo_ref):
        return self.save_entity_photo("equipment", equipment_id, photo_ref)

    def save_entity_photo(self, kind, equipment_id, photo_ref):
        self.initialize()
        table = {"equipment": "operations_equipment", "client": "operations_clients"}.get(kind)
        if not table:
            raise ValueError("Tipo de fotografía inválido.")
        p = self.placeholder
        with self.connection() as conn:
            result = conn.execute(
                f"UPDATE {table} SET photo_ref={p},has_photo={p},source='app',updated_at={p} WHERE id={p}",
                (_text(photo_ref), int(bool(photo_ref)), _now(), _text(equipment_id)),
            )
            if result.rowcount != 1:
                raise ValueError("El ID no existe.")
        self.queue_sync(kind, equipment_id, "sheets", "upsert")

    def save_report_draft(self, item):
        """Guarda un borrador compartido sin reemplazar cambios ajenos por accidente."""
        self.initialize()
        equipment_id = _text(item.get("equipment_id"))
        client_id = _text(item.get("client_id"))
        round_number = _text(item.get("round"))
        payload = dict(item.get("payload")) if isinstance(item.get("payload"), dict) else {}
        changed_fields = item.get("changed_fields")
        changed_fields = dict(changed_fields) if isinstance(changed_fields, dict) else None
        if not equipment_id or not client_id or round_number not in {"1", "2", "3", "4"}:
            raise ValueError("Cliente, equipo y ronda son obligatorios.")
        equipment = next((row for row in self.snapshot()["equipment"] if row["id"] == equipment_id), None)
        if not equipment or equipment["client_id"] != client_id:
            raise ValueError("El equipo no pertenece al cliente seleccionado.")
        p = self.placeholder
        report_id = _text(item.get("id"))
        edit_report_id = _text(item.get("edit_report_id") or payload.get("_edit_report_id"))
        matrix_id = f"{equipment_id}_R {round_number}"
        with self.connection() as conn:
            # Un solo borrador compartido por equipo/ronda. El bloqueo evita que
            # dos teléfonos creen borradores distintos al abrir al mismo tiempo.
            if self.dialect == "sqlite":
                conn.execute("BEGIN IMMEDIATE")
            else:
                conn.execute(
                    f"SELECT id FROM operations_equipment WHERE id={p} FOR UPDATE",
                    (equipment_id,),
                ).fetchone()
            if edit_report_id:
                target = conn.execute(
                    f"SELECT id FROM operations_reports WHERE id={p} AND equipment_id={p} "
                    f"AND client_id={p} AND round_number={p} AND completed=1",
                    (edit_report_id, equipment_id, client_id, round_number),
                ).fetchone()
                if not target:
                    raise ValueError("El reporte que intentas editar ya no existe.")
                payload["_edit_report_id"] = edit_report_id
            if report_id:
                found = conn.execute(
                    f"SELECT id,equipment_id,client_id,round_number,payload_json,revision FROM operations_reports WHERE id={p} AND source='app' AND state='draft'",
                    (report_id,),
                ).fetchone()
                if not found:
                    raise ValueError("El borrador indicado ya no existe o ya fue finalizado.")
                if found[1] != equipment_id or found[2] != client_id or found[3] != round_number:
                    raise ValueError("Ese borrador pertenece a otro equipo o a otra ronda.")
            else:
                candidates = conn.execute(
                    f"SELECT id,payload_json,revision FROM operations_reports WHERE equipment_id={p} AND round_number={p} "
                    "AND source='app' AND state='draft' ORDER BY updated_at DESC",
                    (equipment_id, round_number),
                ).fetchall()
                found = candidates[0] if candidates else None
                report_id = found[0] if found else f"RPT_{uuid.uuid4().hex[:16].upper()}"
            if found:
                try:
                    current_payload = json.loads(found[4] if len(found) > 4 else found[1] or "{}")
                except (TypeError, json.JSONDecodeError):
                    current_payload = {}
                if changed_fields is not None:
                    current_payload.update(changed_fields)
                    # Conserva quién inició el borrador; el autor de cada foto se
                    # guarda por separado y no depende de este dato histórico.
                    current_payload.setdefault("_draft_user_id", payload.get("_draft_user_id", ""))
                    payload = current_payload
            stamp = _now()
            saved = conn.execute(
                f"INSERT INTO operations_reports(id,equipment_id,client_id,round_number,start_at,end_at,completed,source,matrix_id,report_type,payload_json,state,sync_status,updated_at) "
                f"VALUES ({','.join([p] * 14)}) ON CONFLICT(id) DO UPDATE SET "
                "equipment_id=excluded.equipment_id,client_id=excluded.client_id,round_number=excluded.round_number,"
                "start_at=excluded.start_at,end_at=excluded.end_at,matrix_id=excluded.matrix_id,report_type=excluded.report_type,"
                "payload_json=excluded.payload_json,state='draft',sync_status='local_only',"
                "revision=operations_reports.revision+1,updated_at=excluded.updated_at "
                "WHERE operations_reports.state='draft' AND operations_reports.completed=0",
                (report_id, equipment_id, client_id, round_number, _text(payload.get("inicio")),
                 _text(payload.get("fin")), 0, "app", matrix_id, _text(item.get("report_type")) or "refrigeration",
                 json.dumps(payload, ensure_ascii=False), "draft", "local_only", stamp),
            )
            if saved.rowcount != 1:
                raise ValueError("El reporte ya fue finalizado; no se sobrescribió con un borrador tardío.")
        return self.get_report_draft(equipment_id, round_number)

    def ensure_report_fault(self, report_id, *, client_id, equipment_id, description, priority="Alta"):
        """Crea o actualiza la falla grave vinculada al reporte de forma idempotente."""
        report_id, description = _text(report_id), _text(description)
        if not report_id or not description:
            return None
        fault_id = f"FALLA_REPORTE_{re.sub(r'[^A-Za-z0-9_-]+', '_', report_id)[:80]}"
        p, stamp = self.placeholder, _now()
        with self.connection() as conn:
            conn.execute(
                f"INSERT INTO operations_faults"
                f"(id,client_id,equipment_id,report_id,description,priority,status,reported_at,source,raw_json,sync_status,updated_at) "
                f"VALUES ({','.join([p] * 12)}) ON CONFLICT(id) DO UPDATE SET "
                "client_id=excluded.client_id,equipment_id=excluded.equipment_id,report_id=excluded.report_id,"
                "description=excluded.description,priority=excluded.priority,sync_status='pending',updated_at=excluded.updated_at",
                (fault_id, _text(client_id), _text(equipment_id), report_id, description,
                 _text(priority) or "Alta", "Reportada", stamp, "app", "{}", "pending", stamp),
            )
        self.queue_sync("fault", fault_id, "sheets", "upsert", {
            "client_id": _text(client_id), "equipment_id": _text(equipment_id), "report_id": report_id,
        })
        return next(row for row in self.snapshot()["faults"] if row["id"] == fault_id)

    def save_fault(self, item):
        """Registra una falla del cliente, de catálogo o de un equipo fuera de póliza."""
        self.initialize()
        client_id = _text(item.get("client_id"))
        equipment_id = _text(item.get("equipment_id"))
        description = _text(item.get("description"))
        if not client_id or not description:
            raise ValueError("Cliente y descripción son obligatorios.")
        snapshot = self.snapshot()
        if not any(row['id']==client_id for row in snapshot['clients']):
            raise ValueError('El cliente no existe.')
        external = item.get('outside_policy') is True
        if external:
            name, location = _text(item.get('equipment_name')), _text(item.get('equipment_location'))
            if not name or not location or len(name)>200 or len(location)>300:
                raise ValueError('Escribe el nombre del equipo (máximo 200 caracteres) y su ubicación (máximo 300).')
            if equipment_id or item.get('report_id'):
                raise ValueError('Un equipo fuera de catálogo no debe vincularse a otro equipo o reporte.')
            description = f'Fuera de póliza · Equipo: {name} · Ubicación: {location}\n{description}'
        else:
            equipment = next((row for row in snapshot['equipment'] if row['id']==equipment_id),None)
            if not equipment or equipment['client_id']!=client_id:
                raise ValueError('El equipo no pertenece al cliente seleccionado.')
        fault_id = _text(item.get("id")) or f"FALLA_{uuid.uuid4().hex[:16].upper()}"
        existing=next((row for row in self.snapshot()['faults'] if row['id']==fault_id),None)
        if existing:
            if existing['client_id']!=client_id or existing['equipment_id']!=equipment_id or existing['description']!=description:
                raise ValueError('El identificador de falla ya corresponde a otro registro.')
            return existing
        stamp = _now()
        reported_at = _text(item.get("reported_at")) or stamp
        source = _text(item.get("source")).casefold()
        if source not in {"client", "technician", "admin"}:
            source = "app"
        p = self.placeholder
        with self.connection() as conn:
            conn.execute(
                f"INSERT INTO operations_faults(id,client_id,equipment_id,report_id,description,priority,status,reported_at,source,raw_json,sync_status,updated_at) "
                f"VALUES ({','.join([p] * 12)}) ON CONFLICT(id) DO NOTHING",
                (fault_id, client_id, equipment_id, _text(item.get("report_id")), description,
                 _text(item.get("priority")) or "Alta", "Reportada", reported_at,
                 source, "{}", "pending", stamp),
            )
        self.queue_sync("fault", fault_id, "sheets", "upsert", {
            "client_id": client_id, "equipment_id": equipment_id,
        })
        return next(row for row in self.snapshot()["faults"] if row["id"] == fault_id)

    def save_fault_evidence(self, fault_id, position, drive_ref, *, storage_ref=""):
        """Registra hasta tres fotografías relacionadas con una falla."""
        self.initialize()
        fault_id, drive_ref = _text(fault_id), _text(drive_ref)
        storage_ref = _text(storage_ref) or drive_ref
        position = int(position or 0)
        if position not in range(1, 4) or not drive_ref:
            raise ValueError("La posición y la fotografía de la falla no son válidas.")
        p, stamp = self.placeholder, _now()
        with self.connection() as conn:
            if not conn.execute(f"SELECT 1 FROM operations_faults WHERE id={p}", (fault_id,)).fetchone():
                raise ValueError("La falla ya no existe.")
            evidence_id = f"{fault_id}:foto:{position}"
            conn.execute(
                f"INSERT INTO operations_fault_evidence(id,fault_id,position,storage_ref,drive_ref,sync_status,updated_at) "
                f"VALUES ({','.join([p] * 7)}) ON CONFLICT(fault_id,position) DO UPDATE SET "
                "storage_ref=excluded.storage_ref,drive_ref=excluded.drive_ref,sync_status='pending',updated_at=excluded.updated_at",
                (evidence_id, fault_id, position, storage_ref, drive_ref, "pending", stamp),
            )
        self.queue_sync("fault", fault_id, "sheets", "upsert")
        return {"position": position, "storage_ref": storage_ref, "drive_ref": drive_ref}

    def get_fault_evidence(self, fault_id):
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT position,storage_ref,drive_ref FROM operations_fault_evidence "
                f"WHERE fault_id={p} ORDER BY position", (_text(fault_id),),
            ).fetchall()
        return [{"position": int(row[0]), "storage_ref": _text(row[1]), "drive_ref": _text(row[2])}
                for row in rows]

    def get_fault_evidence_ref(self, fault_id, position):
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT f.client_id,e.storage_ref,e.drive_ref FROM operations_fault_evidence e "
                f"JOIN operations_faults f ON f.id=e.fault_id WHERE e.fault_id={p} AND e.position={p}",
                (_text(fault_id), int(position)),
            ).fetchone()
        return {"client_id": row[0], "photo_ref": _text(row[2]) or _text(row[1])} if row else None

    def save_task(self, item, *, _completing_existing=False):
        """Crea o edita un pendiente de agenda conservando su ID."""
        self.initialize()
        title, scheduled_date = _text(item.get("title")), _text(item.get("scheduled_date"))
        if not title or not _valid_date(scheduled_date):
            raise ValueError("Actividad y fecha son obligatorias.")
        task_id = _text(item.get("id")) or f"TASK_{uuid.uuid4().hex[:16].upper()}"
        assigned = item.get('assigned_user_ids')
        if assigned is None:
            assigned = [_text(item.get('assigned_user_id'))] if item.get('assigned_user_id') else []
        if not isinstance(assigned, list) or any(not isinstance(value, str) for value in assigned):
            raise ValueError('Selecciona los técnicos responsables.')
        assigned = list(dict.fromkeys(value.strip() for value in assigned if value.strip()))
        if 'assigned_user_ids' in item and not assigned:
            raise ValueError('Selecciona al menos un responsable.')
        for user_id in assigned if 'assigned_user_ids' in item and not _completing_existing else []:
            user = self.get_user_by_id(user_id) if user_id != 'owner' else {'status':'active','role':'admin'}
            if not user or user.get('status') != 'active' or user.get('role') not in {'admin','technician'}:
                raise ValueError('Uno de los responsables no es un técnico activo.')
        status = _text(item.get("status")) or "Pendiente"
        if status not in {"Solicitada", "Pendiente", "En curso", "Terminada", "Cancelada"}:
            raise ValueError("El estado de la tarea no es válido.")
        stamp, p = _now(), self.placeholder
        with self.connection() as conn:
            existing = conn.execute(f"SELECT created_at,notify_client,notice_state FROM operations_tasks WHERE id={p}", (task_id,)).fetchone()
            created_at = existing[0] if existing else stamp
            notify_client = bool(item.get('notify_client', bool(existing[1]) if existing else False))
            before = json.loads(existing[2]) if existing and existing[2] else ({'legacy': True} if existing else {})
            after = {key: _text(item.get(key)) for key in ('title','details','scheduled_date','scheduled_time','client_id','equipment_id')}
            after.update(id=task_id, status=status, assigned_user_ids=sorted(assigned), notify_client=notify_client,
                         priority=_text(item.get('priority')) or 'Normal')
            conn.execute(
                f"INSERT INTO operations_tasks(id,title,details,scheduled_date,scheduled_time,client_id,equipment_id,assigned_user_id,priority,status,completed_at,created_by,created_at,updated_at) "
                f"VALUES ({','.join([p] * 14)}) ON CONFLICT(id) DO UPDATE SET "
                "title=excluded.title,details=excluded.details,scheduled_date=excluded.scheduled_date,"
                "scheduled_time=excluded.scheduled_time,client_id=excluded.client_id,equipment_id=excluded.equipment_id,"
                "assigned_user_id=excluded.assigned_user_id,priority=excluded.priority,status=excluded.status,completed_at=excluded.completed_at,updated_at=excluded.updated_at",
                (task_id, title, _text(item.get("details")), scheduled_date, _text(item.get("scheduled_time")),
                 _text(item.get("client_id")), _text(item.get("equipment_id")), assigned[0] if assigned else '',
                 _text(item.get("priority")) or "Normal", status, _text(item.get("completed_at")),
                 _text(item.get("created_by")), created_at, stamp),
            )
            conn.execute(f"DELETE FROM operations_task_assignees WHERE task_id={p}", (task_id,))
            for user_id in assigned:
                conn.execute(f"INSERT INTO operations_task_assignees(task_id,user_id) VALUES ({p},{p})", (task_id,user_id))
            conn.execute(f"UPDATE operations_tasks SET notify_client={p},notice_state={p} WHERE id={p}",
                         (int(notify_client), json.dumps(after, ensure_ascii=False), task_id))
            if before != after:
                self._queue_notice_event(conn, {'kind':'task', 'before':before, 'after':after,
                    'actor_id':_text(item.get('actor_id') or item.get('created_by'))}, stamp)
        return next(task for task in self.snapshot()["tasks"] if task["id"] == task_id)

    def _queue_notice_event(self, conn, payload, stamp):
        p = self.placeholder
        conn.execute(f"INSERT INTO operations_notice_outbox(id,payload_json,created_at,updated_at) VALUES ({p},{p},{p},{p})",
                     (uuid.uuid4().hex, json.dumps(payload, ensure_ascii=False), stamp, stamp))

    def pending_notice_events(self, limit=25):
        self.initialize()
        with self.connection() as conn:
            rows = conn.execute(f"SELECT id,payload_json FROM operations_notice_outbox WHERE status='pending' ORDER BY updated_at,id LIMIT {self.placeholder}", (limit,)).fetchall()
        return [{'id':row[0], 'payload':json.loads(row[1])} for row in rows]

    def finish_notice_event(self, event_id, error=''):
        p = self.placeholder
        with self.connection() as conn:
            conn.execute(f"UPDATE operations_notice_outbox SET status={p},attempts=attempts+1,last_error={p},updated_at={p} WHERE id={p}",
                         ('pending' if error else 'sent', str(error)[:500], _now(), event_id))

    def tasks_for_reminders(self, scheduled_date):
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            rows = conn.execute(f"SELECT id,title,scheduled_time,client_id,assigned_user_id,created_by,notify_client FROM operations_tasks WHERE scheduled_date={p} AND status IN ('Pendiente','En curso') ORDER BY scheduled_time,id", (scheduled_date,)).fetchall()
            assignments = conn.execute(f"SELECT a.task_id,a.user_id FROM operations_task_assignees a JOIN operations_tasks t ON t.id=a.task_id WHERE t.scheduled_date={p}", (scheduled_date,)).fetchall()
        users = {}
        for task_id,user_id in assignments:
            users.setdefault(task_id, []).append(user_id)
        return [{'id':r[0],'title':r[1],'scheduled_time':r[2],'client_id':r[3],
                 'assigned_user_ids':users.get(r[0]) or ([r[4]] if r[4] else []),'created_by':r[5], 'notify_client':bool(r[6])} for r in rows]

    def complete_task(self, task_id, actor_id=''):
        task = next((t for t in self.snapshot()['tasks'] if t['id'] == _text(task_id)), None)
        if not task or task['status'] == 'Terminada':
            return task
        task.update(status='Terminada', completed_at=_now(), actor_id=actor_id)
        return self.save_task(task, _completing_existing=True)

    def save_expense(self, item):
        """Registra un gasto de campo conservando responsable y vínculos operativos."""
        self.initialize()
        user_id = _text(item.get("user_id"))
        concept = _text(item.get("concept"))
        expense_date = _text(item.get("expense_date"))
        try:
            amount = round(float(item.get("amount") or 0), 2)
        except (TypeError, ValueError):
            amount = 0
        if not user_id or not concept or not _valid_date(expense_date) or amount <= 0:
            raise ValueError("Técnico, concepto, fecha e importe mayor a cero son obligatorios.")
        expense_id = _text(item.get("id")) or f"GASTO_{uuid.uuid4().hex[:16].upper()}"
        status = _text(item.get("status")) or "Pendiente"
        if status not in {"Pendiente", "Aprobado", "Rechazado", "Reembolsado", "Liquidado"}:
            raise ValueError("El estado del gasto no es válido.")
        client_id, equipment_id = _text(item.get("client_id")), _text(item.get("equipment_id"))
        if equipment_id:
            equipment = next((row for row in self.snapshot()["equipment"] if row["id"] == equipment_id), None)
            if not equipment or (client_id and equipment["client_id"] != client_id):
                raise ValueError("El equipo no pertenece al cliente seleccionado.")
            client_id = equipment["client_id"]
        p, stamp = self.placeholder, _now()
        with self.connection() as conn:
            existing = conn.execute(f"SELECT created_at,receipt_ref,user_id FROM operations_expenses WHERE id={p}", (expense_id,)).fetchone()
            if existing and existing[2] != user_id:
                raise ValueError('Ese gasto pertenece a otra cuenta.')
            created_at, receipt_ref = (existing[0], existing[1]) if existing else (stamp, "")
            conn.execute(
                f"INSERT INTO operations_expenses(id,user_id,technician_name,client_id,equipment_id,repair_id,expense_date,amount,category,concept,payment_method,reimbursable,status,admin_notes,receipt_ref,created_by,created_at,updated_at) "
                f"VALUES ({','.join([p] * 18)}) ON CONFLICT(id) DO UPDATE SET "
                "client_id=excluded.client_id,equipment_id=excluded.equipment_id,repair_id=excluded.repair_id,"
                "expense_date=excluded.expense_date,amount=excluded.amount,category=excluded.category,concept=excluded.concept,"
                "payment_method=excluded.payment_method,reimbursable=excluded.reimbursable,updated_at=excluded.updated_at",
                (expense_id, user_id, _text(item.get("technician_name")), client_id, equipment_id,
                 _text(item.get("repair_id")), expense_date, amount, _text(item.get("category")) or "Otro",
                 concept, _text(item.get("payment_method")) or "Tarjeta propia",
                 1 if item.get("reimbursable", True) else 0, status, _text(item.get("admin_notes")),
                 receipt_ref, _text(item.get("created_by")), created_at, stamp),
            )
            if not existing:
                self._queue_notice_event(conn, {'kind':'expense_created','actor_id':_text(item.get('created_by')),
                    'after':{'id':expense_id,'user_id':user_id,'technician_name':_text(item.get('technician_name')),
                             'concept':concept,'amount':amount,'status':status}}, stamp)
        return next(row for row in self.snapshot()["expenses"] if row["id"] == expense_id)

    def save_expense_receipt(self, expense_id, drive_ref):
        self.initialize()
        p, stamp = self.placeholder, _now()
        with self.connection() as conn:
            cursor = conn.execute(
                f"UPDATE operations_expenses SET receipt_ref={p},updated_at={p} WHERE id={p}",
                (_text(drive_ref), stamp, _text(expense_id)),
            )
            if not cursor.rowcount:
                return None
        return next(row for row in self.snapshot()["expenses"] if row["id"] == _text(expense_id))

    def get_expense_receipt_ref(self, expense_id):
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT user_id,receipt_ref FROM operations_expenses WHERE id={p}", (_text(expense_id),)
            ).fetchone()
        return {"user_id": row[0], "photo_ref": _text(row[1])} if row and _text(row[1]) else None

    def update_expense_status(self, expense_id, status, *, admin_notes="", actor_id='owner'):
        self.initialize()
        status = _text(status)
        if status not in {"Pendiente", "Aprobado", "Rechazado", "Reembolsado", "Liquidado"}:
            raise ValueError("El estado del gasto no es válido.")
        p, stamp = self.placeholder, _now()
        with self.connection() as conn:
            previous = conn.execute(f"SELECT user_id,concept,amount,status,admin_notes FROM operations_expenses WHERE id={p}", (_text(expense_id),)).fetchone()
            if not previous:
                return None
            cursor = conn.execute(
                f"UPDATE operations_expenses SET status={p},admin_notes={p},updated_at={p} WHERE id={p}",
                (status, _text(admin_notes), stamp, _text(expense_id)),
            )
            if not cursor.rowcount:
                return None
            if previous[3] != status or previous[4] != _text(admin_notes):
                self._queue_notice_event(conn, {'kind':'expense_status','actor_id':actor_id,
                    'after':{'id':expense_id,'user_id':previous[0],'concept':previous[1],'amount':previous[2],'status':status}}, stamp)
        return next(row for row in self.snapshot()["expenses"] if row["id"] == _text(expense_id))

    def delete_expense(self, expense_id):
        """Elimina un gasto desde administración; no afecta ningún otro registro operativo."""
        self.initialize()
        expense_id, p = _text(expense_id), self.placeholder
        with self.connection() as conn:
            found = conn.execute(
                f"SELECT id FROM operations_expenses WHERE id={p}", (expense_id,)
            ).fetchone()
            if not found:
                return False
            conn.execute(
                f"DELETE FROM operations_media_cache WHERE kind='expense' AND record_id={p}",
                (expense_id,),
            )
            conn.execute(f"DELETE FROM operations_expenses WHERE id={p}", (expense_id,))
        return True

    def list_users(self):
        self.initialize()
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT id,name,email,phone,role,client_id,status,permissions_json,created_at,updated_at,photo_ref "
                "FROM operations_users ORDER BY name,id"
            ).fetchall()
        result = []
        for row in rows:
            try:
                permissions = json.loads(row[7] or "{}")
            except (TypeError, json.JSONDecodeError):
                permissions = {}
            result.append({"id": row[0], "name": row[1], "email": row[2], "phone": row[3],
                           "role": row[4], "client_id": row[5], "status": row[6],
                           "permissions": permissions, "created_at": row[8], "updated_at": row[9],
                           "has_photo": bool(row[10]), "photo_ref": row[10]})
        return result

    def get_user_by_email(self, email):
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT id,name,email,phone,role,client_id,status,permissions_json,password_hash "
                f"FROM operations_users WHERE LOWER(email)=LOWER({p})", (_text(email),),
            ).fetchone()
        if not row:
            return None
        try:
            permissions = json.loads(row[7] or "{}")
        except (TypeError, json.JSONDecodeError):
            permissions = {}
        return {"id": row[0], "name": row[1], "email": row[2], "phone": row[3], "role": row[4],
                "client_id": row[5], "status": row[6], "permissions": permissions, "password_hash": row[8]}

    def get_user_by_id(self, user_id):
        """Consulta el estado vigente de una cuenta para invalidar sesiones suspendidas."""
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT id,name,email,phone,role,client_id,status,permissions_json,photo_ref "
                f"FROM operations_users WHERE id={p}", (_text(user_id),),
            ).fetchone()
        if not row:
            return None
        try:
            permissions = json.loads(row[7] or "{}")
        except (TypeError, json.JSONDecodeError):
            permissions = {}
        return {"id": row[0], "name": row[1], "email": row[2], "phone": row[3], "role": row[4],
                "client_id": row[5], "status": row[6], "permissions": permissions,
                "has_photo": bool(row[8]), "photo_ref": row[8]}

    def save_user_profile(self, user_id, name, *, photo_ref=None):
        """Permite que una cuenta cambie sólo su nombre visible y su fotografía."""
        self.initialize()
        user_id, name = _text(user_id), _text(name)
        if not user_id or not name or len(name) > 100:
            raise ValueError("Escribe un nombre de máximo 100 caracteres.")
        p, stamp = self.placeholder, _now()
        with self.connection() as conn:
            if photo_ref is None:
                cursor = conn.execute(
                    f"UPDATE operations_users SET name={p},updated_at={p} WHERE id={p}",
                    (name, stamp, user_id),
                )
            else:
                cursor = conn.execute(
                    f"UPDATE operations_users SET name={p},photo_ref={p},updated_at={p} WHERE id={p}",
                    (name, _text(photo_ref), stamp, user_id),
                )
            if cursor.rowcount != 1:
                raise ValueError("La cuenta ya no existe.")
        return self.get_user_by_id(user_id)

    def create_invite(self, item):
        self.initialize()
        role = _text(item.get("role"))
        if role not in {"technician", "client"}:
            raise ValueError("Selecciona un tipo de acceso válido.")
        name, email = _text(item.get("name")), _text(item.get("email")).casefold()
        if not name or not email or "@" not in email:
            raise ValueError("Nombre y correo válido son obligatorios.")
        client_id = _text(item.get("client_id")) if role == "client" else ""
        if role == "client" and not any(c["id"] == client_id for c in self.snapshot()["clients"]):
            raise ValueError("Selecciona el cliente relacionado.")
        invite_id, stamp, p = f"INV_{uuid.uuid4().hex[:16].upper()}", _now(), self.placeholder
        with self.connection() as conn:
            conn.execute(
                f"INSERT INTO operations_invites(id,token_hash,name,email,phone,role,client_id,permissions_json,status,expires_at,created_by,created_at,updated_at) "
                f"VALUES ({','.join([p] * 13)})",
                (invite_id, _text(item.get("token_hash")), name, email, _text(item.get("phone")), role,
                 client_id, json.dumps(item.get("permissions") or {}, ensure_ascii=False), "pending",
                 _text(item.get("expires_at")), _text(item.get("created_by")), stamp, stamp),
            )
        return self.get_invite(_text(item.get("token_hash")))

    def get_invite(self, token_hash):
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT id,name,email,phone,role,client_id,permissions_json,status,expires_at,used_at "
                f"FROM operations_invites WHERE token_hash={p}", (_text(token_hash),),
            ).fetchone()
        if not row:
            return None
        try:
            permissions = json.loads(row[6] or "{}")
        except (TypeError, json.JSONDecodeError):
            permissions = {}
        return {"id": row[0], "name": row[1], "email": row[2], "phone": row[3], "role": row[4],
                "client_id": row[5], "permissions": permissions, "status": row[7],
                "expires_at": row[8], "used_at": row[9]}

    def accept_invite(self, token_hash, password_hash):
        self.initialize()
        invite = self.get_invite(token_hash)
        if not invite or invite["status"] != "pending":
            raise ValueError("La invitación no existe o ya fue utilizada.")
        if invite["expires_at"] and invite["expires_at"] < _now():
            raise ValueError("La invitación ya venció.")
        user_id, stamp, p = f"USR_{uuid.uuid4().hex[:16].upper()}", _now(), self.placeholder
        with self.connection() as conn:
            if conn.execute(f"SELECT 1 FROM operations_users WHERE LOWER(email)=LOWER({p})", (invite["email"],)).fetchone():
                raise ValueError("Ese correo ya tiene una cuenta.")
            conn.execute(
                f"INSERT INTO operations_users(id,name,email,phone,role,client_id,status,permissions_json,password_hash,created_at,updated_at) "
                f"VALUES ({','.join([p] * 11)})",
                (user_id, invite["name"], invite["email"], invite["phone"], invite["role"], invite["client_id"],
                 "active", json.dumps(invite["permissions"], ensure_ascii=False), _text(password_hash), stamp, stamp),
            )
            conn.execute(
                f"UPDATE operations_invites SET status='accepted',used_at={p},updated_at={p} WHERE id={p}",
                (stamp, stamp, invite["id"]),
            )
        return next(user for user in self.list_users() if user["id"] == user_id)

    def delete_user_account(self, user_id):
        self.initialize()
        user=self.get_user_by_id(user_id)
        if not user:
            return False
        if user['role'] not in {'client','technician'}:
            raise ValueError('No se puede eliminar una cuenta administrativa.')
        p=self.placeholder
        with self.connection() as conn:
            if conn.execute(f'SELECT 1 FROM operations_pay_accounts WHERE user_id={p}', (user_id,)).fetchone():
                raise ValueError('Esta cuenta tiene historial de sueldo o pagos. Conserva el historial: suspende el acceso y desactiva su sueldo futuro desde Pagos a técnicos.')
            conn.execute(f'DELETE FROM operations_invites WHERE LOWER(email)=LOWER({p})', (user['email'],))
            conn.execute(f'DELETE FROM operations_users WHERE id={p}', (user_id,))
        return True

    def change_client_user_company(self, user_id, client_id, expected_client_id):
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            user = conn.execute(f"SELECT role,client_id FROM operations_users WHERE id={p}", (user_id,)).fetchone()
            if not user or user[0] != 'client':
                raise ValueError('Sólo se puede cambiar la empresa de una cuenta cliente.')
            if user[1] != expected_client_id:
                raise ValueError('La empresa de esta cuenta cambió. Actualiza y vuelve a confirmar.')
            if not conn.execute(f"SELECT id FROM operations_clients WHERE id={p}",(client_id,)).fetchone():
                raise ValueError('La empresa destino no existe.')
            changed=conn.execute(f"UPDATE operations_users SET client_id={p},updated_at={p} WHERE id={p} AND client_id={p}",(client_id,_now(),user_id,expected_client_id))
            if not changed.rowcount:
                raise ValueError('La empresa cambió durante la confirmación. Actualiza la cuenta.')
        return self.get_user_by_id(user_id)

    def save_user_permissions(self, user_id, permissions, *, status=None):
        self.initialize()
        p, stamp = self.placeholder, _now()
        with self.connection() as conn:
            row = conn.execute(f"SELECT role FROM operations_users WHERE id={p}", (_text(user_id),)).fetchone()
            if not row:
                return None
            next_status = _text(status) if status is not None else None
            if next_status and next_status not in {"active", "suspended"}:
                raise ValueError("El estado de la cuenta no es válido.")
            if next_status:
                conn.execute(
                    f"UPDATE operations_users SET permissions_json={p},status={p},updated_at={p} WHERE id={p}",
                    (json.dumps(permissions or {}, ensure_ascii=False), next_status, stamp, _text(user_id)),
                )
            else:
                conn.execute(
                    f"UPDATE operations_users SET permissions_json={p},updated_at={p} WHERE id={p}",
                    (json.dumps(permissions or {}, ensure_ascii=False), stamp, _text(user_id)),
                )
        return next(user for user in self.list_users() if user["id"] == _text(user_id))

    def delete_fault(self, fault_id):
        """Oculta una falla sin perder su evidencia ni reimportarla desde Sheets."""
        self.initialize()
        fault_id = _text(fault_id)
        p = self.placeholder
        with self.connection() as conn:
            stamp = _now()
            changed = conn.execute(
                f"UPDATE operations_faults SET deleted_at={p},updated_at={p} "
                f"WHERE id={p} AND deleted_at=''",
                (stamp, stamp, fault_id),
            )
            if changed.rowcount:
                conn.execute(
                    f"DELETE FROM operations_sync_outbox WHERE entity_type='fault' AND entity_id={p}",
                    (fault_id,),
                )
        return changed.rowcount == 1

    def resolve_fault(self, fault_id, *, resolved_by="", resolution_notes="", status="Atendida"):
        """Marca una falla como atendida y conserva el cierre para auditoría."""
        self.initialize()
        if status not in {"Atendida", "Revisada", "Reparada"}:
            raise ValueError("Selecciona Revisada o Reparada.")
        fault_id = _text(fault_id)
        if not fault_id:
            raise ValueError("La falla es obligatoria.")
        stamp = _now()
        p = self.placeholder
        with self.connection() as conn:
            found = conn.execute(
                f"SELECT id,client_id,equipment_id FROM operations_faults WHERE id={p}", (fault_id,)
            ).fetchone()
            if not found:
                return None
            conn.execute(
                f"UPDATE operations_faults SET status={p},resolved_at={p},resolved_by={p},"
                f"resolution_notes={p},source='app',sync_status='pending',updated_at={p} WHERE id={p}",
                (status, stamp if status != 'Revisada' else '', _text(resolved_by), _text(resolution_notes), stamp, fault_id),
            )
        self.queue_sync("fault", fault_id, "sheets", "upsert", {
            "client_id": found[1], "equipment_id": found[2],
            "status": status, "resolved_at": stamp if status != 'Revisada' else '',
            "resolved_by": _text(resolved_by), "resolution_notes": _text(resolution_notes),
        })
        return next(row for row in self.snapshot()["faults"] if row["id"] == fault_id)

    def get_report_draft(self, equipment_id, round_number, user_id=None):
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT id,client_id,equipment_id,round_number,matrix_id,report_type,payload_json,updated_at,revision "
                f"FROM operations_reports WHERE equipment_id={p} AND round_number={p} "
                "AND source='app' AND state='draft' ORDER BY updated_at DESC",
                (_text(equipment_id), _text(round_number)),
            ).fetchall()
        row = rows[0] if rows else None
        if not row:
            return None
        return {
            "id": row[0], "client_id": row[1], "equipment_id": row[2], "round": row[3],
            "matrix_id": row[4], "report_type": row[5], "payload": json.loads(row[6] or "{}"),
            "updated_at": row[7], "revision": int(row[8] or 0),
        }

    def delete_report_draft(self, report_id, user_id=None):
        """Elimina únicamente un borrador; un reporte finalizado nunca entra aquí."""
        self.initialize()
        report_id, p = _text(report_id), self.placeholder
        with self.connection() as conn:
            found = conn.execute(
                f"SELECT id,payload_json FROM operations_reports WHERE id={p} AND source='app' AND state='draft'",
                (report_id,),
            ).fetchone()
            if found and user_id is not None and json.loads(found[1] or '{}').get('_draft_user_id') != user_id:
                raise ValueError('Ese borrador pertenece a otra cuenta.')
            if not found:
                return False
            conn.execute(f"DELETE FROM operations_evidence WHERE report_id={p}", (report_id,))
            conn.execute(f"DELETE FROM operations_reports WHERE id={p}", (report_id,))
        return True

    def get_report_submission(self, draft_id, user_id=None):
        """Recibo persistente: también reconoce ediciones cuyo borrador se eliminó."""
        self.initialize()
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT report_id,user_id FROM operations_report_submissions WHERE draft_id={self.placeholder}",
                (_text(draft_id),),
            ).fetchone()
        if row:
            if user_id is not None and row[1] != _text(user_id):
                return None
            return self.get_report_detail(row[0])
        report = self.get_report_detail(draft_id)
        if report and report['completed'] and (user_id is None or
                report['payload'].get('_draft_user_id') == user_id):
            return report
        return None

    def finalize_report(self, report_id):
        """Finaliza un borrador sin perderlo y lo deja listo para sincronización."""
        self.initialize()
        report_id = _text(report_id)
        if not report_id:
            raise ValueError("El borrador del reporte es obligatorio.")
        p = self.placeholder
        stamp = _now()
        with self.connection() as conn:
            # Serialize the final check per equipment, including submissions
            # from separate workers. SQLite is only used locally.
            if self.dialect == 'sqlite':
                conn.execute('BEGIN IMMEDIATE')
            else:
                conn.execute(
                    f"SELECT id FROM operations_equipment WHERE id=(SELECT equipment_id "
                    f"FROM operations_reports WHERE id={p}) FOR UPDATE", (report_id,),
                ).fetchone()
            row = conn.execute(
                f"SELECT id,client_id,equipment_id,round_number,payload_json FROM operations_reports "
                f"WHERE id={p} AND source='app' AND state='draft'", (report_id,),
            ).fetchone()
            if not row:
                confirmed = self.get_report_submission(report_id)
                if confirmed:
                    return confirmed
                raise ValueError("El borrador ya no existe o ya fue finalizado.")
            try:
                payload = json.loads(row[4] or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            edit_report_id = _text(payload.pop("_edit_report_id", ""))
            if not edit_report_id:
                existing = conn.execute(
                    f"SELECT id FROM operations_reports WHERE equipment_id={p} AND client_id={p} "
                    f"AND round_number={p} AND completed=1 AND id!={p}",
                    (row[2], row[1], row[3], report_id),
                ).fetchone()
                if existing:
                    raise ValueError("Este equipo ya tiene reporte en esa ronda. Abre Editar reporte; tu borrador se conserva.")
            start_at = _text(payload.get("inicio"))
            end_at = _text(payload.get("fin"))
            if not start_at or not end_at:
                raise ValueError("Captura las fechas de inicio y terminación antes de finalizar.")
            final_report_id = report_id
            if edit_report_id:
                target = conn.execute(
                    f"SELECT id FROM operations_reports WHERE id={p} AND client_id={p} AND equipment_id={p} "
                    f"AND round_number={p} AND completed=1",
                    (edit_report_id, row[1], row[2], row[3]),
                ).fetchone()
                if not target:
                    raise ValueError("El reporte original ya no existe.")
                final_report_id = edit_report_id
                conn.execute(
                    f"UPDATE operations_reports SET start_at={p},end_at={p},payload_json={p},"
                    f"completed=1,source='app',state='completed',sync_status='pending',updated_at={p} WHERE id={p}",
                    (start_at, end_at, json.dumps(payload, ensure_ascii=False), stamp, final_report_id),
                )
                draft_evidence = conn.execute(
                    f"SELECT position,storage_ref,drive_ref,sync_status,updated_at FROM operations_evidence "
                    f"WHERE report_id={p} ORDER BY position", (report_id,),
                ).fetchall()
                if draft_evidence:
                    conn.execute(f"DELETE FROM operations_evidence WHERE report_id={p}", (final_report_id,))
                    for evidence in draft_evidence:
                        conn.execute(
                            f"INSERT INTO operations_evidence(id,report_id,position,storage_ref,drive_ref,sync_status,updated_at) "
                            f"VALUES ({','.join([p] * 7)})",
                            (f"{final_report_id}:foto:{evidence[0]}", final_report_id, *evidence),
                        )
                conn.execute(f"DELETE FROM operations_evidence WHERE report_id={p}", (report_id,))
                conn.execute(f"DELETE FROM operations_reports WHERE id={p}", (report_id,))
            else:
                conn.execute(
                    f"UPDATE operations_reports SET start_at={p},end_at={p},payload_json={p},"
                    f"completed=1,source='app',state='completed',sync_status='pending',updated_at={p} WHERE id={p}",
                    (start_at, end_at, json.dumps(payload, ensure_ascii=False), stamp, report_id),
                )
            # Antes de la modalidad compartida podía existir un borrador por
            # técnico. Se conservan, pero dejan de aparecer como editables al
            # confirmar el reporte común.
            conn.execute(
                f"UPDATE operations_reports SET state='superseded',updated_at={p} "
                f"WHERE equipment_id={p} AND client_id={p} AND round_number={p} "
                f"AND source='app' AND state='draft' AND id!={p}",
                (stamp, row[2], row[1], row[3], final_report_id),
            )
            self._remember_observations(conn, payload)
            conn.execute(
                f"INSERT INTO operations_report_submissions(draft_id,report_id,user_id,confirmed_at) "
                f"VALUES ({','.join([p] * 4)}) ON CONFLICT(draft_id) DO NOTHING",
                (report_id, final_report_id, _text(payload.get('_draft_user_id')), stamp),
            )
            evidence = conn.execute(
                f"SELECT position,drive_ref FROM operations_evidence WHERE report_id={p} ORDER BY position",
                (final_report_id,),
            ).fetchall()
            # Report, receipt and delivery queue commit together. A restart
            # cannot leave a completed report without its Sheets delivery.
            self._queue_sync_in_transaction(conn, "report", final_report_id, "sheets", "upsert", {
                "client_id": row[1], "equipment_id": row[2], "round": row[3],
                "completed": True, "payload": payload,
                "evidence": [{"position": item[0], "drive_ref": item[1]} for item in evidence],
            })
        return self.get_report_detail(final_report_id)

    def reset_report_evidence(self, report_id):
        """Prepara un nuevo intento de subida para un borrador."""
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            found = conn.execute(
                f"SELECT id FROM operations_reports WHERE id={p} AND source='app' AND state='draft'",
                (_text(report_id),),
            ).fetchone()
            if not found:
                raise ValueError("El borrador ya no existe o ya fue finalizado.")
            conn.execute(f"DELETE FROM operations_evidence WHERE report_id={p}", (_text(report_id),))

    def reserve_report_evidence(self, report_id, preferred_position, mutation_id, *, actor_id="", actor_name=""):
        """Reserva una ranura libre de forma atómica e idempotente."""
        self.initialize()
        report_id, mutation_id = _text(report_id), _text(mutation_id)
        preferred_position = int(preferred_position or 0)
        if not report_id or not mutation_id or preferred_position not in range(1, 7):
            raise ValueError("La fotografía no tiene un identificador o posición válidos.")
        p, stamp = self.placeholder, _now()
        with self.connection() as conn:
            if self.dialect == "sqlite":
                conn.execute("BEGIN IMMEDIATE")
            else:
                conn.execute(
                    f"SELECT id FROM operations_reports WHERE id={p} FOR UPDATE", (report_id,)
                ).fetchone()
            report = conn.execute(
                f"SELECT state FROM operations_reports WHERE id={p} AND source='app'", (report_id,)
            ).fetchone()
            if not report or report[0] != "draft":
                raise ValueError("El borrador ya no está disponible.")
            existing = conn.execute(
                f"SELECT position,storage_ref,drive_ref,sync_status,actor_id,actor_name,created_at "
                f"FROM operations_evidence WHERE report_id={p} AND mutation_id={p}",
                (report_id, mutation_id),
            ).fetchone()
            if existing:
                return {
                    "position": int(existing[0]), "storage_ref": _text(existing[1]),
                    "drive_ref": _text(existing[2]), "sync_status": _text(existing[3]),
                    "actor_id": _text(existing[4]), "actor_name": _text(existing[5]),
                    "created_at": _text(existing[6]), "mutation_id": mutation_id,
                }
            occupied = {
                int(row[0]) for row in conn.execute(
                    f"SELECT position FROM operations_evidence WHERE report_id={p}", (report_id,)
                ).fetchall()
            }
            available = [slot for slot in range(1, 7) if slot not in occupied]
            if not available:
                raise ValueError("Este reporte ya tiene las 6 fotografías permitidas.")
            position = preferred_position if preferred_position in available else available[0]
            evidence_id = f"{report_id}:upload:{mutation_id[:64]}"
            conn.execute(
                f"INSERT INTO operations_evidence"
                f"(id,report_id,position,storage_ref,drive_ref,sync_status,actor_id,actor_name,mutation_id,created_at,updated_at) "
                f"VALUES ({','.join([p] * 11)})",
                (evidence_id, report_id, position, "", "", "uploading", _text(actor_id),
                 _text(actor_name), mutation_id, stamp, stamp),
            )
            conn.execute(
                f"UPDATE operations_reports SET revision=revision+1,updated_at={p} WHERE id={p}",
                (stamp, report_id),
            )
        return {"position": position, "storage_ref": "", "drive_ref": "", "sync_status": "uploading",
                "actor_id": _text(actor_id), "actor_name": _text(actor_name), "created_at": stamp,
                "mutation_id": mutation_id}

    def complete_report_evidence(self, report_id, mutation_id, drive_ref, *, storage_ref=""):
        """Confirma en la reserva el archivo que Drive ya recibió."""
        self.initialize()
        report_id, mutation_id, drive_ref = _text(report_id), _text(mutation_id), _text(drive_ref)
        storage_ref = _text(storage_ref) or drive_ref
        if not report_id or not mutation_id or not drive_ref:
            raise ValueError("La evidencia no pudo confirmarse.")
        p, stamp = self.placeholder, _now()
        with self.connection() as conn:
            result = conn.execute(
                f"UPDATE operations_evidence SET storage_ref={p},drive_ref={p},sync_status='pending',updated_at={p} "
                f"WHERE report_id={p} AND mutation_id={p}",
                (storage_ref, drive_ref, stamp, report_id, mutation_id),
            )
            if result.rowcount != 1:
                raise ValueError("La reserva de la fotografía ya no existe.")
            conn.execute(
                f"UPDATE operations_reports SET revision=revision+1,updated_at={p} WHERE id={p}",
                (stamp, report_id),
            )
            row = conn.execute(
                f"SELECT position,actor_id,actor_name,created_at FROM operations_evidence "
                f"WHERE report_id={p} AND mutation_id={p}", (report_id, mutation_id),
            ).fetchone()
        return {"position": int(row[0]), "storage_ref": storage_ref, "drive_ref": drive_ref,
                "actor_id": _text(row[1]), "actor_name": _text(row[2]),
                "created_at": _text(row[3]), "mutation_id": mutation_id}

    def release_report_evidence(self, report_id, mutation_id):
        """Libera sólo una carga incompleta; nunca borra fotos ya confirmadas."""
        self.initialize()
        p, stamp = self.placeholder, _now()
        with self.connection() as conn:
            result = conn.execute(
                f"DELETE FROM operations_evidence WHERE report_id={p} AND mutation_id={p} "
                "AND drive_ref='' AND storage_ref=''",
                (_text(report_id), _text(mutation_id)),
            )
            if result.rowcount:
                conn.execute(
                    f"UPDATE operations_reports SET revision=revision+1,updated_at={p} WHERE id={p}",
                    (stamp, _text(report_id)),
                )
        return bool(result.rowcount)

    def report_live_state(self, report_id, *, user_id="", user_name=""):
        """Devuelve sólo metadatos ligeros y mantiene visible a quien está editando."""
        self.initialize()
        report_id, user_id, p, stamp = _text(report_id), _text(user_id), self.placeholder, _now()
        with self.connection() as conn:
            report = conn.execute(
                f"SELECT state,revision,updated_at,payload_json FROM operations_reports WHERE id={p}",
                (report_id,),
            ).fetchone()
            if not report:
                return None
            if user_id:
                conn.execute(
                    f"INSERT INTO operations_report_presence(report_id,user_id,user_name,last_seen_at) "
                    f"VALUES ({','.join([p] * 4)}) ON CONFLICT(report_id,user_id) DO UPDATE SET "
                    "user_name=excluded.user_name,last_seen_at=excluded.last_seen_at",
                    (report_id, user_id, _text(user_name), stamp),
                )
            evidence = conn.execute(
                f"SELECT position,actor_id,actor_name,created_at,updated_at,drive_ref,storage_ref "
                f"FROM operations_evidence WHERE report_id={p} AND (drive_ref<>'' OR storage_ref<>'') ORDER BY position",
                (report_id,),
            ).fetchall()
            presence = conn.execute(
                f"SELECT user_id,user_name,last_seen_at FROM operations_report_presence WHERE report_id={p}",
                (report_id,),
            ).fetchall()
        cutoff = datetime.now(timezone.utc).timestamp() - 20
        participants = []
        for row in presence:
            try:
                seen = datetime.fromisoformat(row[2]).timestamp()
            except (TypeError, ValueError):
                continue
            if seen >= cutoff:
                participants.append({"id": row[0], "name": row[1] or "Técnico", "last_seen_at": row[2]})
        try:
            payload = json.loads(report[3] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        return {
            "state": report[0], "revision": int(report[1] or 0), "updated_at": report[2],
            "payload": payload, "participants": participants,
            "evidence": [
                {"position": int(row[0]), "actor_id": _text(row[1]), "actor_name": _text(row[2]),
                 "created_at": _text(row[3]), "updated_at": _text(row[4]),
                 "drive_ref": _text(row[5]), "storage_ref": _text(row[6])}
                for row in evidence
            ],
        }

    def save_report_evidence(self, report_id, position, drive_ref, *, storage_ref=""):
        """Registra la posición Foto1–Foto6 después de confirmar Drive."""
        self.initialize()
        report_id, drive_ref = _text(report_id), _text(drive_ref)
        storage_ref = _text(storage_ref) or drive_ref
        position = int(position or 0)
        if position not in range(1, 7) or not drive_ref:
            raise ValueError("La posición y el archivo de evidencia no son válidos.")
        p, stamp = self.placeholder, _now()
        with self.connection() as conn:
            found = conn.execute(
                f"SELECT id FROM operations_reports WHERE id={p} AND source='app' AND state='draft'",
                (report_id,),
            ).fetchone()
            if not found:
                raise ValueError("El borrador ya no existe o ya fue finalizado.")
            evidence_id = f"{report_id}:foto:{position}"
            conn.execute(
                f"INSERT INTO operations_evidence(id,report_id,position,storage_ref,drive_ref,sync_status,updated_at) "
                f"VALUES ({','.join([p] * 7)}) ON CONFLICT(report_id,position) DO UPDATE SET "
                "storage_ref=excluded.storage_ref,drive_ref=excluded.drive_ref,sync_status='pending',updated_at=excluded.updated_at",
                (evidence_id, report_id, position, storage_ref, drive_ref, "pending", stamp),
            )
            conn.execute(
                f"UPDATE operations_reports SET revision=revision+1,updated_at={p} WHERE id={p}",
                (stamp, report_id),
            )
        return {"position": position, "storage_ref": storage_ref, "drive_ref": drive_ref}

    def get_report_detail(self, report_id):
        """Carga el detalle pesado sólo cuando alguien abre un reporte."""
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT r.id,r.client_id,r.equipment_id,r.round_number,r.start_at,r.end_at,"
                f"r.completed,r.matrix_id,r.report_type,r.payload_json,r.state,r.sync_status,r.revision,"
                f"(SELECT COUNT(*) FROM operations_evidence e WHERE e.report_id=r.id AND (e.drive_ref<>'' OR e.storage_ref<>'')) "
                f"FROM operations_reports r WHERE r.id={p}", (_text(report_id),)
            ).fetchone()
            evidence_rows = conn.execute(
                f"SELECT position,storage_ref,drive_ref,actor_id,actor_name,created_at,mutation_id FROM operations_evidence "
                f"WHERE report_id={p} AND (drive_ref<>'' OR storage_ref<>'') ORDER BY position", (_text(report_id),)
            ).fetchall() if row else []
        if not row:
            return None
        try:
            payload = json.loads(row[9] or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        aliases = {"inicio": "FechaInicio", "fin": "FechaFin", "p1": "PresionCto1", "p2": "PresionCto2",
                   "t1": "TempCto1", "t2": "TempCto2", "a1": "Amperaje1", "a2": "Amperaje2",
                   "electrico": "ObsElectrico", "electronico": "OBsElectrónico", "mecanico": "ObsMecanico",
                   "notas": "Comentarios", "correctivo": "MtoCorrectivo", "partes": "PartesUtilizadas", "revisor": "Responsable"}
        for name, column in aliases.items():
            if name not in payload and column in payload:
                payload[name] = payload[column]
        return {
            "id": row[0], "client_id": row[1], "equipment_id": row[2], "round": row[3],
            "start": row[4], "end": row[5], "completed": bool(row[6]), "matrix_id": row[7],
            "report_type": row[8], "payload": payload, "state": row[10], "sync_status": row[11],
            "revision": int(row[12] or 0), "photo_count": int(row[13] or 0),
            "evidence": [{"position": int(item[0]), "storage_ref": _text(item[1]),
                          "drive_ref": _text(item[2]), "actor_id": _text(item[3]),
                          "actor_name": _text(item[4]), "created_at": _text(item[5]),
                          "mutation_id": _text(item[6])} for item in evidence_rows],
        }

    def get_report_evidence_ref(self, report_id, position):
        self.initialize()
        p = self.placeholder
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT r.client_id,e.storage_ref,e.drive_ref FROM operations_evidence e "
                f"JOIN operations_reports r ON r.id=e.report_id WHERE e.report_id={p} AND e.position={p}",
                (_text(report_id), int(position)),
            ).fetchone()
        if not row:
            return None
        return {"client_id": row[0], "photo_ref": _text(row[2]) or _text(row[1])}

    def queue_sync(self, entity_type, entity_id, destination, action, payload=None):
        """Encola una operación idempotente hacia Drive o Sheets."""
        self.initialize()
        with self.connection() as conn:
            return self._queue_sync_in_transaction(conn, entity_type, entity_id, destination, action, payload)

    def _queue_sync_in_transaction(self, conn, entity_type, entity_id, destination, action, payload=None):
        stamp = _now()
        operation_id = uuid.uuid4().hex
        p = self.placeholder
        conn.execute(
                f"INSERT INTO operations_sync_outbox"
                f"(id,entity_type,entity_id,destination,action,payload_json,status,attempts,last_error,created_at,updated_at) "
                f"VALUES ({','.join([p] * 11)}) ON CONFLICT(entity_type,entity_id,destination,action) "
                "DO UPDATE SET id=excluded.id,payload_json=excluded.payload_json,status='pending',attempts=0,last_error='',updated_at=excluded.updated_at",
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
                f"FROM operations_sync_outbox WHERE status!='synced' ORDER BY updated_at,created_at LIMIT {p}",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [{
            "id": row[0], "entity_type": row[1], "entity_id": row[2],
            "destination": row[3], "action": row[4], "payload": json.loads(row[5] or "{}"),
            "attempts": row[6], "last_error": row[7],
        } for row in rows]

    def mark_sync_success(self, operation_id, entity_type="", entity_id=""):
        """Confirma una salida y actualiza el estado visible de su entidad."""
        self.initialize()
        p, stamp = self.placeholder, _now()
        with self.connection() as conn:
            confirmed = conn.execute(
                f"UPDATE operations_sync_outbox SET status='synced',attempts=attempts+1,"
                f"last_error='',updated_at={p} WHERE id={p}",
                (stamp, _text(operation_id)),
            )
            if confirmed.rowcount != 1:
                return False  # A newer edit replaced this operation while Google was writing.
            table = {
                "report": "operations_reports",
                "fault": "operations_faults",
            }.get(_text(entity_type))
            if table and _text(entity_id):
                conn.execute(
                    f"UPDATE {table} SET sync_status='synced',updated_at={p} WHERE id={p}",
                    (stamp, _text(entity_id)),
                )
        return True

    def mark_sync_failure(self, operation_id, error, entity_type="", entity_id=""):
        """Conserva la operación para reintento sin perder el error anterior."""
        self.initialize()
        p, stamp = self.placeholder, _now()
        message = _text(error)[:1000]
        with self.connection() as conn:
            changed = conn.execute(
                f"UPDATE operations_sync_outbox SET status='failed',attempts=attempts+1,"
                f"last_error={p},updated_at={p} WHERE id={p}",
                (message, stamp, _text(operation_id)),
            )
            if changed.rowcount != 1:
                return False
            table = {
                "report": "operations_reports",
                "fault": "operations_faults",
            }.get(_text(entity_type))
            if table and _text(entity_id):
                conn.execute(
                    f"UPDATE {table} SET sync_status='failed',updated_at={p} WHERE id={p}",
                    (stamp, _text(entity_id)),
                )
