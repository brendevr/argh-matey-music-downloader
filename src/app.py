import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

import album_art
import covers
import duplicates
import loudness
import theme
import youtube
import youtube_import
from config import APP_NAME, AUDIO_BITRATE, EXAMPLE_XLSX, SAMPLE_SECONDS
from downloader import DownloadCancelled, download_song
from duplicates_dialog import DuplicatesDialog
from formatting import format_size, format_speed, format_time
from playlist_tab import PlaylistTab, SongRow
from playlists import Playlist, load_playlists
from widgets import DotSpinner, media_icon
from youtube import Candidate, video_id_from_url

WORKERS = 6
CONSOLE_LINES = 1000
IMAGE_TYPES = "*.jpg *.jpeg *.png *.webp *.bmp *.gif *.tif *.tiff"


class App(tk.Tk):
    def __init__(self, xlsx_path: str | None = None):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1150x800")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        base_font = tkfont.nametofont("TkDefaultFont")
        self.bold_font = base_font.copy()
        self.bold_font.configure(weight="bold")
        self.title_font = base_font.copy()
        self.title_font.configure(size=base_font.cget("size") + 8, weight="bold")
        self.console_font = tkfont.nametofont("TkFixedFont")
        saved_choice = theme.load_choice()
        self.dark = saved_choice == "dark" if saved_choice else theme.os_prefers_dark()
        self.default_fg = "black"

        self.events: queue.Queue = queue.Queue()
        self.pool = ThreadPoolExecutor(max_workers=WORKERS)
        self.rows: list[SongRow] = []
        self.tabs: dict[str, PlaylistTab] = {}
        self.active_tab: PlaylistTab | None = None
        self.album_art_cache: dict[str, Image.Image | None] = {}
        self.downloading = False
        self.cancel_event = threading.Event()
        self.download_summary: str | None = None
        self.batch: dict | None = None
        self.current: dict | None = None
        self.details_visible = False
        self.fetch_log: list[tuple[str, str, str, str]] = []
        self.fetch_frame: ttk.Frame | None = None
        self.fetch_tree: ttk.Treeview | None = None
        self.download_failures: dict[SongRow, tuple[str, str]] = {}  # row -> (time, message)
        self.download_frame: ttk.Frame | None = None
        self.download_tree: ttk.Treeview | None = None
        self.normalizing = False
        self._selection_refresh_pending = False
        self.playing_row: SongRow | None = None
        self.sample_process: subprocess.Popen | None = None

        self._build_menus()

        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        self.queue_label = ttk.Label(top, text="Queue is empty")
        self.queue_label.pack(side="left", fill="x", expand=True)
        self.selected_label = ttk.Label(top, text="Selected: 0 of 0 songs")
        self.selected_label.pack(side="left", padx=(0, 12))
        ttk.Button(top, text="Select all", command=lambda: self._set_rows(self.rows, True)).pack(side="left")
        ttk.Button(top, text="Deselect all", command=lambda: self._set_rows(self.rows, False)).pack(side="left", padx=(4, 0))

        self._build_bottom()
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._build_error_tabs()
        self._build_splash()

        self.bind_all("<MouseWheel>", self._on_wheel)
        self.bind_all("<Button-4>", self._on_wheel)
        self.bind_all("<Button-5>", self._on_wheel)

        self._apply_theme()
        self.after(100, self._drain_events)
        if xlsx_path:
            self._import_xlsx_path(xlsx_path)

    def _build_menus(self):
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Import XLSX…", command=self.import_xlsx)
        file_menu.add_command(label="Import YouTube Playlist URL…", command=self.import_playlist_url)
        file_menu.add_command(label="Import YouTube Video URL…", command=self.import_video_url)
        menubar.add_cascade(label="File", menu=file_menu)

        tools_menu = tk.Menu(menubar, tearoff=False)
        tools_menu.add_command(label="Remove Duplicates…", command=self.remove_duplicates)
        tools_menu.add_command(label="Normalize Loudness…", command=self.normalize_loudness)
        tools_menu.add_command(label="Embed Album Art…", command=self.embed_album_art_tool)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_command(label="Toggle Light/Dark", command=self.toggle_theme)
        menubar.add_cascade(label="View", menu=view_menu)
        self.config(menu=menubar)

    def _apply_theme(self):
        self.palette = theme.apply(self, self.dark)
        self.default_fg = self.palette["fg"]
        self.play_icon = media_icon("play", self.palette["fg"])
        self.stop_icon = media_icon("stop", self.palette["fg"])

    def toggle_theme(self):
        self.dark = not self.dark
        theme.save_choice("dark" if self.dark else "light")
        self._apply_theme()
        for tab in self.tabs.values():
            tab.redraw()  # song titles keep the colour they were built with, so rebuild the visible rows
        self._log_console(f"Switched to {'dark' if self.dark else 'light'} mode")

    def _build_splash(self):
        self.splash = ttk.Frame(self, padding=32, relief="ridge", borderwidth=2)
        ttk.Label(self.splash, text=APP_NAME, font=self.title_font).pack(pady=(0, 18))
        ttk.Button(self.splash, text="Import XLSX playlist", width=32, command=self.import_xlsx).pack(pady=4)
        ttk.Button(self.splash, text="Import YouTube playlist URL", width=32, command=self.import_playlist_url).pack(pady=4)
        ttk.Button(self.splash, text="Import YouTube video URL", width=32, command=self.import_video_url).pack(pady=4)
        ttk.Label(
            self.splash,
            text=f"Spreadsheet format: see “{EXAMPLE_XLSX.name}”",
            foreground="gray",
        ).pack(pady=(18, 0))
        self._update_splash()

    def _update_splash(self):
        if self.rows:
            self.splash.place_forget()
        else:
            self.splash.place(relx=0.5, rely=0.5, anchor="center")
            self.splash.lift()

    def _build_bottom(self):
        wrap = ttk.Frame(self)
        wrap.pack(side="bottom", fill="x")

        self.details = ttk.Frame(wrap, padding=(8, 4))
        self.detail_song = ttk.Label(self.details, text="No download running", font=self.bold_font)
        self.detail_song.pack(anchor="w")
        self.detail_stage = ttk.Label(self.details)
        self.detail_stage.pack(anchor="w")
        self.detail_song_bar = ttk.Progressbar(self.details, maximum=100)
        self.detail_song_bar.pack(fill="x", pady=(2, 6))
        self.detail_overall = ttk.Label(self.details)
        self.detail_overall.pack(anchor="w")
        self.detail_playlists = ttk.Label(self.details, wraplength=1000)
        self.detail_playlists.pack(anchor="w")
        self.detail_counts = ttk.Label(self.details)
        self.detail_counts.pack(anchor="w", pady=(0, 6))
        console_frame = ttk.Frame(self.details)
        console_frame.pack(fill="both", expand=True)
        self.console = tk.Text(console_frame, height=8, wrap="word", state="disabled", font=self.console_font)
        console_scroll = ttk.Scrollbar(console_frame, orient="vertical", command=self.console.yview)
        self.console.configure(yscrollcommand=console_scroll.set)
        console_scroll.pack(side="right", fill="y")
        self.console.pack(side="left", fill="both", expand=True)

        bottom = ttk.Frame(wrap, padding=8)
        bottom.pack(fill="x")
        self.bottom_bar = bottom
        self.status = ttk.Label(bottom, text="")
        self.status.pack(side="left", fill="x", expand=True)
        self.details_button = ttk.Button(bottom, text="Details ▴", command=self._toggle_details)
        self.details_button.pack(side="right")
        self.download_button = ttk.Button(bottom, text="Download selected", command=self.download_all)
        self.download_button.pack(side="right", padx=(0, 4))
        self.cancel_button = ttk.Button(bottom, text="Cancel", command=self.cancel_downloads, state="disabled")
        self.cancel_button.pack(side="right", padx=(0, 4))
        self.progress = ttk.Progressbar(bottom, length=260)
        self.progress.pack(side="right", padx=8)

    def _toggle_details(self):
        self.details_visible = not self.details_visible
        if self.details_visible:
            self.details.pack(fill="x", before=self.bottom_bar)
            self.details_button.config(text="Details ▾")
            self._render_details()
        else:
            self.details.pack_forget()
            self.details_button.config(text="Details ▴")

    def _log_console(self, text: str):
        self.console.config(state="normal")
        self.console.insert("end", f"{datetime.now():%H:%M:%S}  {text}\n")
        line_count = int(self.console.index("end-1c").split(".")[0])
        if line_count > CONSOLE_LINES:
            self.console.delete("1.0", f"{line_count - CONSOLE_LINES}.0")
        self.console.see("end")
        self.console.config(state="disabled")

    def import_xlsx(self):
        path = filedialog.askopenfilename(
            title="Choose a playlist spreadsheet",
            filetypes=[("Excel workbook", "*.xlsx"), ("All files", "*.*")],
            parent=self,
        )
        if path:
            self._import_xlsx_path(path)

    def _import_xlsx_path(self, path: str):
        try:
            playlists = load_playlists(path)
        except Exception as error:
            messagebox.showerror("Could not read spreadsheet", str(error), parent=self)
            return
        self._add_playlists(playlists, Path(path).name)

    def import_playlist_url(self):
        url = simpledialog.askstring(APP_NAME, "Paste a YouTube playlist link:", parent=self)
        if url and url.strip():
            self._fetch_in_background(lambda: youtube_import.fetch_playlist(url.strip()), "playlist")

    def import_video_url(self):
        url = simpledialog.askstring(APP_NAME, "Paste a YouTube video link:", parent=self)
        if url and url.strip():
            self._fetch_in_background(lambda: youtube_import.fetch_video(url.strip()), "video")

    def _fetch_in_background(self, fetch, what: str):
        self.status.config(text=f"Fetching YouTube {what}…")
        # A thread of its own, so the import does not wait behind the searches in the pool.
        threading.Thread(target=self._fetch_job, args=(fetch, what), daemon=True).start()

    def _fetch_job(self, fetch, what: str):
        try:
            self.events.put(("import_ready", [fetch()], what))
        except Exception as exc:
            self.events.put(("import_error", what, str(exc)))

    def _add_playlists(self, playlists: list[Playlist], source: str):
        added = []
        first_new_tab = None
        for playlist in playlists:
            tab = self._tab_named(playlist.name)
            if tab is None:
                tab = PlaylistTab(self.notebook, playlist, self._build_row, self._set_rows)
                self.tabs[str(tab.page)] = tab
                first_new_tab = first_new_tab or tab
                new_rows = tab.rows
            else:
                new_rows = tab.add_songs(playlist.songs)
                self._update_tab_label(tab)
            added.extend(new_rows)
        if first_new_tab is not None:
            self.notebook.select(first_new_tab.page)

        self.rows.extend(added)
        for row in added:
            row.selected.trace_add("write", self._on_selection_changed)
        self._on_selection_changed()
        for frame in (self.fetch_frame, self.download_frame):
            self.notebook.insert("end", frame)  # keep the error tabs after the playlists
        for row in added:
            self._start_matching(row)
        self._update_splash()
        self._update_queue_label()
        self._log_console(f"Added {len(added)} songs from {source}")
        if not added:
            messagebox.showinfo("Nothing added", "Every song in that source is already in the queue.", parent=self)
        else:
            self._update_matching_status()

    def _tab_named(self, name: str) -> PlaylistTab | None:
        return next((tab for tab in self.tabs.values() if tab.name == name), None)

    def _update_tab_label(self, tab: PlaylistTab):
        text = f"{tab.name} ({len(tab.rows)})" + (" ✓" if tab.done else "")
        self.notebook.tab(tab.page, text=text)

    def _update_queue_label(self):
        self.queue_label.config(text=f"Queue: {len(self.tabs)} playlists · {len(self.rows)} songs")

    def _start_matching(self, row: SongRow):
        if row.song.lookup_ids:
            # The spreadsheet comment or YouTube import already names a video, so skip the search.
            candidate = Candidate(row.song.lookup_ids[0], row.song.title, "", None)
            row.auto_candidate = candidate
            row.resolved = True
            self._apply(row, candidate, "From spreadsheet link")
        else:
            self.pool.submit(self._resolve, row)

    def _resolve(self, row: SongRow):
        error = None
        best = None
        try:
            best = youtube.best_candidate(row.song, youtube.search_candidates(row.song))
        except Exception as exc:
            error = str(exc)
        self.events.put(("resolved", row, best, error))

    def _apply(self, row: SongRow, candidate: Candidate | None, match_text: str):
        self._clear_failure(row)
        self._refresh_row_state(row)
        row.candidate = candidate
        self._refresh_override_button(row)
        self._refresh_open_button(row)
        row.match.set(match_text)
        if candidate is None:
            row.url.set("No video selected")
            row.selected.set(False)
            self._set_album_art(row, "no match")
            return
        row.url.set(candidate.url)
        if row.cover_override is not None:
            return  # the user's own cover stays on screen
        if candidate.video_id in self.album_art_cache:
            self._show_album_art(row, candidate.video_id, self.album_art_cache[candidate.video_id])
        else:
            self._set_album_art(row, "loading…")
            self.pool.submit(self._load_album_art, row, candidate.video_id)

    def _load_album_art(self, row: SongRow, video_id: str):
        try:
            cover = covers.make_cover_jpeg(youtube.fetch_video_image(video_id), trim_bars=True)
            image = covers.preview_from_cover(cover)
        except Exception as exc:
            image = None
            self.events.put(("error", row.song.playlist, row.song.title, f"Preview failed: {exc}"))
        self.events.put(("album_art", row, video_id, image))

    def _show_album_art(self, row: SongRow, video_id: str, image: Image.Image | None):
        if row.candidate is None or row.candidate.video_id != video_id or row.cover_override is not None:
            return
        if image is None:
            self._set_album_art(row, "Album Image Fetch Failed.")
        else:
            self._set_album_art(row, "", ImageTk.PhotoImage(image))

    def _set_album_art(self, row: SongRow, text: str, photo: ImageTk.PhotoImage | None = None):
        row.photo = photo
        row.album_art_text.set("" if photo else text)
        if row.album_art_label is not None:
            row.album_art_label.config(image=photo if photo else "")

    def _build_row(self, parent: ttk.Frame, row: SongRow):
        song = row.song
        padding = {"padx": 6, "pady": 6}

        ttk.Checkbutton(parent, variable=row.selected).grid(row=0, column=0, sticky="n", **padding)

        album_art_column = ttk.Frame(parent)
        album_art_column.grid(row=0, column=1, sticky="n", **padding)
        album_art_box = ttk.Frame(album_art_column, width=covers.PREVIEW_SIZE[0], height=covers.PREVIEW_SIZE[1])
        album_art_box.pack()
        album_art_box.pack_propagate(False)
        row.album_art_box = album_art_box
        row.album_art_label = ttk.Label(album_art_box, textvariable=row.album_art_text, anchor="center")
        row.album_art_label.pack(fill="both", expand=True)
        if row.photo is not None:
            row.album_art_label.config(image=row.photo)

        row.play_button = ttk.Button(album_art_box, command=lambda: self._play_sample(row))
        for widget in (album_art_box, row.album_art_label, row.play_button):
            widget.bind("<Enter>", lambda _event, row=row: self._show_play(row))
            widget.bind("<Leave>", lambda event, row=row: self._leave_album_art(row, event))
        self._render_sample_state(row)
        row.override_button = ttk.Button(album_art_column, text="Override Album Art", command=lambda: self._override_album_art(row))
        self._refresh_override_button(row)

        info = ttk.Frame(parent)
        info.grid(row=0, column=2, sticky="nw", **padding)
        row.title_label = ttk.Label(info, textvariable=row.title_text, font=self.bold_font)
        row.title_label.pack(anchor="w")
        ttk.Label(info, text=song.artist, wraplength=420).pack(anchor="w")
        ttk.Label(info, textvariable=row.match, foreground="gray", wraplength=420).pack(anchor="w", pady=(4, 0))
        ttk.Label(info, textvariable=row.result, wraplength=420).pack(anchor="w")

        controls = ttk.Frame(parent)
        controls.grid(row=0, column=3, sticky="nw", **padding)
        ttk.Label(controls, textvariable=row.url, width=46, anchor="w").pack(anchor="w")
        buttons = ttk.Frame(controls)
        buttons.pack(anchor="w", pady=(4, 0))
        ttk.Button(buttons, text="Change URL", command=lambda: self._change_url(row)).pack(side="left")
        row.open_button = ttk.Button(buttons, text="Open in Browser", command=lambda: self._open_in_browser(row))
        row.open_button.pack(side="left", padx=4)
        self._refresh_open_button(row)
        ttk.Button(buttons, text="Reset", command=lambda: self._reset(row)).pack(side="left")
        row.retry_button = ttk.Button(buttons, text="Try Again", command=lambda: self._retry(row))

        self._refresh_row_state(row)
        parent.columnconfigure(2, weight=1)

    def _refresh_open_button(self, row: SongRow):
        if row.open_button is not None:
            row.open_button.config(state="normal" if row.candidate is not None else "disabled")

    def _refresh_override_button(self, row: SongRow):
        """Offer Override Album Art only once a video has been matched for the song."""
        if row.override_button is None:
            return
        if row.candidate is not None:
            row.override_button.pack(pady=(4, 0))
        else:
            row.override_button.pack_forget()

    def _refresh_row_state(self, row: SongRow):
        """Show a failed download in red with a Try Again button; restore the plain look otherwise."""
        if row.failed:
            row.title_text.set(f"{row.song.title} - ERROR DOWNLOADING")
        else:
            row.title_text.set(row.song.title)
        if row.title_label is not None:
            row.title_label.config(foreground="red" if row.failed else self.default_fg)
        if row.retry_button is not None:
            if row.failed:
                row.retry_button.pack(side="left", padx=4)
            else:
                row.retry_button.pack_forget()

    def _change_url(self, row: SongRow):
        dialog = tk.Toplevel(self)
        dialog.title("Change URL")
        dialog.transient(self)
        dialog.resizable(False, False)

        body = ttk.Frame(dialog, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"YouTube link for “{row.song.title}”:").pack(anchor="w")
        url_var = tk.StringVar(value=row.candidate.url if row.candidate else "")
        entry = ttk.Entry(body, textvariable=url_var, width=60)
        entry.pack(fill="x", pady=8)
        entry.focus_set()

        def apply(_event=None):
            if self._use_url(row, url_var.get(), dialog):
                dialog.destroy()

        buttons = ttk.Frame(body)
        buttons.pack(anchor="e")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="left")
        ttk.Button(buttons, text="OK", command=apply).pack(side="left", padx=(8, 0))
        entry.bind("<Return>", apply)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def _use_url(self, row: SongRow, text: str, parent: tk.Misc) -> bool:
        video_id = video_id_from_url(text.strip())
        if video_id is None:
            messagebox.showerror(
                "Invalid URL",
                "That isn't a YouTube video link.\n\nUse a link such as https://www.youtube.com/watch?v=… or https://youtu.be/…",
                parent=parent,
            )
            return False
        row.manual = True
        row.selected.set(True)
        row.result.set("")
        self._apply(row, Candidate(video_id, row.song.title, "", None), "Manual URL")
        self._reorder_attention()
        return True

    def _reset(self, row: SongRow):
        if not row.resolved:
            return
        row.manual = False
        row.cover_override = None
        row.result.set("")
        if row.auto_candidate is None:
            self._apply(row, None, "No automatic match — use Change URL")
        else:
            self._apply(row, row.auto_candidate, "Automatic match")
        self._reorder_attention()

    def _open_in_browser(self, row: SongRow):
        if row.candidate is not None:
            webbrowser.open(row.candidate.url)

    def _set_rows(self, rows: list[SongRow], value: bool):
        for row in rows:
            row.selected.set(value)

    def _show_play(self, row: SongRow):
        if row.play_button is not None and row.candidate is not None and not row.sample_loading:
            row.play_button.place(relx=0.5, rely=0.5, anchor="center")

    def _leave_album_art(self, row: SongRow, event):
        box = row.album_art_box
        if box is None or row.play_button is None:
            return
        left, top = box.winfo_rootx(), box.winfo_rooty()
        inside = left <= event.x_root < left + box.winfo_width() and top <= event.y_root < top + box.winfo_height()
        if not inside and self.playing_row is not row:
            row.play_button.place_forget()

    def _render_sample_state(self, row: SongRow):
        if row.album_art_box is None or row.play_button is None:
            return
        row.play_button.config(image=self.stop_icon if self.playing_row is row else self.play_icon)
        if row.sample_loading:
            if row.spinner is None:
                row.spinner = DotSpinner(row.album_art_box, self.palette["fg"], self.palette["border"], self.palette["bg"])
                row.spinner.place(relx=0.5, rely=0.5, anchor="center")
            row.play_button.place_forget()
            return
        if row.spinner is not None:
            row.spinner.destroy()
            row.spinner = None
        if self.playing_row is row:
            row.play_button.place(relx=0.5, rely=0.5, anchor="center")
        else:
            row.play_button.place_forget()

    def _play_sample(self, row: SongRow):
        if row.candidate is None:
            return
        if self.playing_row is row:
            self._stop_sample()
            return
        self._stop_sample()
        self.playing_row = row
        if row.sample_path is not None and row.sample_path.exists():
            self._start_playback(row)
            return
        row.sample_loading = True
        self._render_sample_state(row)
        threading.Thread(target=self._sample_worker, args=(row, row.candidate.video_id), daemon=True).start()

    def _sample_worker(self, row: SongRow, video_id: str):
        try:
            self.events.put(("sample", row, youtube.fetch_sample(video_id), None))
        except Exception as exc:
            self.events.put(("sample", row, None, str(exc)[:200]))

    def _start_playback(self, row: SongRow):
        ffplay = shutil.which("ffplay")
        if ffplay is None:
            self.playing_row = None
            messagebox.showerror("Cannot play sample", "ffplay (part of FFmpeg) was not found on PATH.", parent=self)
            return
        self.sample_process = subprocess.Popen(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", "-t", str(SAMPLE_SECONDS), str(row.sample_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._render_sample_state(row)

    def _stop_sample(self):
        if self.sample_process is not None and self.sample_process.poll() is None:
            self.sample_process.terminate()
        self.sample_process = None
        if self.playing_row is not None:
            previous = self.playing_row
            self.playing_row = None
            self._render_sample_state(previous)

    def _override_album_art(self, row: SongRow):
        path = filedialog.askopenfilename(
            title=f"Choose a cover image for “{row.song.title}”",
            filetypes=[("Images", IMAGE_TYPES), ("All files", "*.*")],
            parent=self,
        )
        if not path:
            return
        self._set_album_art(row, "converting…")
        threading.Thread(target=self._convert_cover, args=(row, path), daemon=True).start()

    def _convert_cover(self, row: SongRow, path: str):
        try:
            self.events.put(("cover", row, covers.cover_jpeg_from_file(path), None))
        except Exception as exc:
            self.events.put(("cover", row, None, f"{Path(path).name}: {exc}"))

    def _restore_album_art(self, row: SongRow):
        if row.cover_override is not None:
            self._set_album_art(row, "", ImageTk.PhotoImage(covers.preview_from_cover(row.cover_override)))
        elif row.candidate is not None and row.candidate.video_id in self.album_art_cache:
            self._show_album_art(row, row.candidate.video_id, self.album_art_cache[row.candidate.video_id])
        else:
            self._set_album_art(row, "Album Image Fetch Failed.")

    def download_all(self):
        picks = [row for row in self.rows if row.selected.get() and row.candidate is not None]
        if not picks:
            messagebox.showinfo("Nothing to download", "Select at least one song that has a video.", parent=self)
            return
        self._start_downloads(picks)

    def _retry(self, row: SongRow):
        self._start_downloads([row])

    def _retry_all_failed(self):
        rows = [row for row in self.download_failures if row.candidate is not None]
        if not rows:
            messagebox.showinfo("Nothing to retry", "There are no failed downloads to retry.", parent=self)
            return
        self._start_downloads(rows)

    def _start_downloads(self, picks: list[SongRow]):
        if self.downloading:
            messagebox.showinfo("Busy", "A download is already running. Wait for it to finish first.", parent=self)
            return

        self.downloading = True
        self.cancel_event.clear()
        self.download_summary = None
        self.current = None
        self.batch = {
            "started": time.monotonic(),
            "total": len(picks),
            "done": 0,
            "counts": Counter(),
            "finished": [],
        }
        self.download_button.config(state="disabled")
        self.cancel_button.config(state="normal")
        self.progress.config(maximum=len(picks), value=0)
        self._log_console(f"Starting download of {len(picks)} songs")
        threading.Thread(target=self._download_worker, args=(picks,), daemon=True).start()
        self._render_details()

    def cancel_downloads(self):
        self.cancel_event.set()
        self.cancel_button.config(state="disabled")
        self._log_console("Cancel requested; stopping after the current step")

    def _download_worker(self, picks: list[SongRow]):
        totals = Counter(row.song.playlist for row in picks)
        seen: Counter = Counter()
        outcomes: dict[str, Counter] = defaultdict(Counter)
        cancelled = False
        for row in picks:
            if self.cancel_event.is_set():
                cancelled = True
                break
            playlist = row.song.playlist
            self.events.put(("result", row, "Downloading…"))
            try:
                path, already_there = download_song(
                    row.song,
                    row.candidate,
                    row.cover_override,
                    should_cancel=self.cancel_event.is_set,
                    on_progress=lambda info, row=row: self.events.put(("detail", row, info)),
                    on_log=lambda text: self.events.put(("log", text)),
                )
                kind = "skipped" if already_there else "saved"
                outcomes[playlist][kind] += 1
                self.events.put(("outcome", row, kind, "Already downloaded" if already_there else f"Saved: {path}"))
            except DownloadCancelled:
                cancelled = True
                self.events.put(("outcome", row, "cancelled", "Cancelled"))
                break
            except Exception as exc:
                outcomes[playlist]["failed"] += 1
                self.events.put(("outcome", row, "failed", str(exc)[:200]))

            seen[playlist] += 1
            if seen[playlist] == totals[playlist]:
                self.events.put(("playlist_done", playlist, dict(outcomes[playlist])))

        overall: Counter = Counter()
        for counts in outcomes.values():
            overall.update(counts)
        summary = f"{overall['saved']} saved, {overall['skipped']} already present, {overall['failed']} failed"
        self.events.put(("done", f"Cancelled: {summary}" if cancelled else f"Finished: {summary}"))

    def _on_detail(self, row: SongRow, info: dict):
        self.current = {"row": row, **info}

    def _on_outcome(self, row: SongRow, kind: str, text: str):
        self.current = None
        if self.batch is not None and kind != "cancelled":
            self.batch["done"] += 1
            self.batch["counts"][kind] += 1
            self.progress.config(value=self.batch["done"])
        if kind == "failed":
            self._log_console(f"✖ {row.song.title}: {text}")
            self._mark_failed(row, text)
            return
        if kind == "cancelled":
            row.result.set(text)
            self._log_console("Cancelled")
            return
        self._log_console(f"✔ {row.song.title} — {text}")
        self._clear_failure(row)
        row.result.set(text)
        self._refresh_row_state(row)
        self._reorder_attention()

    def _on_playlist_done(self, playlist: str, counts: dict[str, int]):
        if self.batch is not None:
            self.batch["finished"].append(
                f"{playlist} ({counts.get('saved', 0)} saved, {counts.get('failed', 0)} failed)"
            )
        tab = self._tab_named(playlist)
        if tab is not None:
            tab.done = True
            self._update_tab_label(tab)
        self._log_console(f"Playlist finished: {playlist}")

    def _on_done(self, summary: str):
        self.downloading = False
        self.current = None
        self.download_button.config(state="normal")
        self.cancel_button.config(state="disabled")
        self.download_summary = summary
        self._log_console(summary)
        completed = self.batch["finished"] if self.batch else []
        self.status.config(text=summary + (f"   ·   Completed playlists: {', '.join(completed)}" if completed else ""))
        self._render_details()

    def _render_details(self):
        batch = self.batch
        if batch is None:
            return
        done, total = batch["done"], batch["total"]
        counts = batch["counts"]
        elapsed = time.monotonic() - batch["started"]
        per_song = elapsed / done if done else None
        remaining = per_song * (total - done) if per_song else None
        overall_percent = done / total * 100 if total else 100

        current = self.current
        if current is not None and self.downloading:
            row = current["row"]
            title = f"{row.song.title} — {row.song.artist}"
            playlist_name = row.song.playlist
            stage = current.get("stage")
            song_percent = current.get("percent")
            if stage == "downloading":
                percent_text = f"{song_percent:.1f}% · " if song_percent is not None else ""
                stage_text = (
                    f"Downloading · {percent_text}{format_speed(current.get('speed'))} · "
                    f"ETA {format_time(current.get('eta'))} · "
                    f"{format_size(current.get('downloaded'))} of {format_size(current.get('total'))}"
                )
            elif stage == "converting":
                stage_text = f"Converting to Opus at {AUDIO_BITRATE} kbps…"
            elif stage == "tagging":
                stage_text = "Writing tags and cover art…"
            else:
                stage_text = "Starting…"
        else:
            title = "Waiting for the next song" if self.downloading else "No download running"
            playlist_name = "—"
            stage_text = ""
            song_percent = None

        self.detail_song.config(text=title)
        self.detail_stage.config(text=f"{stage_text}   (playlist: {playlist_name})" if stage_text else f"Playlist: {playlist_name}")
        self.detail_song_bar.config(value=song_percent or 0)
        self.detail_overall.config(
            text=f"Overall: {done}/{total} songs ({overall_percent:.0f}%) · elapsed {format_time(elapsed)} · "
            f"estimated remaining {format_time(remaining)}"
        )
        completed = "; ".join(batch["finished"]) or "none yet"
        self.detail_playlists.config(text=f"Completed playlists: {completed}")
        self.detail_counts.config(
            text=f"Saved {counts['saved']} · Already present {counts['skipped']} · "
            f"Failed {counts['failed']} · Remaining {total - done}"
        )

        if self.downloading:
            streaming = current is not None and current.get("stage") == "downloading"
            speed = format_speed(current.get("speed")) if streaming else "—"
            eta = format_time(current.get("eta")) if streaming else "—"
            percent_text = f" · {song_percent:.0f}% of song" if song_percent is not None else ""
            self.status.config(
                text=f"{done}/{total} ({overall_percent:.0f}%) · {title}{percent_text} · {speed} · ETA {eta} · "
                f"{counts['failed']} failed"
            )

    def _mark_failed(self, row: SongRow, message: str):
        row.failed = True
        row.result.set(f"Failed: {message}")
        self.download_failures[row] = (datetime.now().strftime("%H:%M:%S"), message)
        self._refresh_download_errors()
        self._refresh_row_state(row)
        self._reorder_attention()

    def _clear_failure(self, row: SongRow):
        row.failed = False
        if self.download_failures.pop(row, None) is not None:
            self._refresh_download_errors()

    def _reorder_attention(self):
        for tab in self.tabs.values():
            tab.reorder()

    def _build_error_tabs(self):
        self.fetch_frame, self.fetch_tree = self._make_error_tab("Fetch Errors")
        for entry in self.fetch_log:
            self.fetch_tree.insert("", "end", values=entry)

        self.download_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.download_frame, text="Download Errors")
        header = ttk.Frame(self.download_frame, padding=(8, 6))
        header.pack(fill="x")
        ttk.Button(header, text="Retry All", command=self._retry_all_failed).pack(side="left")
        self.download_tree = self._make_error_tree(self.download_frame)
        self._refresh_download_errors()
        self._update_error_labels()

    def _make_error_tab(self, title: str) -> tuple[ttk.Frame, ttk.Treeview]:
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text=title)
        return frame, self._make_error_tree(frame)

    def _make_error_tree(self, frame: ttk.Frame) -> ttk.Treeview:
        tree = ttk.Treeview(frame, columns=("time", "playlist", "song", "message"), show="headings")
        tree.heading("time", text="Time")
        tree.heading("playlist", text="Playlist")
        tree.heading("song", text="Song")
        tree.heading("message", text="Error")
        tree.column("time", width=80, stretch=False)
        tree.column("playlist", width=140, stretch=False)
        tree.column("song", width=260, stretch=False)
        tree.column("message", width=520)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y", pady=8)
        tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        return tree

    def _log_fetch_error(self, playlist: str, song: str, message: str):
        entry = (datetime.now().strftime("%H:%M:%S"), playlist, song, message)
        self.fetch_log.append(entry)
        if self.fetch_tree is not None:
            self.fetch_tree.insert("", "end", values=entry)
        self._update_error_labels()

    def _refresh_download_errors(self):
        if self.download_tree is None:
            return
        self.download_tree.delete(*self.download_tree.get_children())
        for row, (when, message) in self.download_failures.items():
            self.download_tree.insert("", "end", values=(when, row.song.playlist, row.song.title, message))
        self._update_error_labels()

    def _update_error_labels(self):
        if self.fetch_frame is not None:
            self.notebook.tab(self.fetch_frame, text=f"Fetch Errors ({len(self.fetch_log)})")
        if self.download_frame is not None:
            self.notebook.tab(self.download_frame, text=f"Download Errors ({len(self.download_failures)})")

    def _on_selection_changed(self, *_args):
        # Select all can flip many checkboxes at once, so refresh the count once per pass of the event loop.
        if not self._selection_refresh_pending:
            self._selection_refresh_pending = True
            self.after_idle(self._update_selected_label)

    def _update_selected_label(self):
        self._selection_refresh_pending = False
        selected = sum(1 for row in self.rows if row.selected.get())
        self.selected_label.config(text=f"Selected: {selected} of {len(self.rows)} songs")

    def embed_album_art_tool(self):
        source = filedialog.askopenfilename(
            title="Choose an audio file",
            filetypes=[("Audio files", album_art.AUDIO_TYPES), ("All files", "*.*")],
            parent=self,
        )
        if not source:
            return
        image = filedialog.askopenfilename(
            title="Choose the album art image",
            filetypes=[("Images", IMAGE_TYPES), ("All files", "*.*")],
            parent=self,
        )
        if not image:
            return
        source_path = Path(source)
        destination = filedialog.asksaveasfilename(
            title="Save the Opus file as",
            initialfile=f"{source_path.stem}.opus",
            defaultextension=".opus",
            filetypes=[("Opus audio", "*.opus")],
            parent=self,
        )
        if not destination:
            return
        self.status.config(text=f"Embedding album art into {source_path.name}…")
        threading.Thread(
            target=self._embed_art_worker, args=(source_path, Path(image), Path(destination)), daemon=True
        ).start()

    def _embed_art_worker(self, source: Path, image: Path, destination: Path):
        try:
            album_art.embed_album_art(source, image, destination)
            self.events.put(("art_embedded", destination, None))
        except Exception as exc:
            self.events.put(("art_embedded", destination, str(exc)))

    def normalize_loudness(self):
        if self.normalizing:
            messagebox.showinfo("Busy", "A loudness job is already running.", parent=self)
            return
        folder = filedialog.askdirectory(title="Choose the folder of Opus files to normalize", parent=self)
        if not folder:
            return
        files = loudness.list_tracks(folder)
        if not files:
            messagebox.showinfo("No files", f"There are no Opus files in {folder}.", parent=self)
            return
        target = simpledialog.askfloat(
            APP_NAME,
            "Target integrated loudness in LUFS:",
            initialvalue=loudness.DEFAULT_TARGET,
            minvalue=-30.0,
            maxvalue=-8.0,
            parent=self,
        )
        if target is None:
            return
        if not messagebox.askyesno(
            "Normalize loudness?",
            f"Normalize {len(files)} files to {target:.1f} LUFS?\n\n"
            "Each file is re-encoded and replaced in place. Files already within "
            f"{loudness.TOLERANCE} LU of the target are left alone.",
            parent=self,
        ):
            return

        self.normalizing = True
        self.status.config(text=f"Normalizing 0/{len(files)} files…")
        self._log_console(f"Normalizing {len(files)} files in {folder} to {target:.1f} LUFS")
        threading.Thread(target=self._normalize_worker, args=(files, target), daemon=True).start()

    def _normalize_worker(self, files: list[Path], target: float):
        counts: Counter = Counter()
        for index, path in enumerate(files, start=1):
            try:
                outcome = loudness.normalize_file(path, target)
            except Exception as exc:
                outcome = "failed"
                self.events.put(("log", f"Could not normalize {path.name}: {exc}"))
            counts[outcome] += 1
            if outcome == "normalized":
                self.events.put(("log", f"Normalized {path.name}"))
            self.events.put(("normalize_progress", index, len(files)))
        self.events.put(("normalize_done", dict(counts)))

    def remove_duplicates(self):
        folder = filedialog.askdirectory(title="Choose the folder to scan for duplicate songs", parent=self)
        if not folder:
            return
        self.status.config(text=f"Scanning {folder} for duplicates…")
        threading.Thread(target=self._scan_duplicates, args=(folder,), daemon=True).start()

    def _scan_duplicates(self, folder: str):
        try:
            groups = duplicates.find_duplicates(duplicates.scan_folder(folder))
            self.events.put(("duplicates", folder, groups, None))
        except Exception as exc:
            self.events.put(("duplicates", folder, [], str(exc)))

    def _drain_events(self):
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "resolved":
                _, row, best, error = event
                row.resolved = True
                row.auto_candidate = best
                if error:
                    row.result.set(f"Search failed: {error[:200]}")
                    self._log_fetch_error(row.song.playlist, row.song.title, f"Search failed: {error}")
                if not row.manual:
                    if best is None:
                        self._apply(row, None, "No automatic match — use Change URL")
                    else:
                        self._apply(row, best, "Automatic match")
                self._reorder_attention()
                self._update_matching_status()
            elif kind == "album_art":
                _, row, video_id, image = event
                self.album_art_cache[video_id] = image
                self._show_album_art(row, video_id, image)
            elif kind == "cover":
                _, row, data, error = event
                if error:
                    messagebox.showerror("Could not use this image", error, parent=self)
                    self._restore_album_art(row)
                else:
                    row.cover_override = data
                    self._set_album_art(row, "", ImageTk.PhotoImage(covers.preview_from_cover(data)))
            elif kind == "result":
                _, row, text = event
                row.result.set(text)
            elif kind == "detail":
                _, row, info = event
                self._on_detail(row, info)
            elif kind == "outcome":
                _, row, outcome, text = event
                self._on_outcome(row, outcome, text)
            elif kind == "log":
                self._log_console(event[1])
            elif kind == "playlist_done":
                _, playlist, counts = event
                self._on_playlist_done(playlist, counts)
            elif kind == "done":
                self._on_done(event[1])
            elif kind == "error":
                _, playlist, song, message = event
                self._log_fetch_error(playlist, song, message)
            elif kind == "import_ready":
                _, playlists, what = event
                self._add_playlists(playlists, f"YouTube {what}")
            elif kind == "import_error":
                _, what, message = event
                self._log_fetch_error("Import", f"YouTube {what}", message)
                messagebox.showerror("Import failed", message, parent=self)
            elif kind == "sample":
                _, row, path, error = event
                row.sample_loading = False
                self._render_sample_state(row)
                if error:
                    if self.playing_row is row:
                        self.playing_row = None
                    self._log_console(f"Sample failed for {row.song.title}: {error}")
                    messagebox.showerror(
                        "Sample failed", f"Could not fetch a preview for “{row.song.title}”.\n\n{error}", parent=self
                    )
                else:
                    row.sample_path = path
                    if self.playing_row is row:
                        self._start_playback(row)
            elif kind == "normalize_progress":
                _, done, total = event
                self.status.config(text=f"Normalizing {done}/{total} files…")
            elif kind == "art_embedded":
                _, destination, error = event
                if error:
                    self._log_console(f"Album art failed for {destination.name}: {error}")
                    messagebox.showerror("Could not embed album art", error, parent=self)
                else:
                    self._log_console(f"Saved {destination}")
                    messagebox.showinfo("Album art embedded", f"Saved:\n{destination}", parent=self)
                self._update_matching_status()
            elif kind == "normalize_done":
                counts = event[1]
                self.normalizing = False
                summary = (
                    f"Loudness: {counts.get('normalized', 0)} normalized, "
                    f"{counts.get('unchanged', 0)} already at target, {counts.get('failed', 0)} failed"
                )
                self.status.config(text=summary)
                self._log_console(summary)
            elif kind == "duplicates":
                _, folder, groups, error = event
                if error:
                    messagebox.showerror("Scan failed", error, parent=self)
                elif not groups:
                    messagebox.showinfo("No duplicates", f"No duplicate songs found in {folder}.", parent=self)
                else:
                    DuplicatesDialog(
                        self, folder, groups, self._log_console, lambda text: self.status.config(text=text)
                    )
                self.status.config(text="")
        if self.batch is not None and self.downloading:
            self._render_details()
        if self.sample_process is not None and self.sample_process.poll() is not None:
            self._stop_sample()  # the preview finished on its own
        self.after(100, self._drain_events)

    def _update_matching_status(self):
        if self.downloading or self.download_summary:
            return
        pending = sum(1 for row in self.rows if not row.resolved)
        if pending:
            self.status.config(text=f"Matching songs on YouTube… {pending} left")
        else:
            matched = sum(1 for row in self.rows if row.candidate is not None)
            self.status.config(text=f"Ready: {matched}/{len(self.rows)} songs matched")

    def _on_tab_changed(self, _event):
        self.active_tab = next((tab for tab in self.tabs.values() if str(tab.page) == self.notebook.select()), None)

    def _on_wheel(self, event):
        if self.active_tab is None:
            return
        if event.num == 4 or event.delta > 0:
            self.active_tab.wheel(-1)
        elif event.num == 5 or event.delta < 0:
            self.active_tab.wheel(1)

    def _on_close(self):
        self._stop_sample()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.destroy()


def main():
    App(sys.argv[1] if len(sys.argv) > 1 else None).mainloop()


if __name__ == "__main__":
    main()
