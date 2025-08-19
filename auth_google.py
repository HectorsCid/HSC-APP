# auth_google.py
import os, json, base64
from functools import lru_cache
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials as SA_Credentials

# Scopes que ya usas en tu app
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

def _load_service_account_info():
    """
    Carga credenciales de cuenta de servicio desde:
    1) ENV SERVICE_ACCOUNT_B64 (JSON en base64), o
    2) ENV SERVICE_ACCOUNT_JSON (JSON plano), o
    3) archivo SERVICE_ACCOUNT_FILE (ruta), o
    4) archivo 'service_account.json' en el cwd.
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

    # Por defecto: secret file en el raíz del repo
    default = "service_account.json"
    if os.path.isfile(default):
        with open(default, "r", encoding="utf-8") as f:
            return json.load(f)

    raise RuntimeError("No se encontraron credenciales de cuenta de servicio. "
                       "Configura SERVICE_ACCOUNT_B64/SERVICE_ACCOUNT_JSON/SERVICE_ACCOUNT_FILE o sube 'service_account.json'.")

@lru_cache(maxsize=1)
def _sa_credentials():
    info = _load_service_account_info()
    return SA_Credentials.from_service_account_info(info, scopes=SCOPES)

@lru_cache(maxsize=1)
def get_drive_service():
    creds = _sa_credentials()
    return build("drive", "v3", credentials=creds, cache_discovery=False)

@lru_cache(maxsize=1)
def get_sheets_service():
    creds = _sa_credentials()
    return build("sheets", "v4", credentials=creds, cache_discovery=False)
