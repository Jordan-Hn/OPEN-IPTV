"""Search: matches channel names, and (on top) live channels broadcasting the
keyword right now per the guide."""

import tkinter as tk

from theme import (ACCENT, BG, BG_SUNKEN, BORDER, FONT_BODY, FONT_CAPTION,
                   FONT_H2, FONT_SMALL, LIVE, SURFACE_2, TEXT, TEXT_FAINT, TEXT_MUTED)
from widgets import ghost_button, primary_button


class SearchView:
    def open_search_window(self):
        win = tk.Toplevel(self.root)
        win.title("Search Channels")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.root)
        win.grab_set()
        w, h = 520, 220
        win.geometry(
            f"{w}x{h}+{(win.winfo_screenwidth() - w) // 2}+{(win.winfo_screenheight() - h) // 2}")

        frame = tk.Frame(win, bg=BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=28, pady=28)
        tk.Label(frame, text="Search Channels", font=FONT_H2, fg=TEXT, bg=BG).pack(anchor="w")
        tk.Label(frame, text="Find channels by name across your enabled categories.",
                 font=FONT_SMALL, fg=TEXT_MUTED, bg=BG).pack(anchor="w", pady=(4, 18))

        entry_wrap = tk.Frame(frame, bg=SURFACE_2, highlightthickness=1,
                              highlightbackground=BORDER, highlightcolor=ACCENT)
        entry_wrap.pack(fill=tk.X)
        entry = tk.Entry(entry_wrap, font=FONT_BODY, bg=SURFACE_2, fg=TEXT, relief="flat",
                         bd=0, insertbackground=ACCENT)
        entry.pack(fill=tk.X, ipady=9, padx=12, pady=2)
        entry.focus_set()

        def do_search(_=None):
            kw = entry.get().strip()
            if not kw:
                entry_wrap.config(highlightbackground=LIVE)
                win.after(250, lambda: entry_wrap.config(highlightbackground=BORDER))
                return
            win.destroy()
            self.show_search_results(kw, 0)

        entry.bind("<Return>", do_search)
        btns = tk.Frame(frame, bg=BG)
        btns.pack(anchor="e", pady=(20, 0))
        ghost_button(btns, "Cancel", win.destroy).pack(side=tk.LEFT, padx=(0, 10))
        primary_button(btns, "Search", do_search, large=True).pack(side=tk.LEFT)

    def show_search_results(self, keyword, page=0):
        gen = self.new_view()
        self.root.title(f"Search: {keyword}")

        self.header_back()

        head = tk.Frame(self.body, bg=BG_SUNKEN)
        head.pack(fill=tk.X, padx=20, pady=(20, 6))
        tk.Label(head, text=f"Results for “{keyword}”", font=FONT_H2, fg=TEXT,
                 bg=BG_SUNKEN, anchor="w").pack(anchor="w")
        count_lbl = tk.Label(head, text="Searching…", font=FONT_SMALL, fg=TEXT_MUTED,
                             bg=BG_SUNKEN, anchor="w")
        count_lbl.pack(anchor="w", pady=(2, 0))

        # "What's on" section sits above the name matches.
        whatson_section = tk.Frame(self.body, bg=BG_SUNKEN)
        whatson_section.pack(fill=tk.X, padx=20, pady=(8, 0))

        listing = tk.Frame(self.body, bg=BG_SUNKEN)
        listing.pack(fill=tk.BOTH, expand=True, padx=20, pady=(4, 0))

        disabled_list = sorted(self.config.disabled_groups)

        # Live channels actually broadcasting the keyword now/soon (page 0 only,
        # across all categories: a targeted content search, not browsing).
        if page == 0 and self.epg_guide and self.epg_ready:
            def whatson_work():
                matches = self.epg_guide.search_now(keyword)
                cmap = self.catalog.live_channels_by_tvg_ids([m[0] for m in matches], None)
                return matches, cmap

            def whatson_done(data):
                if gen != self.view_gen:
                    return
                matches, cmap = data
                seen, items = set(), []
                for cid, _start, _stop, _title in matches:
                    for ch in cmap.get(cid, []):
                        if ch["url"] not in seen:
                            seen.add(ch["url"])
                            items.append(ch)
                    if len(items) >= 80:
                        break
                if not items:
                    return
                tk.Label(whatson_section,
                         text=f"On air now or next, broadcasting “{keyword}” ({len(items)})",
                         font=FONT_H2, fg=TEXT, bg=BG_SUNKEN, anchor="w").pack(anchor="w", pady=(4, 2))
                tk.Label(whatson_section,
                         text="Live channels whose guide matches your search (all categories)",
                         font=FONT_CAPTION, fg=TEXT_FAINT, bg=BG_SUNKEN, anchor="w").pack(anchor="w", pady=(0, 6))
                for ch in items:
                    self.create_channel_row(whatson_section, ch, gen, show_group=True)
                tk.Label(whatson_section, text=f"Channels named “{keyword}”", font=FONT_H2,
                         fg=TEXT, bg=BG_SUNKEN, anchor="w").pack(anchor="w", pady=(20, 4))
                self.apply_layout()

            self.submit(whatson_work, whatson_done, gen)

        def render(result):
            if gen != self.view_gen:
                return
            rows, has_more = result
            if not rows:
                count_lbl.config(text="No channels match this name")
                empty = tk.Frame(listing, bg=BG_SUNKEN)
                empty.pack(fill=tk.X, pady=40)
                tk.Label(empty, text=f"No channels named “{keyword}”", font=FONT_H2,
                         fg=TEXT_MUTED, bg=BG_SUNKEN).pack()
                tk.Label(empty, text="Any live channels broadcasting it now appear above. "
                         "Also try re-enabling categories in Manage Categories.",
                         font=FONT_SMALL, fg=TEXT_FAINT, bg=BG_SUNKEN).pack(pady=(8, 0))
                return
            start = page * self.PAGE_SIZE
            count_lbl.config(text=f"Showing {start + 1:,} to {start + len(rows):,}")
            for ch in rows:
                self.create_channel_row(listing, ch, gen, show_group=True)
            if self.live_cards:
                # pass the live list (not a copy) so async "what's on" rows tick too
                self.root.after(self.EPG_TICK_MS, lambda: self.epg_tick(gen, self.live_cards))
            self.relayout_names()

            # pager driven by has_more (avoids a full COUNT scan up front)
            self.build_pager(self.body, page, lambda p: self.show_search_results(keyword, p),
                             has_more=has_more)

            # exact total computed lazily so the page renders instantly
            self.submit(lambda: self.catalog.search_count(keyword, disabled_list),
                        lambda total: count_lbl.config(
                            text=f"{total:,} channels found · showing {start + 1:,} to {start + len(rows):,}"),
                        gen)

        self.submit(lambda: self.catalog.search(keyword, disabled_list, page * self.PAGE_SIZE, self.PAGE_SIZE),
                    render, gen)
