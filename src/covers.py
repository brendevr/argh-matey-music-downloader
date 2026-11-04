import base64
import io
import urllib.request
from pathlib import Path

from mutagen.flac import Picture
from PIL import Image, ImageOps

from config import COVER_SIZE

PREVIEW_SIZE = (150, 150)
LETTERBOX_THRESHOLD = 24  # pixels at or below this brightness count as the black bars


def _as_rgb(image: Image.Image) -> Image.Image:
    """Flatten any picture, including transparent ones, to RGB. Transparent areas become white."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def trim_letterbox(image: Image.Image) -> Image.Image:
    """Remove the black bars YouTube adds to fit 16:9 video into its 4:3 thumbnail."""
    bright = image.convert("L").point(lambda value: 255 if value > LETTERBOX_THRESHOLD else 0)
    box = bright.getbbox()
    return image.crop(box) if box else image


def make_cover_jpeg(image: Image.Image, trim_bars: bool = False) -> bytes:
    """Crop the centre square, scale it to COVER_SIZE and return JPEG bytes.

    Pass trim_bars for video thumbnails; leave it off for the user's own pictures.
    """
    image = _as_rgb(image)
    if trim_bars:
        image = trim_letterbox(image)
    width, height = image.size
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    square = image.crop((left, top, left + side, top + side)).resize((COVER_SIZE, COVER_SIZE), Image.LANCZOS)

    buffer = io.BytesIO()
    square.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def cover_jpeg_from_file(path: str | Path) -> bytes:
    with Image.open(path) as image:
        return make_cover_jpeg(ImageOps.exif_transpose(image))


def preview_from_cover(cover_jpeg: bytes) -> Image.Image:
    """Scale the final cover down to preview size, so the preview shows exactly what gets embedded."""
    with Image.open(io.BytesIO(cover_jpeg)) as cover:
        return cover.resize(PREVIEW_SIZE, Image.LANCZOS)


def fetch_image(url: str) -> Image.Image:
    with urllib.request.urlopen(url, timeout=20) as response:
        return Image.open(io.BytesIO(response.read())).convert("RGB")


def front_cover_block(cover_jpeg: bytes) -> str:
    """The base64 FLAC picture block that an Opus file stores as its cover art."""
    picture = Picture()
    picture.type = 3
    picture.mime = "image/jpeg"
    picture.desc = "Cover"
    picture.width = COVER_SIZE
    picture.height = COVER_SIZE
    picture.depth = 24
    picture.data = cover_jpeg
    return base64.b64encode(picture.write()).decode("ascii")
