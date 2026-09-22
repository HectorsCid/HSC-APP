"""Staff-only repair history endpoints. All mutations use createReports permission."""
import io
from flask import Blueprint, current_app, jsonify, render_template, request, session
import operation_repairs as repairs


def create_repair_blueprint(store, role, forbidden, permission, uploader, photo_response, full_photo_response=None):
    bp = Blueprint('repairs', __name__)

    def identity():
        actor = str(session.get('hsc_user_id') or '')
        admin = role() == 'admin'
        if not actor and not admin:
            raise PermissionError('Vuelve a iniciar sesión para identificar tu cuenta.')
        return actor or 'owner', admin

    @bp.before_request
    def access():
        denied = forbidden('admin', 'technician')
        if denied:
            return denied
        if request.method != 'GET':
            denied = permission('createReports')
            if denied:
                return denied
        if not store.enabled:
            return jsonify(ok=False, error='Base operativa no disponible. Tu captura local se conserva.'), 503
        if request.method != 'GET' and (request.content_length or 0) > 3 * 1024 * 1024:
            return jsonify(ok=False, error='El envío es demasiado grande. Envía las fotos comprimidas una por una.'), 413

    @bp.errorhandler(Exception)
    def error(exc):
        if isinstance(exc, PermissionError):
            code = 403
        elif isinstance(exc, LookupError):
            code = 404
        elif isinstance(exc, repairs.Conflict):
            code = 409
        elif isinstance(exc, (ValueError, TypeError)):
            code = 400
        else:
            current_app.logger.exception('Error en bitácora de reparaciones')
            return jsonify(ok=False, error='No se confirmó el envío. Tu reparación local se conserva para reintentar.'), 503
        return jsonify(ok=False, error=str(exc)), code

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @bp.get('/api/operaciones/repairs')
    def index():
        actor, admin = identity()
        offset = max(0, int(request.args.get('offset', 0)))
        rows, following = repairs.listing(store, actor, admin, query=repairs.clean(request.args.get('q', ''), 200),
                                         equipment_key=repairs.clean(request.args.get('equipment', ''), 120), offset=offset)
        return jsonify(ok=True, repairs=rows, next_offset=following)

    @bp.get('/api/operaciones/repairs/<ident>')
    def detail(ident):
        return jsonify(ok=True, repair=repairs.read(store, ident, *identity()))

    @bp.post('/api/operaciones/repairs')
    def save():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError('Reparación no válida.')
        actor, admin = identity()
        author = session.get('hsc_user_name') or ('Administrador' if admin else actor)
        return jsonify(ok=True, repair=repairs.save(store, body, actor, admin, author))

    @bp.post('/api/operaciones/repairs/<ident>/photos/<pid>')
    def photo(ident, pid):
        actor, admin = identity()
        repairs.read(store, ident, actor, admin, edit=True)  # authorize before reading bytes
        file = request.files.get('file')
        if not file:
            raise ValueError('Selecciona una fotografía.')
        content = file.stream.read(2 * 1024 * 1024 + 1)
        if len(content) > 2 * 1024 * 1024:
            raise ValueError('La fotografía debe comprimirse a menos de 2 MB.')
        from PIL import Image
        try:
            with Image.open(io.BytesIO(content)) as img:
                if img.format not in ('JPEG', 'PNG', 'WEBP') or img.width * img.height > 20000000:
                    raise ValueError()
                img.verify()
        except Exception:
            raise ValueError('La fotografía no es una imagen válida o es demasiado grande.')
        repairs.attach(store, ident, pid, request.form.get('mutation_id'), content, actor, admin, uploader)
        return jsonify(ok=True)

    @bp.post('/api/operaciones/repairs/<ident>/finish')
    def finish(ident):
        body = request.get_json(silent=True) or {}
        return jsonify(ok=True, repair=repairs.finish(store, ident, body.get('mutation_id'), *identity()))

    @bp.post('/api/operaciones/repairs/<ident>/delete')
    def delete_visit(ident):
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise ValueError('Solicitud no válida.')
        actor, admin = identity()
        return jsonify(ok=True, repair=repairs.delete_visit(
            store, ident, body.get('mutation_id'), body.get('expected_revision'), actor, admin))

    @bp.get('/api/operaciones/repairs/<ident>/photos/<pid>')
    def image(ident, pid):
        repair = repairs.read(store, ident, *identity())
        if pid not in {p['id'] for p in repair['photos']}:
            raise LookupError('Foto no disponible.')
        with store.connection() as conn:
            ref = repairs.photo_refs(store, conn, ident).get(pid)
        if not ref:
            raise LookupError('Foto aún pendiente de envío.')
        if request.args.get('full') == '1' and full_photo_response:
            return full_photo_response(ref)
        return photo_response('repair', f'{ident}:{pid}', ref)

    @bp.get('/api/operaciones/repairs/<ident>/remision')
    def remision(ident):
        repair = repairs.read(store, ident, *identity())
        if repair['status'] != 'completed':
            raise repairs.Conflict('Finaliza la visita y confirma sus fotos antes de emitir la remisión.')
        return render_template('repair_remision.html', repair=repair, format_turns=repairs.format_turns)

    return bp
