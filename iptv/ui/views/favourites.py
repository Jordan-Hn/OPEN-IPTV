"""Favourites screen: starred channels and starred series."""

import tkinter as tk

from iptv.ui.theme import BG_SUNKEN, FONT_H2, FONT_SMALL, TEXT, TEXT_FAINT, TEXT_MUTED


class FavouritesView:
    def show_favourites(self, page=0):
        gen = self.new_view()
        self.root.title("Favourites · IPTV Stream Browser")

        self.header_back()

        # Starred series come first, then starred channels, paged together.
        combined = ([("series", s) for s in self.config.favourite_series.values()]
                    + [("channel", c) for c in self.config.favourites.values()])
        total = len(combined)
        pages = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(0, min(page, pages - 1))

        head = tk.Frame(self.body, bg=BG_SUNKEN)
        head.pack(fill=tk.X, padx=20, pady=(20, 6))
        tk.Label(head, text="Favourites", font=FONT_H2, fg=TEXT, bg=BG_SUNKEN, anchor="w").pack(anchor="w")
        sub = f"{total:,} saved · page {page + 1} of {pages:,}" if total else "No favourites yet"
        tk.Label(head, text=sub, font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_SUNKEN, anchor="w").pack(anchor="w", pady=(2, 0))

        if not total:
            empty = tk.Frame(self.body, bg=BG_SUNKEN)
            empty.pack(fill=tk.BOTH, expand=True, pady=60)
            tk.Label(empty, text="No favourites yet", font=FONT_H2, fg=TEXT_MUTED, bg=BG_SUNKEN).pack()
            tk.Label(empty, text="Select the ☆ on any channel or series to add it here.",
                     font=FONT_SMALL, fg=TEXT_FAINT, bg=BG_SUNKEN).pack(pady=(8, 0))
            self.apply_layout()
            return

        listing = tk.Frame(self.body, bg=BG_SUNKEN)
        listing.pack(fill=tk.BOTH, expand=True, padx=20, pady=(8, 0))

        def refresh():
            self.root.after(10, lambda: self.show_favourites(page))

        for kind, rec in combined[page * self.PAGE_SIZE:(page + 1) * self.PAGE_SIZE]:
            if kind == "series":
                item = {"label": rec.get("name"), "grp": rec.get("grp", ""),
                        "logo": rec.get("logo", ""), "ep_count": rec.get("ep_count", 0)}
                self.create_series_row(listing, item, gen,
                                       on_back=lambda: self.show_favourites(page),
                                       on_fav_change=refresh)
            else:
                self.create_channel_row(listing, rec, gen, show_group=True, on_fav_change=refresh)
        if self.live_cards:
            self.root.after(self.EPG_TICK_MS, lambda: self.epg_tick(gen, list(self.live_cards)))

        self.build_pager(self.body, page, lambda p: self.show_favourites(p), pages=pages)
        self.relayout_names()
