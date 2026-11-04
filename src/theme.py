import json
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from config import APP_NAME

PALETTES = {
    "light": {"bg": "#f0f0f0", "fg": "#000000", "field": "#ffffff", "select": "#0078d7", "muted": "#e1e1e1", "border": "#c8c8c8"},
    "dark": {"bg": "#202124", "fg": "#e8eaed", "field": "#2b2d30", "select": "#3d5a80", "muted": "#34363a", "border": "#4a4d52"},
}


def config_file() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_NAME / "settings.json"


def _read_settings() -> dict:
    try:
        return json.loads(config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_choice() -> str | None:
    """The saved "light" or "dark" choice, or None if the user has not chosen one yet."""
    value = _read_settings().get("theme")
    return value if value in PALETTES else None


def save_choice(choice: str) -> None:
    settings = _read_settings()
    settings["theme"] = choice
    try:
        path = config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except OSError:
        pass  # the saved choice is a convenience; the app keeps working without it


def _command_output(command: list[str]) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def os_prefers_dark() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            ) as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        except OSError:
            return False
    if sys.platform == "darwin":
        return _command_output(["defaults", "read", "-g", "AppleInterfaceStyle"]) == "Dark"

    # GNOME reports "prefer-dark"; KDE names its colour scheme, e.g. "BreezeDark".
    if "prefer-dark" in _command_output(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"]):
        return True
    scheme = _command_output(["kreadconfig6", "--file", "kdeglobals", "--group", "General", "--key", "ColorScheme"])
    return "dark" in scheme.lower()


def _style_tk_widgets(widget: tk.Misc, palette: dict) -> None:
    """Classic Tk widgets are not styled by ttk, so colour them directly."""
    for child in widget.winfo_children():
        if isinstance(child, (tk.Frame, tk.Toplevel)):
            child.configure(bg=palette["bg"])
        elif isinstance(child, tk.Text):
            child.configure(bg=palette["field"], fg=palette["fg"], insertbackground=palette["fg"])
        elif isinstance(child, tk.Menu):
            child.configure(
                bg=palette["bg"], fg=palette["fg"], activebackground=palette["select"], activeforeground="#ffffff"
            )
        _style_tk_widgets(child, palette)


def apply(root: tk.Tk, dark: bool) -> dict:
    palette = PALETTES["dark" if dark else "light"]
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(
        ".",
        background=palette["bg"],
        foreground=palette["fg"],
        fieldbackground=palette["field"],
        bordercolor=palette["border"],
        lightcolor=palette["bg"],
        darkcolor=palette["bg"],
        troughcolor=palette["muted"],
        selectbackground=palette["select"],
        selectforeground="#ffffff",
        insertcolor=palette["fg"],
        arrowcolor=palette["fg"],
    )
    style.configure("TButton", background=palette["muted"], foreground=palette["fg"])
    style.map("TButton", background=[("active", palette["border"]), ("pressed", palette["select"])])
    style.configure("TNotebook.Tab", background=palette["muted"], foreground=palette["fg"])
    style.map("TNotebook.Tab", background=[("selected", palette["bg"])])
    style.configure("Treeview", background=palette["field"], fieldbackground=palette["field"], foreground=palette["fg"])
    style.configure("Treeview.Heading", background=palette["muted"], foreground=palette["fg"])
    style.map("Treeview", background=[("selected", palette["select"])])
    style.configure("TEntry", fieldbackground=palette["field"], foreground=palette["fg"])
    style.configure("TProgressbar", background=palette["select"], troughcolor=palette["muted"])
    style.configure(
        "TScrollbar",
        background=palette["muted"],
        troughcolor=palette["bg"],
        bordercolor=palette["bg"],
        lightcolor=palette["muted"],
        darkcolor=palette["muted"],
        arrowcolor=palette["fg"],
    )
    style.map("TScrollbar", background=[("active", palette["border"]), ("pressed", palette["select"])])
    style.configure(
        "TCheckbutton",
        background=palette["bg"],
        foreground=palette["fg"],
        indicatorbackground=palette["field"],
        indicatorforeground=palette["fg"],
    )
    style.map(
        "TCheckbutton",
        background=[("active", palette["bg"])],
        indicatorbackground=[("selected", palette["select"]), ("active", palette["muted"])],
    )

    root.configure(bg=palette["bg"])
    _style_tk_widgets(root, palette)
    return palette
