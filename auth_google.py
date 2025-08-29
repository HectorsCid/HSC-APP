# auth_google.py
# HSC deploy marker: 2025-08-29  (forzar rebuild en Render)

import os, json, base64, sys
from functools import lru_cache

from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials as SA_Credentials
from google.oauth2.credentials import Credentials as UserCreds
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

# Intento de imports opcionales de Flask (solo si se está dentro de una request)
try:
    from flask import has_request_context, flash
except Exception:  # pragma: no cover
    has_request_context = lambda: False  # type: ignore
    def flash(*args, **kwargs):  # type: ignore
        pass

# Scopes usados por la app (NO cambiar)
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

# =======================================================================================
#                                     Service Account
# =======================================================================================
def _load_service_account_info():
    """
    Carga credenciales de cuenta de servicio desde:
    1) SERVICE_ACCOUNT_B64 (JSON en base64)
    2) SERVICE_ACCOUNT_JSON (JSON plano)
    3) SERVICE_ACCOUNT_FILE (ruta)
    4) 'service_account.json' en cwd
    """
    b64 = os.environ.get("SERVICE_ACCOUNT_B64", "").strip()
    if b64:
        return json.loads(base64.b64decode(b64).decode("utf-8"))

    js = os.environ.get("SERVICE_ACCOUNT_JSON", "").strip()
    if js:
        return json.loads(js)

    path = os.environ.get("SERVICE_ACCOUNT_FILE", "").strip()
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    default = "service_account.json"
    if os.path.isfile(default):
        with open(default, "r", encoding="utf-8") as f:
            return json.load(f)

    raise RuntimeError(
        "No se encontraron credenciales de cuenta de servicio. "
        "Configura SERVICE_ACCOUNT_B64/SERVICE_ACCOUNT_JSON/SERVICE_ACCOUNT_FILE o sube 'service_account.json'."
    )

@lru_cache(maxsize=1)
def _sa_credentials():
    info = _load_service_account_info()
    return SA_Credentials.from_service_account_info(info, scopes=SCOPES)

@lru_cache(maxsize=1)
def get_drive_service():
    """Drive usando cuenta de servicio."""
    return build("drive", "v3", credentials=_sa_credentials(), cache_discovery=False)

@lru_cache(maxsize=1)
def get_sheets_service():
    """Sheets usando cuenta de servicio."""
    return build("sheets", "v4", credentials=_sa_credentials(), cache_discovery=False)

# =======================================================================================
#                                        Usuario
# =======================================================================================

def _env_token_json_b64() -> str | None:
    """
    Devuelve el contenido Base64 del token del usuario si está presente en env.
    Soporta dos nombres por compatibilidad: TOKEN_JSON_B64 y GOOGLE_TOKEN_B64.
    """
    v = (os.environ.get("TOKEN_JSON_B64") or "").strip()
    if v:
        return v
    v2 = (os.environ.get("GOOGLE_TOKEN_B64") or "").strip()
    if v2:
        return v2
    return None

def _load_user_token():
    """
    Carga token del usuario desde (en este orden):
    1) TOKEN_JSON_B64 o GOOGLE_TOKEN_B64  (base64 de token.json)
    2) TOKEN_JSON                          (contenido JSON plano)
    3) TOKEN_JSON_FILE                     (ruta)
    4) /data/token.json                    (si existe)
    5) ./token.json                        (cwd)
    Devuelve dict o None.
    """
    b64 = _env_token_json_b64()
    if b64:
        try:
            return json.loads(base64.b64decode(b64).decode("utf-8"))
        except Exception as e:
            # Si el Secret está malformado, preferimos continuar a otras fuentes
            print(f"⚠️ TOKEN_JSON_B64 malformado: {type(e).__name__}: {e}", file=sys.stderr)

    js = os.environ.get("TOKEN_JSON", "").strip()
    if js:
        try:
            return json.loads(js)
        except Exception as e:
            print(f"⚠️ TOKEN_JSON malformado: {type(e).__name__}: {e}", file=sys.stderr)

    path = os.environ.get("TOKEN_JSON_FILE", "").strip()
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ TOKEN_JSON_FILE ilegible: {type(e).__name__}: {e}", file=sys.stderr)

    # Opción adicional: /data/token.json (si existe), útil en Render con Persistent Disk
    data_path = "/data/token.json"
    if os.path.isfile(data_path):
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ /data/token.json ilegible: {type(e).__name__}: {e}", file=sys.stderr)

    if os.path.isfile("token.json"):
        try:
            with open("token.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ token.json ilegible en cwd: {type(e).__name__}: {e}", file=sys.stderr)

    return None

def _maybe_flash(msg: str, category: str = "warning"):
    try:
        if has_request_context():
            flash(msg, category)
    except Exception:
        pass

def _invalidate_local_token_files():
    """
    Intenta borrar copias locales del token si existen (no puede borrar env vars).
    No falla si no puede borrar; solo informa por stderr.
    """
    candidates = []
    env_file = (os.environ.get("TOKEN_JSON_FILE") or "").strip()
    if env_file:
        candidates.append(env_file)
    # /data/token.json si existiera
    data_path = "/data/token.json"
    if os.path.isfile(data_path):
        candidates.append(data_path)
    # token.json en cwd
    if os.path.isfile("token.json"):
        candidates.append("token.json")

    for p in candidates:
        try:
            if os.path.isfile(p):
                os.remove(p)
                print(f"🧹 Token inválido eliminado: {p}", file=sys.stderr)
        except Exception as e:
            print(f"⚠️ No se pudo eliminar token inválido ({p}): {type(e).__name__}: {e}", file=sys.stderr)

@lru_cache(maxsize=1)
def _user_credentials():
    """
    Construye credenciales de usuario (OAuth) desde token.json y las refresca si es necesario.
    Lanza RuntimeError si no hay token disponible o si el refresh falla.
    """
    data = _load_user_token()
    if not data:
        raise RuntimeError(
            "No se encontró token.json del USUARIO. "
            "Sugerencia: define TOKEN_JSON_B64 (o GOOGLE_TOKEN_B64) en Render con el contenido de tu token.json."
        )

    creds = UserCreds.from_authorized_user_info(data, scopes=SCOPES)

    # Refrescar si está expirado y tenemos refresh_token
    try:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
    except RefreshError as e:
        # Manejo suave: limpiar copias locales, avisar y re-lanzar error claro
        _invalidate_local_token_files()
        _maybe_flash("Vuelve a conectar Google (token inválido o revocado).", "error")
        # Nota: no podemos borrar secrets de entorno en tiempo de ejecución.
        raise RuntimeError("Google OAuth RefreshError: token expirado o revocado. Vuelve a conectar Google.") from e

    return creds

@lru_cache(maxsize=1)
def get_drive_service_user():
    """Drive autenticado como el USUARIO (token.json). Úsalo para SUBIR/LEER archivos privados."""
    return build("drive", "v3", credentials=_user_credentials(), cache_discovery=False)

@lru_cache(maxsize=1)
def get_sheets_service_user():
    """Sheets autenticado como el USUARIO (token.json)."""
    return build("sheets", "v4", credentials=_user_credentials(), cache_discovery=False)



