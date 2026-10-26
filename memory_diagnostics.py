"""Bounded, temporary telemetry. Never records request bodies, IDs or credentials."""
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import itertools
import json
import logging
import os
import queue
import threading
import time
import uuid

LOG = logging.getLogger(__name__)
BACKGROUND = {'reportes-auto-pdf', 'operations-sheets-sync', 'google-bootstrap-sync', 'hsc-push-delivery'}


def sample_memory():
    from pdf_runtime import memory_snapshot
    return memory_snapshot()


class MemoryMonitor:
    def __init__(self, sample=sample_memory):
        self.sample = sample
        self.lock = threading.RLock()
        self.active = {}
        self.recent = deque(maxlen=200)
        self.pending = queue.Queue(maxsize=64)
        self.counter = itertools.count(1)
        self.sink = None
        self.thread = None
        self.stop = threading.Event()
        self.started = time.monotonic()
        self.last_heartbeat = 0
        self.last_peak = 0
        self.last_flush = 0
        self.last_logged_memory = 0
        self.dropped = 0
        self.persistence_ok = None
        self.instance = str(os.getpid()) + '-' + uuid.uuid4().hex[:8]
        self.paused = False

    def enabled(self):
        setting = os.getenv('HSC_MEMORY_DIAGNOSTICS', '1' if os.getenv('RENDER') else '0').strip().lower()
        try:
            hours = min(168, max(1, float(os.getenv('HSC_MEMORY_DIAGNOSTICS_HOURS', '24'))))
        except ValueError:
            hours = 24
        return not self.paused and setting in {'1', 'true', 'yes'} and time.monotonic() - self.started < hours * 3600

    def safe_sample(self):
        try:
            snapshot = self.sample()
            return {key: value if isinstance(value := snapshot.get(key), (int, float)) else None
                    for key in ('rss_mb', 'container_mb', 'limit_mb')}
        except Exception:
            return dict(rss_mb=None, container_mb=None, limit_mb=None)

    def emit(self, operation, phase, snapshot=None, **details):
        snapshot = snapshot or self.safe_sample()
        event = dict(at=datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                     instance=self.instance, operation=operation, phase=phase, **snapshot, **details)
        with self.lock:
            self.recent.append(event)
        LOG.warning('HSC_MEMORY_DIAG %s', json.dumps(event, ensure_ascii=True, separators=(',', ':')))
        if self.sink:
            try:
                self.pending.put_nowait(event)
            except queue.Full:
                self.dropped += 1
        return event

    def start(self):
        with self.lock:
            if self.thread is not None:
                return
            self.thread = threading.Thread(target=self.run, name='hsc-memory-diagnostics', daemon=True)
            self.thread.start()

    def begin(self, operation):
        if not self.enabled():
            return None
        snapshot = self.safe_sample()
        with self.lock:
            if len(self.active) >= 64:
                self.dropped += 1
                return None
            ident = next(self.counter)
            self.active[ident] = dict(operation=operation, started=time.monotonic(),
                                      peak_rss_mb=snapshot['rss_mb'], peak_container_mb=snapshot['container_mb'])
            overlap = sorted({v['operation'] for v in self.active.values()})
        self.emit(operation, 'start', snapshot, span=ident, overlap=overlap)
        self.start()
        return ident

    def end(self, ident, outcome='ok'):
        if ident is None:
            return
        snapshot = self.safe_sample()
        with self.lock:
            span = self.active.pop(ident, None)
        if not span:
            return
        for key in ('rss_mb', 'container_mb'):
            span['peak_' + key] = max((v for v in (span['peak_' + key], snapshot[key]) if v is not None), default=None)
        self.emit(span['operation'], 'end', snapshot, span=ident, outcome=outcome,
                  duration_ms=round((time.monotonic() - span['started']) * 1000),
                  peak_rss_mb=span['peak_rss_mb'], peak_container_mb=span['peak_container_mb'])

    def tick(self):
        snapshot = self.safe_sample()
        now = time.monotonic()
        with self.lock:
            for span in self.active.values():
                for key in ('rss_mb', 'container_mb'):
                    span['peak_' + key] = max((v for v in (span['peak_' + key], snapshot[key]) if v is not None), default=None)
            overlap = sorted({v['operation'] for v in self.active.values()})
            spans = list(self.active)
        used, limit = snapshot['container_mb'], snapshot['limit_mb']
        high = bool(used is not None and limit and used / limit >= .85)
        growth = bool(used is not None and used >= self.last_logged_memory + 8)
        if (high or growth) and now - self.last_peak >= 5:
            self.last_peak = now
            self.last_logged_memory = used or 0
            self.emit('sampler', 'high_memory' if high else 'growth', snapshot, overlap=overlap, spans=spans,
                      background=sorted({t.name for t in threading.enumerate() if t.name in BACKGROUND}))
        elif now - self.last_heartbeat >= 60:
            self.last_heartbeat = now
            self.emit('sampler', 'heartbeat', snapshot, overlap=overlap,
                      background=sorted({t.name for t in threading.enumerate() if t.name in BACKGROUND}))

    def flush(self):
        batch = []
        for _ in range(32):
            try:
                batch.append(self.pending.get_nowait())
            except queue.Empty:
                break
        if batch and self.sink:
            try:
                self.sink(batch)
                self.persistence_ok = True
            except Exception:
                # Diagnostics must never crash the application or recursively log.
                self.persistence_ok = False

    def run(self):
        while not self.stop.wait(.5):
            try:
                if self.enabled():
                    self.tick()
                now = time.monotonic()
                if now - self.last_flush >= 5:
                    self.last_flush = now
                    self.flush()
                if not self.enabled() and not self.active:
                    self.flush()
                    self.flush()
                    return
            except Exception:
                pass


