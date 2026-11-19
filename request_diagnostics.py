"""Bounded timing diagnostics: no query strings, bodies, credentials or photos."""
import threading
import time
import uuid
from flask import g, request


def install(app):
    active = {}
    lock = threading.Lock()
    worker = None

    def watch():
        while True:
            threading.Event().wait(10)
            now = time.monotonic()
            with lock:
                slow = [(ident, *row) for ident, row in active.items() if now-row[0] >= 10]
            for ident, started, method, route in slow[:10]:
                app.logger.warning('HSC_REQUEST phase=waiting id=%s method=%s route=%s elapsed_s=%.1f', ident, method, route, now-started)

    @app.before_request
    def begin():
        nonlocal worker
        if request.endpoint == 'static':
            return
        route = str(request.url_rule) if request.url_rule else 'unmatched'
        ident = uuid.uuid4().hex[:12]
        g.hsc_request = (ident, time.monotonic(), request.method, route)
        with lock:
            if len(active)<100:
                active[ident] = g.hsc_request[1:]
            if not app.testing and (worker is None or not worker.is_alive()):
                worker=threading.Thread(target=watch, name='hsc-request-watch', daemon=True)
                worker.start()

    @app.after_request
    def end(response):
        row = getattr(g, 'hsc_request', None)
        if row:
            ident,started,method,route=row
            elapsed=time.monotonic()-started
            response.headers['X-HSC-Request-ID']=ident
            if elapsed>=5 or response.status_code>=400:
                app.logger.warning('HSC_REQUEST phase=end id=%s method=%s route=%s status=%s elapsed_s=%.2f',ident,method,route,response.status_code,elapsed)
        return response

    @app.teardown_request
    def cleanup(error):
        row=getattr(g,'hsc_request',None)
        if row:
            with lock:
                active.pop(row[0],None)
