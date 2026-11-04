import sys
from pathlib import Path

APP_NAME = "Argh Matey Music Downloader"
AUDIO_BITRATE = "160"  # kbps, Opus
COVER_SIZE = 512

# In the bundled executable the data lives beside the .exe; from source it lives at the project root.
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parents[1]

OUTPUT_DIR = APP_DIR / "OUTPUT"
CACHE_DIR = APP_DIR / ".cache"
CACHE_FILE = CACHE_DIR / "search.json"
SAMPLE_DIR = CACHE_DIR / "samples"
SAMPLE_SECONDS = 10
EXAMPLE_XLSX = APP_DIR / "EXAMPLE EXCEL SHEET.xlsx"
