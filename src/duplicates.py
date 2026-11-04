import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from mutagen.oggopus import OggOpus

SIMILARITY = 0.9
NOISE_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
NOISE_WORDS = {"official", "music", "video", "audio", "lyrics", "lyric", "hd", "hq", "remastered", "explicit", "clean"}


@dataclass
class TrackFile:
    path: Path
    title: str
    artist: str
    youtube_id: str
    size: int


@dataclass
class DuplicateGroup:
    keep: TrackFile
    remove: list[TrackFile] = field(default_factory=list)


def _first(audio, key: str) -> str:
    values = audio.get(key)
    return values[0] if values else ""


def read_track(path: Path) -> TrackFile:
    """Read the tags the downloader wrote. Files without tags fall back to their file name."""
    try:
        audio = OggOpus(path)
        title, artist, youtube_id = _first(audio, "title"), _first(audio, "artist"), _first(audio, "youtube_id")
    except Exception:
        title, artist, youtube_id = "", "", ""
    return TrackFile(path, title or path.stem, artist, youtube_id, path.stat().st_size)


def scan_folder(folder: str | Path) -> list[TrackFile]:
    return [read_track(path) for path in sorted(Path(folder).rglob("*.opus"))]


def normalize(text: str) -> str:
    """Lower-case, drop bracketed notes and filler words such as 'Official Video', keep only letters and digits."""
    text = NOISE_BRACKETS.sub(" ", text.casefold())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(word for word in text.split() if word not in NOISE_WORDS)


def _key(track: TrackFile) -> str:
    return f"{normalize(track.title)} {normalize(track.artist)}".strip()


def find_duplicates(tracks: list[TrackFile]) -> list[DuplicateGroup]:
    """Group tracks that are the same song. Same YouTube id is a match; otherwise titles and artists must be similar.

    In each group the largest file is kept, since it is usually the best quality.
    """
    parent = list(range(len(tracks)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    by_id: dict[str, int] = {}
    buckets: dict[str, list[int]] = {}
    for index, track in enumerate(tracks):
        if track.youtube_id:
            if track.youtube_id in by_id:
                union(index, by_id[track.youtube_id])
            else:
                by_id[track.youtube_id] = index
        buckets.setdefault(normalize(track.title)[:3], []).append(index)

    for members in buckets.values():
        for position, a in enumerate(members):
            key_a = _key(tracks[a])
            for b in members[position + 1:]:
                if find(a) != find(b) and difflib.SequenceMatcher(None, key_a, _key(tracks[b])).ratio() >= SIMILARITY:
                    union(a, b)

    clusters: dict[int, list[TrackFile]] = {}
    for index, track in enumerate(tracks):
        clusters.setdefault(find(index), []).append(track)

    groups = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda track: (-track.size, str(track.path)))
        groups.append(DuplicateGroup(keep=ordered[0], remove=ordered[1:]))
    return sorted(groups, key=lambda group: str(group.keep.path))
