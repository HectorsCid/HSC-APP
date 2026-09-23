"""Offline, isolated memory experiment; never calls production or uses its DB.

Run with Python 3.11 and the repository's dependency directory on sys.path.
Every case runs in a new process and a temporary working directory. Results
are Windows process working-set measurements, NOT Render container metrics.
"""
import argparse
import ast
import contextlib
import ctypes
from ctypes import wintypes
import gc
import io
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'venv311/Lib/site-packages')]
sys.path.append(str(ROOT.parent / '.work/memory-deps'))
sys.dont_write_bytecode = True


def no_network(*args, **kwargs):
    raise RuntimeError('Network disabled for the memory experiment')


class Counters(ctypes.Structure):
    _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
        (key, ctypes.c_size_t) for key in (
            'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
            'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage',
            'PrivateUsage')]


def memory():
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    getter = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
    getter.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not getter(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return {name: round(value / 1048576, 2) for name, value in {
        'rss_mib': counters.WorkingSetSize,
        'private_mib': counters.PrivateUsage,
        'process_peak_rss_mib': counters.PeakWorkingSetSize,
    }.items()}


def measure(label, operation):
    gc.collect()
    before = memory()
    maxima = dict(before)
    stop = threading.Event()

    def sample():
        while not stop.wait(.005):
            for key, value in memory().items():
                maxima[key] = max(maxima[key], value)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    started = time.perf_counter()
    try:
        details = operation()
    finally:
        elapsed = time.perf_counter() - started
        stop.set()
        sampler.join()
    after = memory()
    for key, value in after.items():
        maxima[key] = max(maxima[key], value)
    gc.collect()
    return dict(case=label, seconds=round(elapsed, 3), before=before,
                sampled_peak=maxima, after=after, after_gc=memory(), details=details)


def load_app(scratch):
    # Local credentials are neither needed nor allowed to choose a database.
    for key in list(os.environ):
        if any(word in key for word in ('DATABASE_URL', 'TOKEN', 'SERVICE_ACCOUNT', 'RENDER')):
            os.environ.pop(key, None)
    os.environ.update(REPORTES_AUTO_PDF='0', OPERACIONES_MATRIX_AUTO_SYNC='0',
                      OPERACIONES_SHEETS_SYNC_ENABLED='0', FLASK_SECRET_KEY='memory-test-only')
    os.chdir(scratch)
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.create_connection = no_network
    from operaciones_store import OperationsStore
    store = OperationsStore(local_path=Path(scratch) / 'experiment.sqlite3')
    with patch.object(OperationsStore, 'from_environment', return_value=store), \
            contextlib.redirect_stdout(io.StringIO()):
        import app as module
    module.app.testing = True
    module.AUTO_SYNC_FROM_DRIVE = False
    module.__did_sync_once = True
    module.__did_sync_cotizaciones_once = True
    module.__did_sync_borradores_once = True
    module.start_notifications = lambda app: None
    store.initialize()
    return module, store


def make_matrix():
    clients = [['ID_Cliente', 'NombreCliente', 'Direccion']]
    clients += [[f'C{i}', f'Cliente {i}', 'Direccion de prueba'] for i in range(12)]
    equipment = [['ID_Equipo', 'ID_Cliente', 'NombreEquipo', 'Marca', 'Estatus', 'Foto']]
    equipment += [[f'E{i}', f'C{i % 12}', f'Equipo {i}', 'Marca', 'Activo', f'e{i}.jpg'] for i in range(600)]
    headers = ['ID_Reporte', 'ID_Equipo', 'ID_Cliente', 'Ronda', 'Realizado'] + [f'Dato{i}' for i in range(35)]
    reports = [headers] + [[f'R{i}', f'E{i % 600}', f'C{i % 12}', str(i // 600 + 1), 'TRUE'] +
                         [f'medicion {i}-{j}' for j in range(35)] for i in range(1800)]
    return json.dumps({'valueRanges': [dict(range=key, values=value) for key, value in
                                      [('Clientes!A1:ZZ', clients), ('Equipos!A1:ZZ', equipment),
                                       ('Reportes!A1:ZZ', reports)]]})


def run_case(case, fixture, legacy_source):
    logging.disable(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix='hsc-memory-') as scratch, contextlib.chdir(scratch):
        module, store = load_app(scratch)
        if case == 'idle':
            client = module.app.test_client()
            def idle():
                for _ in range(500):
                    assert client.get('/healthz').status_code == 200
                return {'health_requests': 500, 'network': 'blocked'}
            return measure(case, idle)
        if case == 'google_clients':
            import auth_google
            from google.auth.credentials import AnonymousCredentials
            snapshots = []
            phases = threading.Barrier(3)

            def build_clients(worker):
                for cycle in range(5):
                    phases.wait()
                    for key, api, version, timeout in (
                        ('drive', 'drive', 'v3', 20),
                        ('sheets', 'sheets', 'v4', 20),
                        ('sheets_write', 'sheets', 'v4', 30),
                    ):
                        auth_google._thread_service(key, api, version, AnonymousCredentials(), timeout=timeout)
                    phases.wait()
                    if worker == 0:
                        snapshots.append(memory())
                    phases.wait()
                    auth_google.reset_thread_google_services()
                    phases.wait()
                    if worker == 0:
                        gc.collect()
                    phases.wait()

            def clients():
                with ThreadPoolExecutor(max_workers=3) as executor:
                    list(executor.map(build_clients, range(3)))
                return dict(workers=3, cycles=5, clients_per_worker=3, cycle_memory=snapshots,
                            network='blocked; real discovery/client constructors with anonymous credentials')
            return measure(case, clients)
        if case.startswith('thumbnails'):
            raw = Path(fixture).read_bytes()
            from flask import make_response
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as source:
                dimensions = source.size
            module.serve_drive_image_ref_fast = lambda ref: make_response(raw)
            if case.endswith('legacy'):
                tree = ast.parse(Path(legacy_source).read_text(encoding='utf-8'))
                function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_serve_operations_thumbnail')
                exec(compile(ast.Module(body=[function], type_ignores=[]), '<HEAD thumbnail>', 'exec'), module.__dict__)
                del tree, function
            generated = cached = 0
            counter_lock = threading.Lock()

            def fetch(index):
                nonlocal generated, cached
                with module.app.test_request_context('/'):
                    response = module._serve_operations_thumbnail('equipment', f'E{index}', f'source-{index}')
                    mode = response.headers.get('X-HSC-Thumbnail')
                    assert mode in {'cache', 'generated'}, mode
                    response.close()
                with counter_lock:
                    generated += mode == 'generated'
                    cached += mode == 'cache'

            def photos():
                with ThreadPoolExecutor(max_workers=2) as executor:
                    for _ in range(2):
                        list(executor.map(fetch, range(20)))
                return dict(generated=generated, cached=cached, image_dimensions=dimensions,
                            source_bytes=len(raw), workers=2)
            return measure(case, photos)
        if case == 'matrix':
            from operaciones_matrix import build_operaciones_bootstrap
            source = make_matrix()

            def sync():
                for _ in range(20):
                    response = json.loads(source)
                    payload = build_operaciones_bootstrap(response['valueRanges'], include_raw=True, include_media_refs=True)
                    counts = store.import_matrix_snapshot(payload)
                    snapshot = store.snapshot()
                    del snapshot, payload, response
                return dict(cycles=20, source_bytes=len(source), counts=counts,
                            excludes='Google transport, real sheet data and remote database')
            return measure(case, sync)
        if case in {'payments', 'payments_post'}:
            import operation_payments as payments
            from payment_bp import create_payment_blueprint
            from flask import Flask
            with store.connection() as conn:
                for i in range(10):
                    conn.execute('INSERT INTO operations_users(id,name,role,email,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                                 (f'T{i}', f'Tecnico {i}', 'technician', f't{i}@test.invalid', '2026-09-01', '2026-09-01'))
            for i in range(100):
                store.save_expense(dict(id=f'G{i}', user_id=f'T{i % 10}', technician_name='Prueba',
                                        expense_date='2026-09-21', amount=100, concept='Prueba de memoria'))
            local_app = Flask('payment-memory')
            local_app.secret_key = 'test-only'
            local_app.register_blueprint(create_payment_blueprint(store, lambda *args: None))
            client = local_app.test_client()
            if case == 'payments_post':
                payments.mutate(store, 'T0', dict(action='salary', mutation_id='setup-memory-pay',
                                expected_revision=0, weekly_amount='2000', effective_week='2026-09-13'),
                                'owner', date(2026, 9, 14))

            def pay():
                with patch.object(payments, 'today', return_value=date(2026, 9, 23)):
                    for i in range(100):
                        assert client.get('/api/operaciones/payments').status_code == 200
                        assert client.get('/api/operaciones/payments/T0').status_code == 200
                        body = dict(action='salary', mutation_id=f'memory-{i:06d}', expected_revision=i,
                                    weekly_amount='2000', effective_week='2026-09-20')
                        if case == 'payments_post':
                            body = dict(action='payment', mutation_id=f'memory-pay-{i:06d}',
                                        expected_revision=i+1, amount='1', paid_on='2026-09-23',
                                        week_start='2026-09-13', note='Synthetic payment')
                        response = client.post('/api/operaciones/payments/T0', json=body)
                        assert response.status_code == 200, response.get_json()
                return dict(cycles=100, requests=300, technicians=10, expenses=100,
                            writes='temporary SQLite only; no real payments')
            return measure(case, pay)
        raise ValueError(case)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case')
    parser.add_argument('--fixture')
    parser.add_argument('--legacy-source')
    parser.add_argument('--output')
    parser.add_argument('--cases', default='idle,payments,payments_post,matrix,google_clients,thumbnails_legacy,thumbnails_fixed')
    parser.add_argument('--width', type=int, default=4000)
    parser.add_argument('--height', type=int, default=3000)
    args = parser.parse_args()
    if args.case:
        print(json.dumps(run_case(args.case, args.fixture, args.legacy_source)))
        return
    results = []
    with tempfile.TemporaryDirectory(prefix='hsc-memory-fixtures-') as scratch:
        from PIL import Image
        fixture = Path(scratch) / 'photo.jpg'
        # Fixture preparation is in the PARENT, not counted in child peaks.
        with Image.new('RGB', (args.width, args.height), '#7b9fa1') as image:
            image.save(fixture, 'JPEG', quality=90)
        legacy = Path(scratch) / 'legacy_app.py'
        source = subprocess.check_output(['git', 'show', 'HEAD:app.py'], cwd=ROOT).decode('utf-8')
        function = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == '_serve_operations_thumbnail')
        legacy.write_text(ast.get_source_segment(source, function), encoding='utf-8')
        for case in args.cases.split(','):
            completed = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()),
                                       '--case', case, '--fixture', str(fixture),
                                       '--legacy-source', str(legacy)], cwd=scratch,
                                      capture_output=True, text=True, timeout=120,
                                      env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
            if completed.returncode:
                raise RuntimeError(f'{case}: {completed.stderr[-3000:]}')
            result = json.loads(completed.stdout.strip().splitlines()[-1])
            results.append(result)
            print(json.dumps(result), flush=True)
    if args.output:
        Path(args.output).write_text(json.dumps({'platform': sys.platform, 'results': results}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
