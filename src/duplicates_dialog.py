import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from duplicates import DuplicateGroup, TrackFile
from formatting import format_size


class DuplicatesDialog(tk.Toplevel):
    """Lists the extra copies from duplicates.find_duplicates. Checked files are deleted on request."""

    def __init__(
        self,
        master: tk.Misc,
        folder: str,
        groups: list[DuplicateGroup],
        log: Callable[[str], None],
        set_status: Callable[[str], None],
    ):
        super().__init__(master)
        self.title("Remove Duplicates")
        self.transient(master)
        self.geometry("980x560")
        self.folder = folder
        self.log = log
        self.set_status = set_status
        self.tracks: dict[str, TrackFile] = {}
        self.checked: dict[str, bool] = {}

        removable = sum(len(group.remove) for group in groups)
        ttk.Label(
            self,
            text=f"Found {len(groups)} groups of similar songs ({removable} extra files) in {folder}.\n"
            "The largest copy in each group is kept. Checked files will be permanently deleted; "
            "click a row's box to change it.",
            padding=12,
        ).pack(anchor="w")

        frame = ttk.Frame(self, padding=(12, 0))
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("remove", "group", "file", "kept", "size"), show="headings", selectmode="browse")
        for column, text, width, stretch in (
            ("remove", "Delete", 60, False),
            ("group", "Group", 60, False),
            ("file", "File", 400, True),
            ("kept", "Kept copy", 300, True),
            ("size", "Size", 90, False),
        ):
            self.tree.heading(column, text=text)
            self.tree.column(column, width=width, stretch=stretch)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        for group_number, group in enumerate(groups, start=1):
            for track in group.remove:
                iid = self.tree.insert(
                    "",
                    "end",
                    values=("☑", group_number, str(track.path.relative_to(folder)), group.keep.path.name, format_size(track.size)),
                )
                self.tracks[iid] = track
                self.checked[iid] = True

        self.tree.bind("<Button-1>", self._toggle)

        buttons = ttk.Frame(self, padding=12)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Select all", command=lambda: self._set_all(True)).pack(side="left")
        ttk.Button(buttons, text="Deselect all", command=lambda: self._set_all(False)).pack(side="left", padx=4)
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Delete checked", command=self._delete_checked).pack(side="right", padx=(0, 8))
        self.grab_set()

    def _set_checked(self, iid: str, value: bool):
        self.checked[iid] = value
        self.tree.set(iid, "remove", "☑" if value else "☐")

    def _set_all(self, value: bool):
        for iid in self.checked:
            self._set_checked(iid, value)

    def _toggle(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell" or self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        if iid:
            self._set_checked(iid, not self.checked[iid])

    def _delete_checked(self):
        chosen = [iid for iid, value in self.checked.items() if value]
        if not chosen:
            messagebox.showinfo("Nothing selected", "No files are checked.", parent=self)
            return
        if not messagebox.askyesno(
            "Delete files?",
            f"Permanently delete {len(chosen)} files?\n\nThis cannot be undone.",
            parent=self,
        ):
            return
        deleted = 0
        for iid in chosen:
            track = self.tracks[iid]
            try:
                track.path.unlink()
            except OSError as exc:
                self.log(f"Could not delete {track.path.name}: {exc}")
                continue
            self.tree.delete(iid)
            del self.checked[iid]
            deleted += 1
        self.log(f"Removed {deleted} duplicate files from {self.folder}")
        self.set_status(f"Removed {deleted} duplicate files")
