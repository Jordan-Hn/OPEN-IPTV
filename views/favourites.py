"""Favourites screen: the channels the user has starred."""

import tkinter as tk

from theme import BG_SUNKEN, FONT_H2, FONT_SMALL, TEXT, TEXT_FAINT, TEXT_MUTED


class FavouritesView:
    def show_favourites(self, page=0):
        gen = self.new_view()
        self.root.title("Favourites · IPTV Stream Browser")

        self.header_back()

        items = list(self.config.favourites.values())
        total = len(items)
        pages = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(0, min(page, pages - 1))

        head = tk.Frame(self.body, bg=BG_SUNKEN)
        head.pack(fill=tk.X, padx=20, pady=(20, 6))
        tk.Label(head, text="Favourites", font=FONT_H2, fg=TEXT, bg=BG_SUNKEN, anchor="w").pack(anchor="w")
        sub = f"{total:,} channels · page {page + 1} of {pages:,}" if total else "No favourites yet"
        tk.Label(head, text=sub, font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_SUNKEN, anchor="w").pack(anchor="w", pady=(2, 0))

        if not total:
            empty = tk.Frame(self.body, bg=BG_SUNKEN)
            empty.pack(fill=tk.BOTH, expand=True, pady=60)
            tk.Label(empty, text="No favourites yet", font=FONT_H2, fg=TEXT_MUTED, bg=BG_SUNKEN).pack()
            tk.Label(empty, text="Select the ☆ on any channel to add it here.",
                     font=FONT_SMALL, fg=TEXT_FAINT, bg=BG_SUNKEN).pack(pady=(8, 0))
            self.apply_layout()
            return

        listing = tk.Frame(self.body, bg=BG_SUNKEN)
        listing.pack(fill=tk.BOTH, expand=True, padx=20, pady=(8, 0))

        def refresh():
            self.root.after(10, lambda: self.show_favourites(page))

        for ch in items[page * self.PAGE_SIZE:(page + 1) * self.PAGE_SIZE]:
            self.create_channel_row(listing, ch, gen, show_group=True, on_fav_change=refresh)
        if self.live_cards:
            self.root.after(self.EPG_TICK_MS, lambda: self.epg_tick(gen, list(self.live_cards)))

        self.build_pager(self.body, page, lambda p: self.show_favourites(p), pages=pages)
        self.relayout_names()
