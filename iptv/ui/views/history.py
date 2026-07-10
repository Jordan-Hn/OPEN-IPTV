"""Recently watched: the play history, replayable and clearable."""

import tkinter as tk

from iptv.ui.theme import BG_SUNKEN, FONT_H2, FONT_SMALL, TEXT, TEXT_FAINT, TEXT_MUTED
from iptv.ui.widgets import ghost_button


class HistoryView:
    def show_history(self, page=0):
        gen = self.new_view()
        self.root.title("Recently watched · IPTV Stream Browser")
        self.header_back()

        items = self.config.history_items()
        total = len(items)
        pages = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(0, min(page, pages - 1))

        head = tk.Frame(self.body, bg=BG_SUNKEN)
        head.pack(fill=tk.X, padx=20, pady=(20, 6))
        title_row = tk.Frame(head, bg=BG_SUNKEN)
        title_row.pack(fill=tk.X)
        tk.Label(title_row, text="Recently watched", font=FONT_H2, fg=TEXT,
                 bg=BG_SUNKEN, anchor="w").pack(side=tk.LEFT)
        if total:
            ghost_button(title_row, "Clear history", self._clear_history).pack(side=tk.RIGHT)
        sub = f"{total:,} watched · page {page + 1} of {pages:,}" if total else "No history yet"
        tk.Label(head, text=sub, font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_SUNKEN,
                 anchor="w").pack(anchor="w", pady=(2, 0))

        if not total:
            empty = tk.Frame(self.body, bg=BG_SUNKEN)
            empty.pack(fill=tk.BOTH, expand=True, pady=60)
            tk.Label(empty, text="Nothing watched yet", font=FONT_H2, fg=TEXT_MUTED,
                     bg=BG_SUNKEN).pack()
            tk.Label(empty, text="Channels you play show up here so you can jump back.",
                     font=FONT_SMALL, fg=TEXT_FAINT, bg=BG_SUNKEN).pack(pady=(8, 0))
            self.apply_layout()
            return

        listing = tk.Frame(self.body, bg=BG_SUNKEN)
        listing.pack(fill=tk.BOTH, expand=True, padx=20, pady=(8, 0))
        for rec in items[page * self.PAGE_SIZE:(page + 1) * self.PAGE_SIZE]:
            self.create_channel_row(
                listing, rec, gen, show_group=True,
                on_remove=lambda r=rec: self._remove_history(r.get("url"), page))
        if self.live_cards:
            self.root.after(self.EPG_TICK_MS, lambda: self.epg_tick(gen, list(self.live_cards)))
        self.build_pager(self.body, page, lambda p: self.show_history(p), pages=pages)
        self.relayout_names()

    def _clear_history(self):
        self.config.clear_history()
        self.show_history()

    def _remove_history(self, url, page):
        self.config.remove_history(url)
        self.root.after(10, lambda: self.show_history(page))
