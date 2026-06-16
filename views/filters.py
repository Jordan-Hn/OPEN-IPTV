"""Manage Categories: choose which categories appear in the browser and search."""

import tkinter as tk

from theme import (ACCENT, BG, BG_SUNKEN, FONT_BODY, FONT_CAPTION, FONT_H2,
                   FONT_SMALL, SURFACE, SURFACE_2, TEXT, TEXT_FAINT, TEXT_MUTED)
from widgets import ghost_button, primary_button


class FiltersView:
    def open_filter_window(self):
        win = tk.Toplevel(self.root)
        win.title("Manage Categories")
        win.configure(bg=BG)
        win.transient(self.root)
        win.grab_set()
        w, h = 640, 760
        win.geometry(
            f"{w}x{h}+{(win.winfo_screenwidth() - w) // 2}+{(win.winfo_screenheight() - h) // 2}")

        head = tk.Frame(win, bg=BG)
        head.pack(fill=tk.X, padx=24, pady=(22, 6))
        tk.Label(head, text="Manage Categories", font=FONT_H2, fg=TEXT, bg=BG).pack(anchor="w")
        tk.Label(head, text="Choose which categories appear in the browser and search.",
                 font=FONT_SMALL, fg=TEXT_MUTED, bg=BG).pack(anchor="w", pady=(4, 0))

        controls = tk.Frame(win, bg=BG)
        controls.pack(fill=tk.X, padx=24, pady=(10, 8))

        fcanvas = tk.Canvas(win, bg=BG_SUNKEN, highlightthickness=0, bd=0)
        fscroll = tk.Scrollbar(win, orient=tk.VERTICAL, command=fcanvas.yview,
                               bg=SURFACE, troughcolor=BG_SUNKEN, width=12, activebackground=ACCENT)
        fframe = tk.Frame(fcanvas, bg=BG_SUNKEN)
        fwin = fcanvas.create_window((0, 0), window=fframe, anchor="nw")
        fframe.bind("<Configure>", lambda e: fcanvas.configure(scrollregion=fcanvas.bbox("all")))
        fcanvas.bind("<Configure>", lambda e: fcanvas.itemconfig(fwin, width=e.width))
        fcanvas.configure(yscrollcommand=fscroll.set)
        fcanvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(24, 0), pady=4)
        fscroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 24), pady=4)

        self.set_scroll_target(fcanvas)

        var_dict: dict = {}

        def populate(groups):
            valid = [(g, n) for (g, n) in groups if g and g.strip() and n]
            for i, (grp, n) in enumerate(valid):
                var = tk.BooleanVar(value=(grp not in self.config.disabled_groups))
                shade = SURFACE if i % 2 == 0 else SURFACE_2
                card = tk.Frame(fframe, bg=shade)
                card.pack(fill=tk.X, padx=10, pady=2)
                chk = tk.Checkbutton(
                    card, text=f"  {grp}", variable=var, anchor="w",
                    bg=shade, fg=TEXT, selectcolor=SURFACE_2,
                    activebackground=shade, activeforeground=TEXT,
                    font=FONT_BODY, cursor="hand2", bd=0, highlightthickness=0, padx=8, pady=8)
                chk.pack(side=tk.LEFT, fill=tk.X, expand=True)
                tk.Label(card, text=f"{n:,}", font=FONT_CAPTION, fg=TEXT_FAINT,
                         bg=shade).pack(side=tk.RIGHT, padx=12)
                var_dict[grp] = var

        def select_all():
            for v in var_dict.values():
                v.set(True)

        def select_none():
            for v in var_dict.values():
                v.set(False)

        ghost_button(controls, "Select All", select_all).pack(side=tk.LEFT)
        ghost_button(controls, "Select None", select_none).pack(side=tk.LEFT, padx=(10, 0))

        footer_bar = tk.Frame(win, bg=BG)
        footer_bar.pack(fill=tk.X, padx=24, pady=16)

        def apply_changes():
            self.config.disabled_groups.clear()
            for grp, v in var_dict.items():
                if not v.get() and grp.strip():
                    self.config.disabled_groups.add(grp)
            self.config.save()
            self.set_scroll_target(self.canvas)
            win.destroy()
            self.show_groups()

        def cancel():
            self.set_scroll_target(self.canvas)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", cancel)
        primary_button(footer_bar, "Apply Changes", apply_changes, large=True).pack(side=tk.RIGHT)
        ghost_button(footer_bar, "Cancel", cancel, large=True).pack(side=tk.RIGHT, padx=(0, 10))

        self.submit(self.catalog.groups, populate, None)
