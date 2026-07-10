"""Categories (groups) grid: the home screen."""

import tkinter as tk

from iptv.ui.theme import (BG_SUNKEN, BORDER, FONT_CAPTION, FONT_CARD, FONT_SMALL,
                   HOVER, SURFACE, TEXT, TEXT_MUTED)
from iptv.ui.widgets import card_hover, ghost_button, primary_button


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
    def _continue_strip(self, parent, recent):
        """A row of 'continue watching' cards at the top of the home screen."""
        wrap = tk.Frame(parent, bg=BG_SUNKEN)
        wrap.pack(fill=tk.X, padx=20, pady=(16, 0))
        tk.Label(wrap, text="Continue watching", font=FONT_CARD, fg=TEXT, bg=BG_SUNKEN,
                 anchor="w").pack(anchor="w", pady=(0, 8))
        strip = tk.Frame(wrap, bg=BG_SUNKEN)
        strip.pack(fill=tk.X)
        for rec in recent:
            self._continue_card(strip, rec)

    def _continue_card(self, parent, rec):
        card = tk.Frame(parent, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER,
                        highlightcolor=BORDER, width=196, height=66)
        card.pack(side=tk.LEFT, padx=(0, 10))
        card.pack_propagate(False)
        title = rec.get("series") or rec.get("name") or "Unknown"
        tlabel = tk.Label(card, text=title, font=FONT_SMALL, fg=TEXT, bg=SURFACE,
                          anchor="w", justify="left", wraplength=176)
        tlabel.pack(fill=tk.X, padx=10, pady=(9, 0))
        caption = "Play again"
        if rec.get("series"):
            nxt = self.catalog.next_episode(
                rec["series"], rec.get("season", 0), rec.get("episode", 0),
                sorted(self.config.disabled_groups), rec.get("grp"))
            caption = ("Next: S%02dE%02d" % (nxt["season"], nxt["episode"])) if nxt else "Latest episode"
        else:
            saved = self.config.get_position(rec.get("url", ""))
            if saved and saved.get("pos", 0) > 15:
                mins, secs = divmod(int(saved["pos"]), 60)
                caption = f"Resume {mins}:{secs:02d}"
        clabel = tk.Label(card, text=caption, font=FONT_CAPTION, fg=TEXT_MUTED, bg=SURFACE, anchor="w")
        clabel.pack(fill=tk.X, padx=10, pady=(2, 0))
        widgets = (card, tlabel, clabel)
        card_hover(card, widgets, SURFACE, HOVER)
        for w in widgets:
            w.bind("<Button-1>", lambda e, r=rec: self.play_continue(r))
            w.config(cursor="hand2")

    def show_groups(self):
        gen = self.new_view()
        self.root.title("IPTV Stream Browser")

        self.clear_header()
        fav_label = (f"★ Favourites ({len(self.config.favourites)})"
                     if self.config.favourites else "★ Favourites")
        ghost_button(self.header_actions, fav_label, self.show_favourites).pack(side=tk.LEFT, padx=(0, 10))
        ghost_button(self.header_actions, "History", self.show_history).pack(side=tk.LEFT, padx=(0, 10))
        ghost_button(self.header_actions, "Categories", self.open_filter_window).pack(side=tk.LEFT, padx=(0, 10))
        ghost_button(self.header_actions, "Refresh", self.refresh_source).pack(side=tk.LEFT, padx=(0, 10))
        ghost_button(self.header_actions, "Settings",
                     lambda: self.open_settings(first_run=False)).pack(side=tk.LEFT, padx=(0, 10))
        primary_button(self.header_actions, "Search", self.open_search_window).pack(side=tk.LEFT)

        recent = self.config.history_items()[:6]
        if recent:
            self._continue_strip(self.body, recent)

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
                card_hover(card, widgets, SURFACE, HOVER)
                for w in widgets:
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
