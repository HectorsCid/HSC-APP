from __future__ import print_function
import os.path
import mimetypes

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request

SCOPES = ['https://www.googleapis.com/auth/drive.file']

from google.oauth2 import service_account

def autenticar():
    creds = service_account.Credentials.from_service_account_file(
        'service_account.json',
        scopes=['https://www.googleapis.com/auth/drive.file']
    )
    return build('drive', 'v3', credentials=creds)

def obtener_o_crear_carpeta(service, nombre_carpeta, id_padre=None):
    query = f"name='{nombre_carpeta}' and mimeType='application/vnd.google-apps.folder'"
    if id_padre:
        query += f" and '{id_padre}' in parents"

    resultados = service.files().list(q=query, spaces='drive',
                                      fields="files(id, name)").execute()
    items = resultados.get('files', [])

    if items:
        return items[0]['id']
    else:
        metadata = {
            'name': nombre_carpeta,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if id_padre:
            metadata['parents'] = [id_padre]

        carpeta = service.files().create(body=metadata, fields='id').execute()
        return carpeta.get('id')

def subir_a_drive(nombre_archivo, ruta_local):
    service = autenticar()

    # Crear ruta: cotizaciones/EJEMPLO
    carpeta_principal = obtener_o_crear_carpeta(service, "cotizaciones")
    carpeta_destino = obtener_o_crear_carpeta(service, "EJEMPLO", carpeta_principal)

    # Subir archivo
    file_metadata = {
        'name': nombre_archivo,
        'parents': [carpeta_destino]
    }

    mime_type = mimetypes.guess_type(ruta_local)[0] or 'application/pdf'
    media = MediaFileUpload(ruta_local, mimetype=mime_type)

    archivo = service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()

    print(f"✅ Archivo subido a Drive:")
    print(f"🆔 ID: {archivo.get('id')}")
    print(f"🔗 Link: {archivo.get('webViewLink')}")

if __name__ == '__main__':
    subir_a_drive("ejemplo.pdf", r"C:\Users\rotce\OneDrive\Documentos\Cotizador-hsc\cotizaciones\ejemplo.pdf")

