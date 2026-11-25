from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

FORMATS = ("JPEG", "PNG", "WEBP")
MAX_NORMALIZED_BYTES = 750 * 1024


class PhotoImageError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class NormalizedPhoto:
    data: bytes
    width: int
    height: int
    sha256: str
    original_sha256: str


def normalize_photo(
    data: bytes, *, max_bytes: int, max_dimension: int, max_pixels: int, output_edge: int
) -> NormalizedPhoto:
    if len(data) > max_bytes:
        raise PhotoImageError("photo_too_large")
    try:
        with Image.open(BytesIO(data), formats=FORMATS) as probe:
            width, height = probe.size
            if max(width, height) > max_dimension or width * height > max_pixels:
                raise PhotoImageError("photo_dimensions_exceeded")
            if getattr(probe, "n_frames", 1) != 1:
                raise PhotoImageError("invalid_photo")
            probe.verify()

        with Image.open(BytesIO(data), formats=FORMATS) as decoded:
            decoded.load()
            oriented = ImageOps.exif_transpose(decoded)
            oriented.thumbnail((output_edge, output_edge), Image.Resampling.LANCZOS)
            rgba = oriented.convert("RGBA")
            clean = Image.new("RGB", rgba.size, "white")
            clean.paste(rgba, mask=rgba.getchannel("A"))

        for quality in (82, 70, 55):
            buffer = BytesIO()
            clean.save(buffer, "JPEG", quality=quality, optimize=True)
            normalized = buffer.getvalue()
            if len(normalized) <= MAX_NORMALIZED_BYTES:
                return NormalizedPhoto(
                    normalized, clean.width, clean.height, sha256(normalized).hexdigest(),
                    sha256(data).hexdigest(),
                )
        raise PhotoImageError("photo_too_large")
    except Image.DecompressionBombError:
        raise PhotoImageError("photo_dimensions_exceeded") from None
    except PhotoImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, OverflowError):
        raise PhotoImageError("invalid_photo") from None
