import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable

from PIL import ImageTk

from playlists import Playlist, Song
from youtube import Candidate

ROW_HEIGHT = 196  # cover preview, its Override Album Art button, and padding; every song row is this tall
SCROLL_STEP = 3 * ROW_HEIGHT // 2


class SongRow:
    """All the state for one song. Widgets exist only while the row is on screen, so the state lives here."""

    def __init__(self, song: Song):
        self.song = song
        self.selected = tk.BooleanVar(value=True)
        self.url = tk.StringVar(value="No video selected")
        self.match = tk.StringVar(value="Searching…")
        self.result = tk.StringVar()
        self.album_art_text = tk.StringVar(value="loading…")
        self.title_text = tk.StringVar(value=song.title)
        self.album_art_label: ttk.Label | None = None
        self.title_label: ttk.Label | None = None
        self.retry_button: ttk.Button | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.candidate: Candidate | None = None
        self.auto_candidate: Candidate | None = None
        self.resolved = False
        self.manual = False
        self.failed = False
        self.cover_override: bytes | None = None  # the user's own cover, as a square JPEG
        self.sample_path: Path | None = None  # the cached preview, once fetched
        self.sample_loading = False
        self.album_art_box: ttk.Frame | None = None
        self.play_button: ttk.Button | None = None
        self.spinner = None
        self.override_button: ttk.Button | None = None
        self.open_button: ttk.Button | None = None

    @property
    def needs_attention(self) -> bool:
        return self.failed or (self.resolved and self.candidate is None)


def row_priority(row: SongRow) -> int:
    """Failed or unmatched songs first, then songs with unconfirmed details, then everything else."""
    if row.needs_attention:
        return 0
    if row.song.unconfirmed:
        return 1
    return 2


class PlaylistTab:
    """One notebook page. Only the rows in view are built, placed at their scroll offset, so long playlists stay cheap."""

    def __init__(
        self,
        parent: ttk.Notebook,
        playlist: Playlist,
        build_row: Callable[[ttk.Frame, SongRow], None],
        set_rows: Callable[[list[SongRow], bool], None],
    ):
        self.name = playlist.name
        self.done = False
        self.build_row = build_row
        self.rows = sorted((SongRow(song) for song in playlist.songs), key=row_priority)
        self.frames: dict[int, ttk.Frame] = {}
        self.top = 0

        frame = ttk.Frame(parent)
        parent.add(frame, text=f"{playlist.name} ({len(self.rows)})")
        self.page = frame

        header = ttk.Frame(frame, padding=(8, 6))
        header.pack(fill="x")
        ttk.Button(header, text="Select all", command=lambda: set_rows(self.rows, True)).pack(side="left")
        ttk.Button(header, text="Deselect all", command=lambda: set_rows(self.rows, False)).pack(side="left", padx=(4, 0))

        holder = ttk.Frame(frame)
        holder.pack(fill="both", expand=True)
        self.scrollbar = ttk.Scrollbar(holder, orient="vertical", command=self._on_scrollbar)
        self.scrollbar.pack(side="right", fill="y")
        self.view = ttk.Frame(holder)
        self.view.pack(side="left", fill="both", expand=True)
        self.view.bind("<Configure>", lambda _event: self.refresh())

        self.refresh()

    @property
    def total_height(self) -> int:
        return len(self.rows) * ROW_HEIGHT

    def add_songs(self, songs: list[Song]) -> list[SongRow]:
        """Append songs not already in this playlist (same title and artist). Returns the new rows."""
        known = {(row.song.title.casefold(), row.song.artist.casefold()) for row in self.rows}
        new_rows = []
        for song in songs:
            if (song.title.casefold(), song.artist.casefold()) not in known:
                new_rows.append(SongRow(song))
        if new_rows:
            self.rows.extend(new_rows)
            self.reorder()
            self.refresh()
        return new_rows

    def scroll_to(self, top: float):
        view_height = self.view.winfo_height() or 1
        self.top = int(max(0, min(top, self.total_height - view_height)))
        self.refresh()

    def wheel(self, notches: int):
        self.scroll_to(self.top + notches * SCROLL_STEP)

    def reorder(self):
        """Move rows that need attention to the top, keeping spreadsheet order within each group."""
        ordered = sorted(self.rows, key=row_priority)
        if all(a is b for a, b in zip(ordered, self.rows)):
            return
        self.clear()  # must run before self.rows changes, so each frame index still maps to its row
        self.rows = ordered
        self.refresh()

    def redraw(self):
        self.clear()
        self.refresh()

    def clear(self):
        for index in list(self.frames):
            self._destroy_row(index)

    def _on_scrollbar(self, action, value, unit=None):
        if action == "moveto":
            self.scroll_to(float(value) * self.total_height)
        elif action == "scroll":
            step = self.view.winfo_height() if unit == "pages" else SCROLL_STEP
            self.scroll_to(self.top + int(value) * step)

    def refresh(self):
        view_height = self.view.winfo_height() or 1
        if self.total_height:
            first = self.top // ROW_HEIGHT
            last = min(len(self.rows) - 1, (self.top + view_height) // ROW_HEIGHT)
            self.scrollbar.set(self.top / self.total_height, min(1.0, (self.top + view_height) / self.total_height))
        else:
            first, last = 0, -1
            self.scrollbar.set(0, 1)

        for index in list(self.frames):
            if not first <= index <= last:
                self._destroy_row(index)
        for index in range(first, last + 1):
            if index not in self.frames:
                self._create_row(index)
            self.frames[index].place(x=0, y=index * ROW_HEIGHT - self.top, relwidth=1, height=ROW_HEIGHT)

    def _create_row(self, index: int):
        frame = ttk.Frame(self.view, padding=(8, 0))
        self.frames[index] = frame
        self.build_row(frame, self.rows[index])

    def _destroy_row(self, index: int):
        row = self.rows[index]
        row.album_art_label = None
        row.title_label = None
        row.retry_button = None
        row.album_art_box = None
        row.play_button = None
        row.spinner = None
        row.override_button = None
        row.open_button = None
        self.frames.pop(index).destroy()
