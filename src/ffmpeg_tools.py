import shutil
import subprocess
from pathlib import Path

from config import AUDIO_BITRATE


def find(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        raise RuntimeError(f"{tool} was not found on PATH")
    return path


def run(command: list[str]) -> subprocess.CompletedProcess:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        raise RuntimeError(lines[-1] if lines else "ffmpeg failed")
    return result


def encode_opus(source: Path, destination: Path, audio_filter: str | None = None) -> None:
    """Re-encode the first audio stream of source as Opus at AUDIO_BITRATE. A failed run leaves no destination file."""
    command = [
        find("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-map", "0:a:0", "-map_metadata", "0", "-vn",
    ]
    if audio_filter is not None:
        command += ["-af", audio_filter]
    command += ["-c:a", "libopus", "-b:a", f"{AUDIO_BITRATE}k", "-f", "ogg", str(destination)]
    try:
        run(command)
    except RuntimeError:
        destination.unlink(missing_ok=True)
        raise
