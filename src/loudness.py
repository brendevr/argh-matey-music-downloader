import json
import math
import os
import re
from pathlib import Path

from mutagen.oggopus import OggOpus

import ffmpeg_tools

DEFAULT_TARGET = -16.0  # integrated loudness in LUFS, a common streaming level
TRUE_PEAK = -1.5  # dBTP ceiling, leaves headroom for lossy encoding
LOUDNESS_RANGE = 11.0  # LU
TOLERANCE = 0.5  # files this close to the target are left as they are


def list_tracks(folder: str | Path) -> list[Path]:
    return sorted(Path(folder).rglob("*.opus"))


def measure(path: Path, target: float) -> dict:
    """First pass: measure the file's loudness with EBU R128 (ffmpeg's loudnorm filter)."""
    result = ffmpeg_tools.run([
        ffmpeg_tools.find("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), "-vn",
        "-af", f"loudnorm=I={target}:TP={TRUE_PEAK}:LRA={LOUDNESS_RANGE}:print_format=json",
        "-f", "null", "-",
    ])
    blocks = re.findall(r"\{[^{}]*\}", result.stderr)  # loudnorm prints its JSON after the other messages
    if not blocks:
        raise RuntimeError("ffmpeg did not report a loudness measurement")
    return json.loads(blocks[-1])


def _copy_tags(source: Path, destination: Path) -> None:
    """Carry the original tags and cover art over to the re-encoded file."""
    original = OggOpus(source)
    new = OggOpus(destination)
    for key in original.keys():
        new[key] = original[key]
    new.save()


def normalize_file(path: Path, target: float = DEFAULT_TARGET) -> str:
    """Bring one Opus file to the target loudness, replacing it in place.

    Returns "normalized", or "unchanged" when the file is already within TOLERANCE of the target or is silent.
    Raises RuntimeError if ffmpeg fails; the original file is left untouched then.
    """
    stats = measure(path, target)
    measured = float(stats["input_i"])
    if not math.isfinite(measured) or abs(measured - target) <= TOLERANCE:
        return "unchanged"

    # The second pass applies the measured values, which is more accurate than a single pass.
    gain_filter = (
        f"loudnorm=I={target}:TP={TRUE_PEAK}:LRA={LOUDNESS_RANGE}:linear=true"
        f":measured_I={stats['input_i']}:measured_TP={stats['input_tp']}"
        f":measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}"
        f":offset={stats['target_offset']}"
    )
    temporary = path.with_name(path.name + ".tmp")
    ffmpeg_tools.encode_opus(path, temporary, gain_filter)
    try:
        _copy_tags(path, temporary)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, path)
    return "normalized"
