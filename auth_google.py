# auth_google.py
# HSC deploy marker: 2025-08-29  (forzar rebuild en Render)

import os, json, base64, sys, threading
import httplib2

from googleapiclient.discovery import build
from google_auth_httplib2 import AuthorizedHttp
from google.oauth2.service_account import Credentials as SA_Credentials
from google.oauth2.credentials import Credentials as UserCreds
from google.auth.transport.requests import Request
from google.auth.transport.requests import AuthorizedSession
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

# Los clientes de google-api-python-client mantienen estado de conexión y no
# deben compartirse entre el monitor automático y los hilos de Gunicorn.
_thread_services = threading.local()
_discovery_build_lock = threading.Lock()
GOOGLE_HTTP_TIMEOUT = max(5.0, float(os.environ.get("GOOGLE_HTTP_TIMEOUT", "20")))
GOOGLE_REFRESH_TIMEOUT = max(5.0, float(os.environ.get("GOOGLE_REFRESH_TIMEOUT", "12")))


class _BoundedRequest(Request):
    """Impide que la renovación OAuth deje un hilo bloqueado durante minutos."""

    def __call__(self, url, method="GET", body=None, headers=None, timeout=120, **kwargs):
        try:
            bounded_timeout = min(float(timeout), GOOGLE_REFRESH_TIMEOUT)
        except (TypeError, ValueError):
            bounded_timeout = GOOGLE_REFRESH_TIMEOUT
        return super().__call__(
            url=url,
            method=method,
            body=body,
            headers=headers,
            timeout=bounded_timeout,
            **kwargs,
        )


def _close_service(service):
    try:
        http = getattr(service, "_http", None)
        if http and hasattr(http, "close"):
            http.close()
    except Exception:
        pass


def reset_thread_google_services():
    """Descarta únicamente las conexiones Google del hilo actual."""
    services = getattr(_thread_services, "services", {})
    for service in services.values():
        _close_service(service)
    _thread_services.services = {}
    _thread_services.user_credentials = None
    _thread_services.sa_credentials = None


def _thread_service(key, api, version, credentials, timeout=None, fresh=False):
    """Crea un transporte httplib2 independiente para cada hilo."""
    timeout = GOOGLE_HTTP_TIMEOUT if timeout is None else max(5.0, float(timeout))
    services = getattr(_thread_services, "services", None)
    if services is None:
        services = {}
        _thread_services.services = services
    cache_key = f"{key}:{timeout:g}"
    if fresh:
        _close_service(services.pop(cache_key, None))
    service = services.get(cache_key)
    if service is None:
        authorized_http = AuthorizedHttp(
            credentials,
            http=httplib2.Http(timeout=timeout),
        )
        # googleapiclient importa discovery_cache de forma perezosa. En un
        # arranque nuevo, dos builds simultáneos pueden dejar ese módulo a medio
        # inicializar y provocar el error "partially initialized module".
        with _discovery_build_lock:
            service = build(api, version, http=authorized_http, cache_discovery=False)
        services[cache_key] = service
    return service

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

def _sa_credentials():
    credentials = getattr(_thread_services, "sa_credentials", None)
    if credentials is None:
        info = _load_service_account_info()
        credentials = SA_Credentials.from_service_account_info(info, scopes=SCOPES)
        _thread_services.sa_credentials = credentials
    return credentials

def get_drive_service(timeout=None, fresh=False):
    """Drive con cuenta de servicio y una conexión independiente por hilo."""
    return _thread_service(
        "drive_sa", "drive", "v3", _sa_credentials(), timeout=timeout, fresh=fresh
    )

def get_sheets_service(timeout=None, fresh=False):
    """Sheets usando una conexión independiente por hilo."""
    return _thread_service(
        "sheets_sa", "sheets", "v4", _sa_credentials(), timeout=timeout, fresh=fresh
    )

def get_sheets_authorized_session():
    """Sesión REST nueva para lecturas automáticas con timeout estricto."""
    credentials = SA_Credentials.from_service_account_info(
        _load_service_account_info(), scopes=SCOPES
    )
    return AuthorizedSession(credentials, refresh_timeout=GOOGLE_REFRESH_TIMEOUT)

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

def _user_credentials():
    """
    Construye credenciales de usuario (OAuth) desde token.json y las refresca si es necesario.
    Lanza RuntimeError si no hay token disponible o si el refresh falla.
    """
    cached = getattr(_thread_services, "user_credentials", None)
    if cached is not None:
        return cached

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
            creds.refresh(_BoundedRequest())
    except RefreshError as e:
        # Manejo suave: limpiar copias locales, avisar y re-lanzar error claro
        _invalidate_local_token_files()
        _maybe_flash("Vuelve a conectar Google (token inválido o revocado).", "error")
        # Nota: no podemos borrar secrets de entorno en tiempo de ejecución.
        raise RuntimeError("Google OAuth RefreshError: token expirado o revocado. Vuelve a conectar Google.") from e

    _thread_services.user_credentials = creds
    return creds

def get_drive_service_user(timeout=None, fresh=False):
    """Drive del usuario con una conexión independiente por hilo."""
    if fresh:
        reset_thread_google_services()
    return _thread_service(
        "drive_user", "drive", "v3", _user_credentials(), timeout=timeout, fresh=fresh
    )

def get_sheets_service_user(timeout=None, fresh=False):
    """Sheets del usuario con una conexión independiente por hilo."""
    if fresh:
        reset_thread_google_services()
    return _thread_service(
        "sheets_user", "sheets", "v4", _user_credentials(), timeout=timeout, fresh=fresh
    )



