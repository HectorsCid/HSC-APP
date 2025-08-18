# _subir_drive_playground.py
# Playground helper; sin Flask ni blueprints para que no interfiera con la app.

import mimetypes
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

def drive_service():
    creds = Credentials.from_authorized_user_file('token.json', [
        'https://www.googleapis.com/auth/drive'
    ])
    return build('drive', 'v3', credentials=creds)

def upsert_file(local_path, name, parent_id):
    """Sube un archivo a Drive a la carpeta parent_id. Devuelve {id, webViewLink}."""
    service = drive_service()
    mime = mimetypes.guess_type(local_path)[0] or 'application/pdf'
    media = MediaFileUpload(local_path, mimetype=mime)
    meta = {'name': name, 'parents': [parent_id]}
    return service.files().create(body=meta, media_body=media,
                                  fields='id,webViewLink').execute()

if __name__ == "__main__":
    # ejemplo opcional:
    # print(upsert_file(r'cotizaciones\ejemplo.pdf', 'ejemplo.pdf', 'ID_DE_CARPETA'))
    pass
