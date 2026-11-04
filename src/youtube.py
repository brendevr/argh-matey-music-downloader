import json
import re
import threading
import time
import urllib.error
from dataclasses import dataclass
from pathlib import Path

import yt_dlp
from PIL import Image

from config import CACHE_FILE, SAMPLE_DIR, SAMPLE_SECONDS
from covers import fetch_image
from playlists import Song

SEARCH_RESULTS = 5
RETRY_DELAYS = (3, 8)  # seconds to wait after each transient failure; the attempt after the last wait is final
TRANSIENT_ERRORS = ("Requested format is not available", "HTTP Error 403", "HTTP Error 429")

# Words that usually mean "not the studio recording" when they are absent from the song title.
UNWANTED_WORDS = ("live", "remix", "cover", "karaoke", "instrumental", "reaction", "nightcore", "slowed", "sped up")
PREFERRED_WORDS = ("official", "audio", "topic")

_cache_lock = threading.Lock()


@dataclass
class Candidate:
    video_id: str
    title: str
    channel: str
    duration: int | None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def video_id_from_url(text: str) -> str | None:
    match = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", text)
    return match.group(1) if match else None


def ydl_options(**extra) -> dict:
    options = {"quiet": True, "no_warnings": True, "noprogress": True, "js_runtimes": {"node": {}}}
    options.update(extra)
    return options


def retry_transient(call):
    """Run call, retrying YouTube's transient failures (HTTP 403, 429, missing formats) after each delay."""
    for delay in RETRY_DELAYS:
        try:
            return call()
        except yt_dlp.utils.DownloadError as exc:
            if not any(marker in str(exc) for marker in TRANSIENT_ERRORS):
                raise
            time.sleep(delay)
    return call()


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _strip_notes(text: str) -> str:
    """Remove bracketed annotations such as '[movement unconfirmed]' that the spreadsheet uses but YouTube does not."""
    return re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]", "", text)).strip()


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache_entry(query: str, results: list[dict]) -> None:
    with _cache_lock:
        cache = _load_cache()
        cache[query] = results
        CACHE_FILE.parent.mkdir(exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def _extract_search(key: str) -> dict:
    with yt_dlp.YoutubeDL(ydl_options(skip_download=True, extract_flat="in_playlist")) as ydl:
        return ydl.extract_info(key, download=False)


def search_candidates(song: Song) -> list[Candidate]:
    """Find candidate videos for a song with yt-dlp's ytsearch pseudo-URL. Results are cached on disk."""
    query = _strip_notes(f"{song.title} {song.artist}")
    key = f"ytsearch{SEARCH_RESULTS}:{query}"

    raw = _load_cache().get(key)
    if raw is None:
        info = retry_transient(lambda: _extract_search(key))
        raw = [
            {"id": e.get("id"), "title": e.get("title") or "", "channel": e.get("channel") or "", "duration": e.get("duration")}
            for e in info.get("entries") or []
            if e and e.get("id")
        ]
        if raw:  # an empty answer may be a transient failure, so only keep real results
            _save_cache_entry(key, raw)

    return [Candidate(r["id"], r["title"], r["channel"], r["duration"]) for r in raw]


def best_candidate(song: Song, candidates: list[Candidate]) -> Candidate | None:
    """Pick the search result that best matches the spreadsheet title and artist."""
    if not candidates:
        return None

    title = _normalize(_strip_notes(song.title))
    artist_names = [_normalize(a) for a in song.artist.split(",") if a.strip()]

    def score(candidate: Candidate) -> float:
        text = _normalize(f"{candidate.title} {candidate.channel}")
        result = 0.0
        if title and title in _normalize(candidate.title):
            result += 3
        if artist_names and any(name and name in text for name in artist_names):
            result += 2
        for word in UNWANTED_WORDS:
            if word in text and word not in title:
                result -= 2
        for word in PREFERRED_WORDS:
            if word in text:
                result += 0.5
        return result

    # max() keeps the first of equal scores, so yt-dlp's own ranking breaks ties.
    return max(candidates, key=score)


def fetch_video_image(video_id: str) -> Image.Image:
    """The largest thumbnail YouTube has for this video: 1280x720 when it exists, otherwise 480x360."""
    try:
        return fetch_image(f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg")
    except urllib.error.HTTPError:
        return fetch_image(f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")


def fetch_sample(video_id: str) -> Path:
    """Download the first SAMPLE_SECONDS of a video's audio for previewing. Cached per video."""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    cached = [path for path in SAMPLE_DIR.glob(f"{video_id}.*") if ".part" not in path.name]
    if cached:
        return cached[0]

    def attempt() -> Path:
        options = ydl_options(
            format="bestaudio/best",
            outtmpl=f"{str(SAMPLE_DIR).replace('%', '%%')}/{video_id}.%(ext)s",
            download_ranges=yt_dlp.utils.download_range_func(None, [(0, SAMPLE_SECONDS)]),
            force_keyframes_at_cuts=True,
        )
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            return Path(ydl.prepare_filename(info))

    return retry_transient(attempt)
