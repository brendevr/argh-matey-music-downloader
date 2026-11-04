import glob
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import yt_dlp
from mutagen.oggopus import OggOpus

import covers
from config import APP_NAME, AUDIO_BITRATE, OUTPUT_DIR
from playlists import Song
from youtube import Candidate, retry_transient, ydl_options

DESCRIPTION_LIMIT = 4000


class DownloadCancelled(Exception):
    """Raised from yt-dlp's progress hook when the user presses Cancel mid-download."""


class _Logger:
    """Forwards yt-dlp's messages to the app console. Debug chatter is dropped."""

    def __init__(self, on_log):
        self.on_log = on_log

    def debug(self, msg):
        pass

    def info(self, msg):
        self._emit(msg)

    def warning(self, msg, *, once=False):
        self._emit(f"WARNING: {msg}")

    def error(self, msg):
        self._emit(f"ERROR: {msg}")

    def _emit(self, msg):
        if self.on_log is not None:
            self.on_log(msg)


def _report(on_progress, **info):
    if on_progress is not None:
        on_progress(info)


def _progress_hook(should_cancel, on_progress):
    last_report = 0.0

    def hook(progress):
        nonlocal last_report
        if should_cancel is not None and should_cancel():
            raise DownloadCancelled()
        status = progress.get("status")
        if status == "downloading":
            now = time.monotonic()
            if now - last_report < 0.2:
                return
            last_report = now
            total = progress.get("total_bytes") or progress.get("total_bytes_estimate")
            downloaded = progress.get("downloaded_bytes") or 0
            _report(
                on_progress,
                stage="downloading",
                percent=downloaded / total * 100 if total else None,
                speed=progress.get("speed"),
                eta=progress.get("eta"),
                downloaded=downloaded,
                total=total,
            )
        elif status == "finished":
            _report(on_progress, stage="converting", percent=100)

    return hook


def _postprocessor_hook(on_progress):
    def hook(progress):
        if progress.get("status") == "started":
            _report(on_progress, stage="converting")

    return hook


def _safe_dir_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(".") or "_"


def _download_options(playlist_dir: Path, should_cancel, on_progress, on_log) -> dict:
    # yt-dlp fills in %(title)s; a literal '%' in the folder name must be escaped for its template.
    folder = str(playlist_dir).replace("%", "%%")
    return ydl_options(
        quiet=False,
        logger=_Logger(on_log),
        outtmpl=f"{folder}/%(title)s.%(ext)s",
        format="bestaudio/best",
        postprocessors=[{"key": "FFmpegExtractAudio", "preferredcodec": "opus", "preferredquality": AUDIO_BITRATE}],
        progress_hooks=[_progress_hook(should_cancel, on_progress)],
        postprocessor_hooks=[_postprocessor_hook(on_progress)],
    )


def _format_date(upload_date: str | None) -> str:
    if upload_date and len(upload_date) == 8:
        return f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
    return ""


def _format_duration(seconds) -> str:
    if not seconds:
        return ""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def tag_file(path: Path, song: Song, info: dict, cover_jpeg: bytes | None) -> None:
    """Write the song's metadata and cover into an Opus file. Empty values are left out."""
    values = {
        "title": info.get("title") or song.title,
        "artist": song.artist or info.get("channel") or "",
        "album": song.playlist,
        "albumartist": song.artist,
        "tracknumber": str(song.position + 1),
        "date": _format_date(info.get("upload_date")),
        "comment": info.get("webpage_url"),
        "description": (info.get("description") or "")[:DESCRIPTION_LIMIT],
        "encoder": f"{APP_NAME} ({AUDIO_BITRATE} kbps Opus)",
        "youtube_id": info.get("id"),
        "source_url": info.get("webpage_url"),
        "channel": info.get("channel") or info.get("uploader"),
        "channel_url": info.get("channel_url"),
        "duration": _format_duration(info.get("duration")),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "keywords": ", ".join(info.get("tags") or []),
        "categories": ", ".join(info.get("categories") or []),
        "playlist": song.playlist,
        "spreadsheet_title": song.title,
        "spreadsheet_artist": song.artist,
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    audio = OggOpus(path)
    for key, value in values.items():
        if key in audio:
            del audio[key]
        text = "" if value is None else str(value)
        if text:
            audio[key] = [text]

    if cover_jpeg is not None:
        audio["metadata_block_picture"] = [covers.front_cover_block(cover_jpeg)]
    audio.save()


def _remove_partial_files(target: Path) -> None:
    for partial in glob.glob(glob.escape(str(target.with_suffix(""))) + ".*"):
        if ".part" in Path(partial).name:
            Path(partial).unlink(missing_ok=True)


def download_song(
    song: Song, candidate: Candidate, cover_jpeg: bytes | None = None, should_cancel=None, on_progress=None, on_log=None
) -> tuple[Path, bool]:
    """Download and tag one song. Returns (path, skipped), where skipped means the file already existed."""
    return retry_transient(
        lambda: _download_once(song, candidate, cover_jpeg, should_cancel, on_progress, on_log)
    )


def _download_once(song, candidate, cover_jpeg, should_cancel, on_progress, on_log) -> tuple[Path, bool]:
    """Download the candidate as Opus into OUTPUT/{playlist}/{video title}.opus and tag it.

    cover_jpeg is the user's own cover; without it the video's thumbnail becomes the cover.
    """
    playlist_dir = OUTPUT_DIR / _safe_dir_name(song.playlist)
    playlist_dir.mkdir(parents=True, exist_ok=True)
    options = _download_options(playlist_dir, should_cancel, on_progress, on_log)

    with yt_dlp.YoutubeDL(options) as probe:
        info = probe.extract_info(candidate.url, download=False)
        target = Path(probe.prepare_filename(info)).with_suffix(".opus")

    if target.exists():
        return target, True

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.process_ie_result(info, download=True)
    except DownloadCancelled:
        _remove_partial_files(target)
        raise

    _report(on_progress, stage="tagging", percent=100)
    if cover_jpeg is None and info.get("thumbnail"):
        cover_jpeg = covers.make_cover_jpeg(covers.fetch_image(info["thumbnail"]), trim_bars=True)
    tag_file(target, song, info, cover_jpeg)
    return target, False
