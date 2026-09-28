"""Canonical Drive identities, persisted before create and shared by all workers."""
import hashlib
import io
import json
from googleapiclient.http import MediaIoBaseUpload
from operaciones_store import ReportConflictError

FOLDER = 'application/vnd.google-apps.folder'


def resource_key(parent, name, folder=False):
    value = json.dumps([parent, name, 'folder' if folder else 'file'], ensure_ascii=False)
    return 'drive:' + hashlib.sha256(value.encode()).hexdigest()


def list_all(drive, query):
    result, token = [], None
    while True:
        options = dict(q=query, spaces='drive', fields='nextPageToken,files(id,name,mimeType,parents,createdTime,md5Checksum,trashed)', pageSize=1000)
        if token:
            options['pageToken'] = token
        page = drive.files().list(**options).execute()
        result.extend(page.get('files', []))
        token = page.get('nextPageToken')
        if not token:
            return result


def _metadata(drive, ident):
    try:
        return drive.files().get(fileId=ident, fields='id,name,mimeType,parents,md5Checksum,trashed').execute()
    except Exception as exc:
        if getattr(getattr(exc, 'resp', None), 'status', None) == 404:
            return None
        raise


def _access_signature(drive, ident):
    metadata = drive.files().get(fileId=ident, fields='id,permissions(id,role,type,allowFileDiscovery),inheritedPermissionsDisabled').execute()
    if 'permissions' not in metadata:
        return None  # An incomplete view must never authorize moving files.
    return (bool(metadata.get('inheritedPermissionsDisabled')), sorted(
        json.dumps(permission, sort_keys=True) for permission in metadata['permissions']))


def ensure_object(store, drive, parent, name, *, content=None, mime=FOLDER, immutable=False, photo_identity=''):
    folder = content is None
    key = resource_key(parent, 'photo:' + photo_identity if photo_identity else name, folder)
    digest = hashlib.md5(content).hexdigest() if content is not None else ''
    with store.resource_lock(key):
        saved = store.drive_registry(key)
        safe_name, safe_parent = name.replace("'", "\\'"), parent.replace("'", "\\'")
        query = f"'{safe_parent}' in parents and trashed=false"
        if not photo_identity:
            query = f"name='{safe_name}' and " + query
        if folder:
            query += f" and mimeType='{FOLDER}'"
        existing = list_all(drive, query)
        if photo_identity:
            # Slot numbers can change when a lease expires. The content/mutation
            # identity, not the slot, defines the canonical file. Drive's name
            # 'contains' only matches prefixes, so filter this bounded folder.
            existing = [row for row in existing if f'.{photo_identity}.' in row.get('name', '')]
        existing.sort(key=lambda row: (row.get('createdTime', ''), row['id']))
        if not saved:
            matching = existing if folder else [row for row in existing if row.get('md5Checksum') == digest]
            chosen = (matching or existing)[0] if existing else None
            ident = chosen['id'] if chosen else drive.files().generateIds(count=1, space='drive', type='files').execute()['ids'][0]
            saved = dict(id=ident, parent=parent, name=chosen.get('name', name) if chosen else name, folder=folder, state='reserved', digest=digest)
            # Commit the generated ID before sending bytes. A process restart or
            # an accepted create with a lost response reuses this exact ID.
            store.drive_registry(key, saved)
        ident = saved['id']
        duplicates = [row['id'] for row in existing if row['id'] != ident]
        saved['duplicates'] = sorted(set(saved.get('duplicates', [])) | set(duplicates))
        store.drive_registry(key, saved)
        current = next((row for row in existing if row['id'] == ident), None) or _metadata(drive, ident)
        if current and current.get('trashed'):
            raise ValueError('La carpeta o archivo canónico está en la papelera de Drive. Restáuralo para continuar.')
        media = None if folder else MediaIoBaseUpload(io.BytesIO(content), mimetype=mime, resumable=False)
        if current is None:
            metadata = dict(id=ident, name=saved['name'], parents=[parent], mimeType=mime)
            try:
                current = drive.files().create(body=metadata, media_body=media, fields='id,md5Checksum').execute()
            except Exception as exc:
                if getattr(getattr(exc, 'resp', None), 'status', None) != 409:
                    raise
                current = _metadata(drive, ident)
                if not current:
                    raise
        if not folder and current.get('md5Checksum') != digest:
            if immutable:
                raise ReportConflictError('El archivo de esta fotografía ya contiene otros bytes. Se conservó el original.', code='photo_content_conflict')
            drive.files().update(fileId=ident, media_body=media, fields='id').execute()
        if folder:
            # Consolidate without deleting folders or files. IDs remain valid;
            # child names remain unchanged, even if both copies have equal names.
            access = _access_signature(drive, ident) if saved['duplicates'] else None
            saved['access_review'] = []
            for alias in saved['duplicates']:
                if access is None or access != _access_signature(drive, alias):
                    saved['access_review'].append(alias)
                    continue
                for child in list_all(drive, f"'{alias}' in parents and trashed=false"):
                    drive.files().update(fileId=child['id'], addParents=ident, removeParents=alias, fields='id,parents').execute()
                # Keep the emptied folder and its ID, but remove path ambiguity
                # for AppSheet and older clients which resolve names rather than IDs.
                drive.files().update(fileId=alias, body={'name': f'{name} (duplicado conservado {alias})'}, fields='id').execute()
        saved.update(state='confirmed', digest=digest)
        store.drive_registry(key, saved)
        return ident
