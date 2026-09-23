"""Procesamiento acotado de fotos para evidencias, miniaturas y PDF."""

from contextlib import ExitStack
import io

from PIL import Image, ImageOps

from pdf_runtime import log_memory_event, pdf_render_slot, release_pdf_memory


MAX_SOURCE_PIXELS = 80_000_000
MAX_DECODE_PIXELS = 25_000_000
MAX_PHOTO_BYTES = 20 * 1024 * 1024


def prepare_photo(content, *, target_size=(1400, 1400), thumbnail=False, wait_timeout=5):
    """Reduce antes de copiar/rotar; libera todos los buffers aun si falla."""
    if not content or len(content) > MAX_PHOTO_BYTES:
        raise ValueError("La foto está vacía o supera 20 MB.")
    with pdf_render_slot(wait_timeout=wait_timeout):
        log_memory_event("thumbnail" if thumbnail else "photo", "start")
        try:
            with ExitStack() as stack:
                source = stack.enter_context(Image.open(io.BytesIO(content)))
                if source.width <= 0 or source.height <= 0 or source.width * source.height > MAX_SOURCE_PIXELS:
                    raise ValueError("La foto supera el límite de 80 megapíxeles.")
                if (source.format or "").upper() in {"JPEG", "JPG", "MPO"}:
                    source.draft("RGB", target_size)
                if source.width * source.height > MAX_DECODE_PIXELS:
                    raise ValueError("La foto requiere demasiada memoria; reduce su resolución antes de subirla.")
                # exif_transpose carga y copia la imagen. Reducir primero evita
                # conservar dos buffers de la foto completa sólo para girarla.
                source.thumbnail(target_size, Image.Resampling.LANCZOS)
                image = ImageOps.exif_transpose(source)
                stack.callback(image.close)
                if thumbnail:
                    if image.mode not in {"RGB", "RGBA"}:
                        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                        stack.callback(image.close)
                    fmt, mime, options = "WEBP", "image/webp", {"quality": 78, "method": 4}
                else:
                    if image.mode != "RGB":
                        if "A" in image.getbands() or "transparency" in image.info:
                            rgba = image.convert("RGBA")
                            stack.callback(rgba.close)
                            background = Image.new("RGB", image.size, "white")
                            stack.callback(background.close)
                            alpha = rgba.getchannel("A")
                            stack.callback(alpha.close)
                            background.paste(rgba, mask=alpha)
                            image = background
                        else:
                            image = image.convert("RGB")
                            stack.callback(image.close)
                    fmt, mime, options = "JPEG", "image/jpeg", {"quality": 85, "optimize": True}
                with io.BytesIO() as output:
                    image.save(output, format=fmt, **options)
                    return output.getvalue(), mime, image.width, image.height
        finally:
            release_pdf_memory()
            log_memory_event("thumbnail" if thumbnail else "photo", "end")
