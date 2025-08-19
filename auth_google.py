# auth_google.py
import os, json, base64
from functools import lru_cache
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials as SA_Credentials
from google.oauth2.credentials import Credentials as UserCredentials

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

def _load_service_account_info():
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

def _has_token_file() -> bool:
    return os.path.isfile("token.json")

@lru_cache(maxsize=1)
def _user_credentials():
    # Lee token.json y refresca automáticamente cuando haga falta
    return UserCredentials.from_authorized_user_file("token.json", SCOPES)

@lru_cache(maxsize=1)
def _sa_credentials():
    info = _load_service_account_info()
    return SA_Credentials.from_service_account_info(info, scopes=SCOPES)

def _pick_creds():
    # Si hay token.json, PRIORIDAD al usuario final (evita el problema de cuota de la service account)
    if _has_token_file():
        return _user_credentials()
    return _sa_credentials()

@lru_cache(maxsize=1)
def get_drive_service():
    creds = _pick_creds()
    return build("drive", "v3", credentials=creds, cache_discovery=False)

@lru_cache(maxsize=1)
def get_sheets_service():
    creds = _pick_creds()
    return build("sheets", "v4", credentials=creds, cache_discovery=False)

