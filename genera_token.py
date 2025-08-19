# genera_token.py
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
import json, os

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

def main():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        if creds and creds.valid:
            print("token.json ya es válido.")
            return
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open("token.json", "w", encoding="utf-8") as f:
                f.write(creds.to_json())
            print("token.json refrescado.")
            return

    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    # Fuerza nuevo consentimiento para obtener refresh_token
    creds = flow.run_local_server(prompt="consent", access_type="offline", include_granted_scopes="true")
    with open("token.json", "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print("token.json generado.")

if __name__ == "__main__":
    main()
