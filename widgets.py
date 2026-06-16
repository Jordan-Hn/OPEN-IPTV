"""
Small reusable Tk widgets and helpers: a thin progress bar, themed buttons,
flicker-free hover, click binding, a labelled entry, and the logo placeholder /
rounding helpers. All styling comes from theme.py.
"""

import tkinter as tk

from PIL import Image, ImageDraw

from theme import (ACCENT, ACCENT_HOV, ACCENT_INK, BORDER, BG, FONT_BODY, FONT_BTN,
                   FONT_BTN_LG, FONT_SMALL, HOVER, LOGO_BG, LOGO_SIZE, SURFACE_2,
                   TEXT, TEXT_FAINT, TEXT_MUTED, rgb)


class MiniProgress(tk.Canvas):
    """A thin progress bar. Determinate (set_fraction) or an indeterminate
    marquee (start_pulse) for work whose total isn't known yet."""

    def __init__(self, parent, bg, fill=ACCENT, height=4, **kw):
        super().__init__(parent, height=height, bg=bg, highlightthickness=0, bd=0, **kw)
        self._fill = fill
        self._fraction = 0.0
        self._indet = False
        self._pos = 0.0
        self._track = self.create_rectangle(0, 0, 0, height, fill=BORDER, width=0)
        self._bar = self.create_rectangle(0, 0, 0, height, fill=fill, width=0)
        self.bind("<Configure>", lambda e: self._redraw())

    def set_fraction(self, fraction):
        self._indet = False
        self._fraction = max(0.0, min(1.0, fraction))
        self._redraw()

    def start_pulse(self, step=0.012, interval=16):
        """Animate a segment sweeping across, so the bar always shows motion."""
        if self._indet:
            return
        self._indet = True
        self._pos = 0.0
        self._pulse(step, interval)

    def stop_pulse(self):
        self._indet = False

    def _pulse(self, step, interval):
        if not self._indet:
            return
        try:
            self._pos = (self._pos + step) % 1.0
            self._redraw()
            self.after(interval, lambda: self._pulse(step, interval))
        except tk.TclError:
            self._indet = False  # widget destroyed; stop quietly

    def _redraw(self):
        try:
            w = self.winfo_width()
            h = int(self["height"])
            self.coords(self._track, 0, 0, w, h)
            if self._indet:
                seg = max(40, int(w * 0.22))
                x = int((w + seg) * self._pos) - seg
                self.coords(self._bar, x, 0, x + seg, h)
            else:
                self.coords(self._bar, 0, 0, int(w * self._fraction), h)
        except tk.TclError:
            pass


def primary_button(parent, text, command, large=False):
    btn = tk.Button(
        parent, text=text, command=command,
        bg=ACCENT, fg=ACCENT_INK,
        activebackground=ACCENT_HOV, activeforeground=ACCENT_INK,
        font=FONT_BTN_LG if large else FONT_BTN,
        relief="flat", bd=0, cursor="hand2",
        padx=24 if large else 16, pady=10 if large else 7,
    )
    btn.bind("<Enter>", lambda e: btn.config(bg=ACCENT_HOV))
    btn.bind("<Leave>", lambda e: btn.config(bg=ACCENT))
    return btn


def ghost_button(parent, text, command, large=False):
    btn = tk.Button(
        parent, text=text, command=command,
        bg=SURFACE_2, fg=TEXT,
        activebackground=HOVER, activeforeground=TEXT,
        font=FONT_BTN_LG if large else FONT_BTN,
        relief="flat", bd=0, cursor="hand2",
        padx=20 if large else 14, pady=10 if large else 7,
    )
    btn.bind("<Enter>", lambda e: btn.config(bg=HOVER))
    btn.bind("<Leave>", lambda e: btn.config(bg=SURFACE_2))
    return btn


def pointer_inside(widget):
    """True if the mouse pointer is within ``widget``'s bounds. Used by hover
    handlers so moving onto a child doesn't flicker the parent off."""
    try:
        px, py = widget.winfo_pointerxy()
        x, y = widget.winfo_rootx(), widget.winfo_rooty()
        return x <= px < x + widget.winfo_width() and y <= py < y + widget.winfo_height()
    except tk.TclError:
        return True  # assume inside on error, so we don't un-hover spuriously


def add_hover(container, recolor_widgets, base, hover):
    """Flicker-free hover: re-colours ``recolor_widgets`` while the pointer is
    anywhere within ``container``."""

    def enter(_=None):
        for w in recolor_widgets:
            try:
                w.config(bg=hover)
            except tk.TclError:
                pass

    def leave(_=None):
        if pointer_inside(container):
            return
        for w in recolor_widgets:
            try:
                w.config(bg=base)
            except tk.TclError:
                pass

    for w in [container, *recolor_widgets]:
        w.bind("<Enter>", enter, add="+")
        w.bind("<Leave>", leave, add="+")


def bind_click(widgets, command):
    for w in widgets:
        w.bind("<Button-1>", lambda e: command())
        try:
            w.config(cursor="hand2")
        except tk.TclError:
            pass


def settings_entry(parent, label, value="", show=None, browse=None):
    """A labelled text field, optionally with a Browse button on the right."""
    tk.Label(parent, text=label, font=FONT_SMALL, fg=TEXT_MUTED, bg=BG,
             anchor="w").pack(anchor="w", pady=(10, 2))
    wrap = tk.Frame(parent, bg=SURFACE_2, highlightthickness=1,
                    highlightbackground=BORDER, highlightcolor=ACCENT)
    wrap.pack(fill=tk.X)
    entry = tk.Entry(wrap, font=FONT_BODY, bg=SURFACE_2, fg=TEXT, relief="flat",
                     bd=0, insertbackground=ACCENT, show=(show or ""))
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=7, padx=10, pady=2)
    if value:
        entry.insert(0, value)
    if browse:
        ghost_button(wrap, "Browse", browse).pack(side=tk.RIGHT, padx=3, pady=2)
    return entry


def make_placeholder(size=LOGO_SIZE, radius=14):
    """A neutral rounded logo tile with a faint play glyph, shown until a real
    logo loads."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=rgb(LOGO_BG))
    cx, cy = size[0] / 2, size[1] / 2
    d.polygon([(cx - 7, cy - 9), (cx - 7, cy + 9), (cx + 10, cy)],
              fill=(rgb(TEXT_FAINT) + (180,)))
    return img


def round_logo(pil_img, radius=12):
    """Round the corners of a loaded logo so it sits nicely on the tile."""
    pil_img = pil_img.convert("RGBA")
    mask = Image.new("L", pil_img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, pil_img.size[0] - 1, pil_img.size[1] - 1], radius=radius, fill=255)
    base = Image.new("RGBA", pil_img.size, rgb(LOGO_BG) + (255,))
    base.paste(pil_img, (0, 0), pil_img)
    base.putalpha(mask)
    return base