MONITOR = MemoryMonitor()


@contextmanager
def memory_span(operation):
    ident, outcome = None, 'ok'
    try:
        ident = MONITOR.begin(operation)
    except Exception:
        pass
    try:
        yield
    except BaseException as exc:
        outcome = type(exc).__name__
        raise
    finally:
        try:
            MONITOR.end(ident, outcome)
        except Exception:
            pass


def trace_memory(operation):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with memory_span(operation):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def persist_events(store, events):
    store.initialize()
    p = store.placeholder
    with store.connection() as conn:
        if store.dialect == 'postgres':
            conn.execute('SELECT pg_advisory_xact_lock(72146982110422)')
        else:
            conn.execute('BEGIN IMMEDIATE')
        for event in events:
            conn.execute(f'INSERT INTO operations_diagnostic_events(id,created_at,payload_json) VALUES ({p},{p},{p})',
                         (uuid.uuid4().hex, event['at'], json.dumps(event, ensure_ascii=True)))
        conn.execute('DELETE FROM operations_diagnostic_events WHERE id NOT IN '
                     '(SELECT id FROM operations_diagnostic_events ORDER BY created_at DESC,id DESC LIMIT 200)')


def read_events(store):
    store.initialize()
    with store.connection() as conn:
        return [json.loads(row[0]) for row in conn.execute(
            'SELECT payload_json FROM operations_diagnostic_events ORDER BY created_at DESC,id DESC LIMIT 100').fetchall()]


def install_memory_diagnostics(app, store):
    from flask import g, request
    MONITOR.sink = lambda events: persist_events(store, events) if store.enabled else None

    @app.before_request
    def memory_request_start():
        endpoint = request.endpoint or ''
        operation = None
        if endpoint.startswith(('facturacion.', 'pagos.')):
            operation = 'billing_request'
        elif endpoint.startswith('reportes.'):
            operation = 'reports_request'
        elif request.path.startswith('/api/operaciones/reports') and request.method != 'GET':
            operation = 'report_save'
        if operation and MONITOR.enabled():
            try:
                g.hsc_memory_span = MONITOR.begin(operation)
            except Exception:
                pass

    @app.after_request
    def memory_request_end(response):
        try:
            ident = g.pop('hsc_memory_span', None)
            MONITOR.end(ident, 'http_' + str(response.status_code))
        except Exception:
            pass
        return response

    @app.teardown_request
    def memory_request_error(error):
        try:
            MONITOR.end(g.pop('hsc_memory_span', None), type(error).__name__ if error else 'closed')
        except Exception:
            pass

    class SafeErrorHandler(logging.Handler):
        def emit(self, record):
            if record.name == __name__ or not MONITOR.enabled():
                return
            try:
                kind = record.exc_info[0].__name__ if record.exc_info else 'ServerError'
                MONITOR.emit('server_error', 'error', error_type=kind)
                MONITOR.start()
            except Exception:
                pass
    handler = SafeErrorHandler(level=logging.ERROR)
    logging.getLogger().addHandler(handler)
    app.extensions['hsc_memory_error_handler'] = handler
