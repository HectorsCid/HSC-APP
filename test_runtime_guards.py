import threading
import time
import unittest
import ssl
from unittest.mock import MagicMock, patch

import auth_google
import pdf_runtime
import reportes_bp


class _FakeHTML:
    state_lock = threading.Lock()
    active = 0
    max_active = 0

    def __init__(self, *args, **kwargs):
        pass

    def write_pdf(self, *args, **kwargs):
        with self.state_lock:
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
        try:
            time.sleep(0.08)
            return b"%PDF-test"
        finally:
            with self.state_lock:
                type(self).active -= 1


class RuntimeGuardTests(unittest.TestCase):
    def test_pdf_renders_are_serialized_between_threads(self):
        _FakeHTML.active = 0
        _FakeHTML.max_active = 0
        results = []

        def render():
            results.append(pdf_runtime.render_pdf_bytes("<p>ok</p>", wait_timeout=2))

        with patch.object(pdf_runtime, "HTML", _FakeHTML):
            threads = [threading.Thread(target=render) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(results, [b"%PDF-test", b"%PDF-test"])
        self.assertEqual(_FakeHTML.max_active, 1)

    def test_google_services_are_reused_in_thread_but_not_shared_between_threads(self):
        built = []

        def fake_build(*args, **kwargs):
            service = object()
            built.append(service)
            return service

        with (
            patch.object(auth_google, "build", side_effect=fake_build),
            patch.object(auth_google, "AuthorizedHttp", side_effect=lambda *args, **kwargs: object()),
            patch.object(auth_google.httplib2, "Http", side_effect=lambda *args, **kwargs: object()),
            patch.object(auth_google, "_sa_credentials", return_value=object()),
        ):
            auth_google.reset_thread_google_services()
            main_first = auth_google.get_drive_service()
            main_second = auth_google.get_drive_service()
            from_thread = []

            def obtain_service():
                auth_google.reset_thread_google_services()
                from_thread.append(auth_google.get_drive_service())

            thread = threading.Thread(target=obtain_service)
            thread.start()
            thread.join()

        self.assertIs(main_first, main_second)
        self.assertIsNot(main_first, from_thread[0])
        self.assertEqual(len(built), 2)

    def test_light_pages_start_without_waiting_for_google(self):
        import app as app_module

        app_module.__did_sync_once = True
        app_module.__did_sync_cotizaciones_once = True
        client = app_module.app.test_client()

        healthz = client.get("/healthz")
        with patch.object(app_module, "_health_snapshot", side_effect=AssertionError("no debe llamarse")):
            home = client.get("/inicio-app")
        reports = client.get("/reportes")

        self.assertEqual(healthz.status_code, 200)
        self.assertEqual(home.status_code, 200)
        self.assertEqual(reports.status_code, 200)

    def test_google_retry_rebuilds_the_service(self):
        broken = MagicMock()
        broken.spreadsheets.return_value.values.return_value.get.return_value.execute.side_effect = ssl.SSLError("bad socket")
        healthy = MagicMock()
        healthy.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {"values": [["ok"]]}

        with (
            patch.object(reportes_bp, "_sheets_service", side_effect=[broken, healthy]) as service_getter,
            patch.object(reportes_bp, "_discard_reportes_google_connections"),
            patch.object(reportes_bp.time, "sleep"),
        ):
            result = reportes_bp._values_get("Reportes!A1:A1")

        self.assertEqual(result, {"values": [["ok"]]})
        self.assertEqual(service_getter.call_count, 2)

    def test_health_refresh_is_non_blocking_and_single_flight(self):
        import app as app_module

        entered = threading.Event()
        release = threading.Event()
        calls = []

        def slow_health():
            calls.append(1)
            entered.set()
            release.wait(2)
            return {**app_module._empty_ui_health(), "drive_ok": True}

        with patch.object(app_module, "_compute_ui_health", side_effect=slow_health):
            with app_module._HEALTH_CACHE_LOCK:
                app_module._HEALTH_CACHE.update(ts=0.0, payload=None)
            client = app_module.app.test_client()
            started = time.monotonic()
            first = client.get("/health")
            elapsed = time.monotonic() - started
            self.assertTrue(entered.wait(1))
            second = client.get("/health")

            self.assertLess(elapsed, 0.2)
            self.assertTrue(first.get_json()["checking"])
            self.assertTrue(second.get_json()["refreshing"])
            self.assertEqual(len(calls), 1)

            release.set()
            deadline = time.monotonic() + 2
            while app_module._HEALTH_REFRESH_LOCK.locked() and time.monotonic() < deadline:
                time.sleep(0.01)

            final = client.get("/health").get_json()
            self.assertTrue(final["drive_ok"])
            self.assertFalse(final["refreshing"])

    def test_render_blocks_writes_until_drive_bootstrap_is_confirmed(self):
        import app as app_module

        client = app_module.app.test_client()
        with (
            patch.object(app_module, "IS_RENDER", True),
            patch.object(app_module, "__did_sync_once", False),
            patch.object(app_module, "__did_sync_cotizaciones_once", False),
        ):
            add_client = client.get("/nuevo_cliente")
            generate_quote = client.get("/generar_pdf")

        self.assertEqual(add_client.status_code, 503)
        self.assertEqual(generate_quote.status_code, 503)
        self.assertEqual(add_client.headers.get("Retry-After"), "10")


if __name__ == "__main__":
    unittest.main()
