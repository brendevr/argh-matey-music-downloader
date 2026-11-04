import re
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook

UNCONFIRMED = re.compile(r"\[[^\]]*unconfirmed[^\]]*\]", re.IGNORECASE)
YOUTUBE_URL = re.compile(r"https?://(?:www\.|m\.)?(?:youtube\.com/watch\?\S*v=|youtu\.be/)([\w-]{11})\S*")


@dataclass
class Song:
    playlist: str
    title: str
    artist: str
    # YouTube video ids found in the spreadsheet comment for this cell, in order.
    lookup_ids: list[str] = field(default_factory=list)
    position: int = 0  # zero-based place in its playlist, used as the track number

    @property
    def unconfirmed(self) -> bool:
        """True when the spreadsheet marks some detail as unconfirmed, e.g. '[dance number unconfirmed]'."""
        return UNCONFIRMED.search(f"{self.title} {self.artist}") is not None


@dataclass
class Playlist:
    name: str
    songs: list[Song] = field(default_factory=list)


def load_playlists(path: str | Path) -> list[Playlist]:
    """Read the spreadsheet (Playlist | Song | Artist columns) into playlists, keeping sheet order."""
    workbook = load_workbook(path)
    sheet = workbook.worksheets[0]

    playlists: dict[str, Playlist] = {}
    for row in sheet.iter_rows(min_row=2):
        playlist_cell, song_cell, artist_cell = row[0], row[1], row[2]
        playlist_name = str(playlist_cell.value or "").strip()
        title = str(song_cell.value or "").strip()
        artist = str(artist_cell.value or "").strip()
        if not playlist_name or not title:
            continue

        comment = song_cell.comment.text if song_cell.comment else ""
        lookup_ids = [m.group(1) for m in YOUTUBE_URL.finditer(comment)]

        playlist = playlists.setdefault(playlist_name, Playlist(playlist_name))
        playlist.songs.append(Song(playlist_name, title, artist, lookup_ids, len(playlist.songs)))

    return list(playlists.values())
