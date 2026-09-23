"""Regresiones de memoria: fotos de campo, caché de Drive y PDF simultáneos."""
import base64
import io
import threading
import unittest
from unittest.mock import mock_open, patch

from PIL import Image

import pdf_runtime
import photo_runtime
import reportes_bp as reports


def photo_bytes(size=(4000, 3000), *, mode="RGB", fmt="JPEG", orientation=None):
    with Image.new(mode, size, "white") as image, io.BytesIO() as output:
        options = {}
        if orientation:
            exif = Image.Exif()
            exif[274] = orientation
            options["exif"] = exif
        image.save(output, format=fmt, **options)
        return output.getvalue()


class MediaMemoryTests(unittest.TestCase):
    def setUp(self):
        reports._clear_pdf_photo_cache()
        self.addCleanup(reports._clear_pdf_photo_cache)

    def test_thumbnail_reduces_pixels_before_exif_copy_and_keeps_rotation(self):
        raw = photo_bytes(orientation=6)
        transpose = photo_runtime.ImageOps.exif_transpose
        copied_sizes = []

        def observe(image):
            copied_sizes.append(image.size)
            return transpose(image)

        with patch.object(photo_runtime.ImageOps, "exif_transpose", side_effect=observe):
            content, mime, width, height = photo_runtime.prepare_photo(
                raw, target_size=(360, 360), thumbnail=True)
        self.assertEqual(mime, "image/webp")
        self.assertLess(width, height)
        self.assertLessEqual(max(width, height), 360)
        self.assertTrue(all(max(size) <= 360 for size in copied_sizes))
        with Image.open(io.BytesIO(content)) as result:
            self.assertEqual(result.size, (width, height))

    def test_cached_original_is_still_compressed_before_pdf(self):
        ident = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
        original = photo_bytes()
        reports._cache_set_bytes(ident, original, "image/jpeg", "original.jpg")
        with patch.object(reports, "_resolve_shortcut", return_value=ident), \
             patch.object(reports, "_download_drive_image", side_effect=AssertionError("No red")):
            uri = reports._photo_data_uri(ident)
            with patch.object(reports, "_optimize_photo_bytes", side_effect=AssertionError("Ya optimizada")):
                self.assertEqual(reports._photo_data_uri(ident), uri)
        with Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as result:
            self.assertLessEqual(max(result.size), 1400)
        self.assertEqual(reports._cache_get_bytes(ident)[0], original)

    def test_photo_waits_for_pdf_and_nested_report_can_prepare_photos(self):
        raw = photo_bytes((80, 60))
        started = threading.Event()
        done = threading.Event()
        results, errors = [], []

        def prepare():
            started.set()
            try:
                results.append(photo_runtime.prepare_photo(raw, wait_timeout=2))
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()

        with pdf_runtime.pdf_render_slot():
            thread = threading.Thread(target=prepare)
            thread.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(done.wait(0.05))
            self.assertTrue(photo_runtime.prepare_photo(raw)[0])
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)

    def test_png_is_reduced_before_rotation_and_transparency_is_preserved(self):
        raw = photo_bytes((1600, 1200), mode="RGBA", fmt="PNG")
        content, mime, width, height = photo_runtime.prepare_photo(raw, target_size=(360, 360), thumbnail=True)
        self.assertEqual(mime, "image/webp")
        self.assertEqual((width, height), (360, 270))
        with Image.open(io.BytesIO(content)) as result:
            self.assertEqual(result.getpixel((0, 0))[:3], (255, 255, 255))

    def test_oversized_photo_rejected_before_decode(self):
        raw = photo_bytes((10, 10))
        with patch.object(photo_runtime, "MAX_SOURCE_PIXELS", 50):
            with self.assertRaises(ValueError):
                photo_runtime.prepare_photo(raw)
        with patch.object(photo_runtime, "MAX_PHOTO_BYTES", 4):
            with self.assertRaises(ValueError):
                photo_runtime.prepare_photo(b"12345")

    def test_drive_download_stops_at_byte_limit_and_closes_buffer(self):
        captured = []

        class Downloader:
            def __init__(self, target, request, chunksize):
                captured.append((target, chunksize))
                self.target = target

            def next_chunk(self):
                self.target.write(b"1234")
                return None, False

        from unittest.mock import MagicMock
        with patch.object(reports, "MediaIoBaseDownload", Downloader), \
             patch.object(reports, "MAX_PHOTO_BYTES", 8), \
             patch.object(reports, "_drive_img_call", side_effect=lambda fn: fn(MagicMock())):
            with self.assertRaisesRegex(ValueError, "supera"):
                reports._download_drive_image("test")
        self.assertEqual(captured[0][1], 1024 * 1024)
        self.assertTrue(captured[0][0].closed)

    def test_memory_snapshot_reads_container_limit_and_has_portable_fallback(self):
        def read(path, **kwargs):
            values = {"/sys/fs/cgroup/memory.current": str(300 * 1024 * 1024),
                      "/sys/fs/cgroup/memory.max": str(512 * 1024 * 1024)}
            if path not in values:
                raise FileNotFoundError(path)
            return mock_open(read_data=values[path])()

        with patch.object(pdf_runtime, "rss_megabytes", return_value=250), patch("builtins.open", side_effect=read):
            self.assertEqual(pdf_runtime.memory_snapshot(), {"rss_mb": 250, "container_mb": 300, "limit_mb": 512})
        with patch.object(pdf_runtime, "rss_megabytes", return_value=None), patch("builtins.open", side_effect=FileNotFoundError):
            self.assertEqual(pdf_runtime.memory_snapshot(), {"rss_mb": None, "container_mb": None, "limit_mb": None})


if __name__ == "__main__":
    unittest.main()
