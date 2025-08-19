# auth_google.py
import os, json, base64
from functools import lru_cache
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials as SA_Credentials
from google.oauth2.credentials import Credentials as UserCreds
from google.auth.transport.requests import Request

# Scopes usados por la app
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

# -------------------- Service Account --------------------
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
    """Drive usando cuenta de servicio (útil para lectura pública o carpetas compartidas con la SA)."""
    return build("drive", "v3", credentials=_sa_credentials(), cache_discovery=False)

@lru_cache(maxsize=1)
def get_sheets_service():
    """Sheets usando cuenta de servicio."""
    return build("sheets", "v4", credentials=_sa_credentials(), cache_discovery=False)

# -------------------- Usuario (token.json) --------------------
def _load_user_token():
    """
    Carga token del usuario desde:
    1) TOKEN_JSON_B64 (base64 de token.json)
    2) TOKEN_JSON (contenido JSON plano)
    3) TOKEN_JSON_FILE (ruta)
    4) 'token.json' en cwd
    Devuelve dict o None.
    """
    b64 = os.environ.get("TOKEN_JSON_B64", "").strip()
    if b64:
        return json.loads(base64.b64decode(b64).decode("utf-8"))

    js = os.environ.get("TOKEN_JSON", "").strip()
    if js:
        return json.loads(js)

    path = os.environ.get("TOKEN_JSON_FILE", "").strip()
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    if os.path.isfile("token.json"):
        with open("token.json", "r", encoding="utf-8") as f:
            return json.load(f)

    return None

@lru_cache(maxsize=1)
def _user_credentials():
    """
    Construye credenciales de usuario (OAuth) desde token.json y las refresca si es necesario.
    Lanza RuntimeError si no hay token disponible.
    """
    data = _load_user_token()
    if not data:
        raise RuntimeError("No se encontró token.json del USUARIO. Sube 'token.json' a Render como Secret File.")

    creds = UserCreds.from_authorized_user_info(data, scopes=SCOPES)
    # Refrescar si está expirado y tenemos refresh_token
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds

@lru_cache(maxsize=1)
def get_drive_service_user():
    """Drive autenticado como el USUARIO (token.json). Úsalo para SUBIR/LEER archivos privados."""
    return build("drive", "v3", credentials=_user_credentials(), cache_discovery=False)

@lru_cache(maxsize=1)
def get_sheets_service_user():
    """Sheets autenticado como el USUARIO (token.json)."""
    return build("sheets", "v4", credentials=_user_credentials(), cache_discovery=False)


