"""Safe, presentation-only thumbnail rendering for attachment archives."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from src.attachments.models import AttachmentError


def render_archive_thumbnail(path: Path, *, max_pixels: int) -> bytes:
    """Render the first frame as metadata-free static WebP without enlarging it."""

    try:
        with Image.open(path) as source:
            source.seek(0)
            if source.width * source.height > max_pixels:
                raise AttachmentError("attachment_limit_exceeded")
            frame = ImageOps.exif_transpose(source)
            frame.thumbnail((512, 512), Image.Resampling.LANCZOS)
            rendered = frame.convert("RGBA" if "A" in frame.getbands() else "RGB")
            output = BytesIO()
            rendered.save(output, format="WEBP", save_all=False, method=4)
            return output.getvalue()
    except AttachmentError:
        raise
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise AttachmentError("archive_thumbnail_unavailable") from exc
