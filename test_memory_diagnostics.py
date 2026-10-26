import gc
import io
import logging
import os
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch
import memory_diagnostics as diag
from operaciones_store import OperationsStore


class MemoryDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'HSC_MEMORY_DIAGNOSTICS': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.monitor = diag.MemoryMonitor(sample=lambda: dict(rss_mb=100., container_mb=200., limit_mb=512.))
        self.monitor.start = lambda: None

    def test_overlap_peak_end_and_exceptions_keep_behavior(self):
        with patch.object(diag, 'MONITOR', self.monitor), self.assertLogs(diag.LOG, level='WARNING') as logs:
            @diag.trace_memory('photo')
            def photo(secret):
                self.monitor.sample = lambda: dict(rss_mb=140., container_mb=450., limit_mb=512.)
                self.monitor.tick()
                return secret
            with diag.memory_span('matrix_sync'):
                self.assertEqual(photo('PASSWORD_NOT_FOR_LOGS'), 'PASSWORD_NOT_FOR_LOGS')
            @diag.trace_memory('pdf')
            def fail():
                raise ValueError('PRIVATE_MESSAGE_NOT_FOR_LOGS')
            with self.assertRaises(ValueError):
                fail()
        events = list(self.monitor.recent)
        self.assertTrue(any(e.get('overlap') == ['matrix_sync', 'photo'] for e in events))
        self.assertTrue(any(e.get('peak_container_mb') == 450 for e in events))
        self.assertTrue(any(e.get('outcome') == 'ValueError' for e in events))
        self.assertFalse(self.monitor.active)
        self.assertNotIn('PASSWORD_NOT_FOR_LOGS', ''.join(logs.output))
        self.assertNotIn('PRIVATE_MESSAGE_NOT_FOR_LOGS', ''.join(logs.output))

    def test_bounded_and_auto_expiry(self):
        with patch.object(diag.LOG, 'warning'):
            self.monitor.sink = lambda events: None
            for _ in range(400):
                ident = self.monitor.begin('photo')
                self.monitor.end(ident)
            self.assertEqual(len(self.monitor.recent), 200)
            self.assertEqual(self.monitor.pending.qsize(), 64)
            self.assertGreater(self.monitor.dropped, 0)
            self.monitor.flush()
            self.assertEqual(self.monitor.pending.qsize(), 32)
            self.monitor.started = time.monotonic()-25*3600
            self.assertIsNone(self.monitor.begin('photo'))
        with patch.dict(os.environ, {'HSC_MEMORY_DIAGNOSTICS': '0'}):
            self.assertFalse(self.monitor.enabled())

    def test_no_data_growth_after_warmup(self):
        # A Mock would retain every logging call and measure the test harness,
        # not the bounded telemetry buffer.
        with patch.object(diag.LOG, 'warning', lambda *args, **kwargs: None):
            gc.collect()
            tracemalloc.start()
            try:
                for _ in range(1000):
                    self.monitor.end(self.monitor.begin('photo'))
                gc.collect()
                first = tracemalloc.get_traced_memory()[0]
                for _ in range(1000):
                    self.monitor.end(self.monitor.begin('photo'))
                gc.collect()
                second, peak = tracemalloc.get_traced_memory()
                self.assertLess(second-first, 64*1024)
                self.assertLess(peak, 1024*1024)
                print('Telemetry Python heap after 1000/2000 operations:', round(first/1024), round(second/1024), 'KiB; peak:', round(peak/1024), 'KiB')
            finally:
                tracemalloc.stop()

    def test_persistence_bounded_and_survives_new_reader(self):
        with tempfile.TemporaryDirectory() as folder:
            store = OperationsStore(local_path=Path(folder)/'test.sqlite3')
            events = [dict(at=f'2026-10-26T12:{i//60:02}:{i%60:02}+00:00', instance='test', operation='photo', phase='end') for i in range(250)]
            diag.persist_events(store, events)
            with store.connection() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM operations_diagnostic_events').fetchone()[0], 200)
            self.assertEqual(len(diag.read_events(OperationsStore(local_path=Path(folder)/'test.sqlite3'))), 100)

    def test_sampler_and_sink_failure_never_break_work(self):
        def broken():
            raise OSError('Secret connection string')
        self.monitor.sample = broken
        with patch.object(diag.LOG, 'warning'):
            self.monitor.sink = lambda _: broken()
            self.monitor.end(self.monitor.begin('photo'))
            self.monitor.flush()
        self.assertFalse(self.monitor.persistence_ok)
        self.assertEqual(self.monitor.safe_sample()['rss_mb'], None)

    def test_request_and_safe_error_handler(self):
        from flask import Flask
        app = Flask(__name__)
        app.add_url_rule('/billing', endpoint='facturacion.test', view_func=lambda: ('failure', 500))
        store = OperationsStore()
        with patch.object(diag, 'MONITOR', self.monitor), patch.object(diag.LOG, 'warning'):
            diag.install_memory_diagnostics(app, store)
            self.addCleanup(logging.getLogger().removeHandler, app.extensions['hsc_memory_error_handler'])
            self.assertEqual(app.test_client().get('/billing').status_code, 500)
            logging.getLogger('test').error('SECRET VALUE NOT RETAINED')
        self.assertTrue(any(e.get('outcome') == 'http_500' for e in self.monitor.recent))
        self.assertTrue(any(e['operation'] == 'server_error' for e in self.monitor.recent))
        self.assertNotIn('SECRET VALUE', str(list(self.monitor.recent)))


if __name__ == '__main__':
    unittest.main()
