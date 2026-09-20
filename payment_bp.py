"""Private administrator-only payment accounts. Never exposed in bootstrap."""
from flask import Blueprint, current_app, jsonify, request, session
import operation_payments as payments


def create_payment_blueprint(store, forbidden):
    bp = Blueprint('payments', __name__)

    @bp.before_request
    def access():
        denied = forbidden('admin')
        if denied:
            return denied
        if not store.enabled:
            return jsonify(ok=False, error='La base de pagos no está disponible.'), 503
        if (request.content_length or 0) > 16000:
            return jsonify(ok=False, error='Solicitud demasiado grande.'), 413

    @bp.after_request
    def private(response):
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    @bp.errorhandler(Exception)
    def error(exc):
        if isinstance(exc, payments.Conflict):
            code = 409
        elif isinstance(exc, LookupError):
            code = 404
        elif isinstance(exc, (ValueError, TypeError)):
            code = 400
        else:
            current_app.logger.exception('No se confirmó la operación de pagos')
            return jsonify(ok=False, error='No se pudo confirmar. Conserva esta pantalla y reintenta el mismo movimiento.'), 503
        return jsonify(ok=False, error=str(exc)), code

    @bp.get('/api/operaciones/payments')
    def index():
        return jsonify(ok=True, accounts=payments.listing(store))

    @bp.get('/api/operaciones/payments/<user_id>')
    def detail(user_id):
        return jsonify(ok=True, account=payments.detail(store, user_id, offset=request.args.get('offset', 0)))

    @bp.post('/api/operaciones/payments/<user_id>')
    def change(user_id):
        result = payments.mutate(store, user_id, request.get_json(silent=True), str(session.get('hsc_user_id') or 'owner'))
        return jsonify(result)

    return bp
