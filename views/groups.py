"""Categories (groups) grid: the home screen."""

import tkinter as tk

from theme import (ACCENT, BG_SUNKEN, BORDER, FONT_CAPTION, FONT_CARD, HOVER,
                   SURFACE, TEXT, TEXT_MUTED)
from widgets import ghost_button, pointer_inside, primary_button


def _columns_for_width(width):
    if width > 2100:
        return 5
    if width > 1650:
        return 4
    if width > 1180:
        return 3
    if width > 820:
        return 2
    return 1


class GroupsView:
    def show_groups(self):
        gen = self.new_view()
        self.root.title("IPTV Stream Browser")

        for w in self.header_actions.winfo_children():
            w.destroy()
        fav_label = (f"★ Favourites ({len(self.config.favourites)})"
                     if self.config.favourites else "★ Favourites")
        ghost_button(self.header_actions, fav_label, self.show_favourites).pack(side=tk.LEFT, padx=(0, 10))
        ghost_button(self.header_actions, "Categories", self.open_filter_window).pack(side=tk.LEFT, padx=(0, 10))
        ghost_button(self.header_actions, "Settings",
                     lambda: self.open_settings(first_run=False)).pack(side=tk.LEFT, padx=(0, 10))
        primary_button(self.header_actions, "Search", self.open_search_window).pack(side=tk.LEFT)

        grid = tk.Frame(self.body, bg=BG_SUNKEN)
        grid.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)

        def render(groups):
            if gen != self.view_gen:
                return
            enabled = [(g, n) for (g, n) in groups if g not in self.config.disabled_groups and n]
            total_groups = len(groups)
            hidden = total_groups - len(enabled)
            chan_total = sum(n for _, n in enabled)
            self.subtitle_label.config(
                text=f"{len(enabled):,} categories · {chan_total:,} channels"
                + (f" · {hidden:,} hidden" if hidden else ""))

            # Lightweight cards (3 widgets each) keep the grid cheap to lay out.
            cards = []
            title_labels = []
            for grp, n in enabled:
                card = tk.Frame(grid, bg=SURFACE, highlightthickness=1,
                                highlightbackground=BORDER, highlightcolor=BORDER)
                title = tk.Label(card, text=grp, font=FONT_CARD, fg=TEXT, bg=SURFACE,
                                 anchor="w", justify="left", wraplength=240)
                title.pack(fill=tk.X, padx=16, pady=(14, 0))
                count = tk.Label(card, text=f"{n:,} channels", font=FONT_CAPTION,
                                 fg=TEXT_MUTED, bg=SURFACE, anchor="w")
                count.pack(fill=tk.X, padx=16, pady=(2, 14))
                widgets = (card, title, count)

                def on_enter(_=None, ws=widgets, c=card):
                    for w in ws:
                        try:
                            w.config(bg=HOVER)
                        except tk.TclError:
                            pass
                    c.config(highlightbackground=ACCENT, highlightcolor=ACCENT)

                def on_leave(_=None, ws=widgets, c=card):
                    if pointer_inside(c):
                        return
                    for w in ws:
                        try:
                            w.config(bg=SURFACE)
                        except tk.TclError:
                            pass
                    c.config(highlightbackground=BORDER, highlightcolor=BORDER)

                for w in widgets:
                    w.bind("<Enter>", on_enter, add="+")
                    w.bind("<Leave>", on_leave, add="+")
                    w.bind("<Button-1>", lambda e, g=grp: self.show_channels(g, 0))
                    w.config(cursor="hand2")
                cards.append(card)
                title_labels.append(title)

            def relayout():
                cols = _columns_for_width(self.root.winfo_width())
                if getattr(relayout, "_cols", None) == cols:
                    return  # column count unchanged: cards are already placed correctly
                relayout._cols = cols
                for i in range(8):
                    grid.grid_columnconfigure(i, weight=1 if i < cols else 0,
                                              uniform="cat" if i < cols else "")
                for idx, card in enumerate(cards):
                    card.grid(row=idx // cols, column=idx % cols, sticky="nsew", padx=8, pady=8)
                wrap = max(180, (self.root.winfo_width() - 120) // cols - 60)
                for lbl in title_labels:
                    try:
                        lbl.config(wraplength=wrap)
                    except tk.TclError:
                        pass

            self.set_relayout(relayout)
            self.apply_layout()
            tk.Frame(self.body, bg=BG_SUNKEN, height=24).pack()

        self.submit(self.catalog.groups, render, gen)
