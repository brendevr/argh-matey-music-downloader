import math
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk


class DotSpinner(tk.Canvas):
    """Eight dots on a circle, one highlighted at a time."""

    DOTS = 8
    FRAME_MS = 90

    def __init__(self, master: tk.Misc, color: str, dim: str, background: str, size: int = 44):
        super().__init__(master, width=size, height=size, highlightthickness=0, borderwidth=0, bg=background)
        self.color = color
        self.dim = dim
        self.frame = 0
        self.job = None
        self.dots = []
        centre = size / 2
        radius = size / 2 - 6
        for index in range(self.DOTS):
            angle = 2 * math.pi * index / self.DOTS - math.pi / 2
            x = centre + radius * math.cos(angle)
            y = centre + radius * math.sin(angle)
            self.dots.append(self.create_oval(x - 3.5, y - 3.5, x + 3.5, y + 3.5, outline=""))
        self.bind("<Destroy>", lambda _event: self._cancel())
        self._tick()

    def _tick(self):
        for index, dot in enumerate(self.dots):
            self.itemconfigure(dot, fill=self.color if index == self.frame else self.dim)
        self.frame = (self.frame + 1) % self.DOTS
        self.job = self.after(self.FRAME_MS, self._tick)

    def _cancel(self):
        if self.job is not None:
            self.after_cancel(self.job)
            self.job = None


def media_icon(kind: str, color: str) -> ImageTk.PhotoImage:
    """Draw a play triangle or stop square. Drawn rather than typed, since many fonts lack the ▶ glyph."""
    image = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if kind == "play":
        draw.polygon([(6, 4), (16, 10), (6, 16)], fill=color)
    else:
        draw.rectangle((5, 5, 14, 14), fill=color)
    return ImageTk.PhotoImage(image)
