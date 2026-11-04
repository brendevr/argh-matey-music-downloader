# Argh Matey Music Downloader

Downloads the songs in a playlist spreadsheet, or a YouTube playlist or video, as tagged Opus audio files with cover art.

Currently, the app in its entirety has been produced by Claude Code. It has not been looked over for correctness, doneness, effeciency, or bugs.

## Using the app

1. Start the program (`ArghMateyMusic` on Linux, `ArghMateyMusic.exe` on Windows).
2. Add songs with **File**: *Import XLSX…*, or paste a YouTube playlist or video link.
3. Each song is matched to a YouTube video automatically. Use **Change URL** to pick a different video, **Override Album Art** to use your own cover, and the play button on a cover to hear a 10-second preview.
4. Tick the songs you want and press **Download selected**. Files are saved to `OUTPUT/<playlist name>/` next to the program.

The **Tools** menu has Remove Duplicates, Normalize Loudness, and Embed Album Art.

## Spreadsheet format

One row per song with the columns `Playlist | Song | Artist`. See `EXAMPLE EXCEL SHEET.xlsx`.

- A YouTube link in a comment on the Song cell selects that video directly, without searching.
- Bracketed notes such as `[movement unconfirmed]` are shown in the app but left out of the search.

## Requirements

- **FFmpeg** (`ffmpeg` and `ffplay`) on your PATH. Downloads, previews and the tools all need it.
- **Node.js** is recommended. yt-dlp uses it to get past YouTube's JavaScript challenges.

## Running from source

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python src/app.py
```

On Windows, use `.venv\Scripts\pip` and `.venv\Scripts\python`.

## Building the executable

PyInstaller cannot cross-compile, so build on the operating system you are targeting.

```
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name ArghMateyMusic --collect-all yt_dlp --hidden-import PIL._tkinter_finder --paths src src/app.py
```

The result is `dist/ArghMateyMusic` (or `dist/ArghMateyMusic.exe` on Windows). Put it in the same folder as `EXAMPLE EXCEL SHEET.xlsx`: the program keeps its cache and `OUTPUT/` folder beside itself.

## Layout

- `src/app.py`: the main window and the download queue.
- `src/config.py`: paths and constants.
- `src/playlists.py`: reads the spreadsheet.
- `src/youtube.py`, `src/youtube_import.py`: YouTube search, matching, previews and playlist import.
- `src/downloader.py`: downloads a song and writes its tags.
- `src/covers.py`, `src/ffmpeg_tools.py`: cover images and ffmpeg helpers.
- `src/album_art.py`, `src/loudness.py`, `src/duplicates.py`: the Tools menu features.
- `src/duplicates_dialog.py`, `src/playlist_tab.py`, `src/widgets.py`, `src/theme.py`, `src/formatting.py`: interface pieces.
