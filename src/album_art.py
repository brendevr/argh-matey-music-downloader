import os
from pathlib import Path

from mutagen.oggopus import OggOpus

import ffmpeg_tools
from covers import cover_jpeg_from_file, front_cover_block

AUDIO_TYPES = "*.mp3 *.m4a *.aac *.flac *.wav *.ogg *.oga *.opus *.wma *.aif *.aiff *.wv *.mp4 *.webm"


def embed_album_art(source: Path, image: Path, destination: Path) -> None:
    """Convert any audio file to Opus at destination with the image as its album art, cropped to a square.

    The source's tags are kept where ffmpeg can map them.
    """
    temporary = destination.with_name(destination.name + ".tmp")
    ffmpeg_tools.encode_opus(source, temporary)
    try:
        audio = OggOpus(temporary)
        audio["metadata_block_picture"] = [front_cover_block(cover_jpeg_from_file(image))]
        audio.save()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, destination)
