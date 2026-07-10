"""Channel rows (shared by several screens) and the paginated channel list."""

import time
import tkinter as tk

from PIL import ImageTk

from iptv.ui.theme import (BG_SUNKEN, BORDER_SOFT, FONT_CAPTION, FONT_H2, FONT_ROW,
                   FONT_SMALL, FONT_PILL, INDIGO, LIVE, LOGO_SIZE, SURFACE_2,
                   TEXT, TEXT_FAINT, TEXT_MUTED)
from iptv.ui.widgets import (MiniProgress, bind_click, ghost_button, round_logo,
                   row_hover, star_button)


def _format_time(ts):
    return time.strftime("%H:%M", time.localtime(ts))


class ChannelsView:
    def create_channel_row(self, parent, channel, gen, show_group=False, on_fav_change=None,
                           on_remove=None):
        row = tk.Frame(parent, bg=SURFACE_2, highlightthickness=1,
                       highlightbackground=BORDER_SOFT, highlightcolor=BORDER_SOFT)
        row.pack(fill=tk.X, pady=4)
        inner = tk.Frame(row, bg=SURFACE_2)
        inner.pack(fill=tk.X, padx=14, pady=12)

        logo_lbl = tk.Label(inner, image=self.placeholder_img, bg=SURFACE_2)
        logo_lbl.pack(side=tk.LEFT, padx=(0, 14))

        mid = tk.Frame(inner, bg=SURFACE_2)
        mid.pack(side=tk.LEFT, fill=tk.X, expand=True)

        name_row = tk.Frame(mid, bg=SURFACE_2)
        name_row.pack(fill=tk.X)
        recolor = [row, inner, mid, logo_lbl, name_row]

        # LIVE vs VOD tag. An on-demand title (a recorded match, a movie) is not a
        # live feed, so VOD entries are labelled to avoid confusion.
        is_live = bool(channel.get("is_live"))
        if is_live:
            pill = tk.Label(name_row, text="● LIVE", font=FONT_PILL, fg=LIVE, bg=SURFACE_2)
        else:
            pill = tk.Label(name_row, text="VOD", font=FONT_PILL, fg=INDIGO, bg=SURFACE_2)
        pill.pack(side=tk.LEFT, padx=(0, 8))
        recolor.append(pill)

        name_lbl = tk.Label(name_row, text=channel["name"], font=FONT_ROW, fg=TEXT,
                            bg=SURFACE_2, anchor="w", justify="left",
                            wraplength=max(260, self.root.winfo_width() - 360))
        name_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        recolor.append(name_lbl)
        self.name_labels.append(name_lbl)

        play_glyph = tk.Label(inner, text="▶", font=("Segoe UI", 13), fg=TEXT_FAINT, bg=SURFACE_2)
        play_glyph.pack(side=tk.RIGHT, padx=(12, 4))
        recolor.append(play_glyph)

        # Favourite toggle (its own click handler, so it never triggers playback).
        def toggle_fav():
            fav = self.config.toggle_favourite(channel)
            if on_fav_change:
                on_fav_change()
            return fav

        star = star_button(inner, SURFACE_2,
                           lambda: self.config.is_favourite(channel.get("url", "")),
                           toggle_fav)
        star.pack(side=tk.RIGHT, padx=(8, 0))
        recolor.append(star)

        # Optional remove button (used by the history screen).
        if on_remove:
            def _remove(_=None):
                on_remove()
                return "break"
            remove_btn = tk.Label(inner, text="✕", font=("Segoe UI", 12), fg=TEXT_FAINT,
                                  bg=SURFACE_2, cursor="hand2")
            remove_btn.pack(side=tk.RIGHT, padx=(8, 0))
            recolor.append(remove_btn)
            remove_btn.bind("<Button-1>", _remove)
            remove_btn.bind("<Enter>", lambda e: remove_btn.config(fg=LIVE), add="+")
            remove_btn.bind("<Leave>", lambda e: remove_btn.config(fg=TEXT_FAINT), add="+")

        refs = {"channel": channel, "now": None, "progress": None, "next": None}

        if show_group and channel.get("grp"):
            cap = tk.Label(mid, text=channel["grp"], font=FONT_CAPTION, fg=INDIGO,
                           bg=SURFACE_2, anchor="w")
            cap.pack(fill=tk.X, pady=(2, 0))
            recolor.append(cap)

        tvg_id = channel.get("tvg_id")
        if is_live and self.epg_guide and self.epg_guide.enabled and tvg_id:
            now_lbl = tk.Label(mid, text="", font=FONT_SMALL, fg=TEXT_FAINT,
                               bg=SURFACE_2, anchor="w", justify="left")
            now_lbl.pack(fill=tk.X, pady=(4, 3))
            prog = MiniProgress(mid, bg=SURFACE_2)
            prog.pack(fill=tk.X)
            next_lbl = tk.Label(mid, text="", font=FONT_CAPTION, fg=TEXT_FAINT,
                                bg=SURFACE_2, anchor="w")
            next_lbl.pack(fill=tk.X, pady=(2, 0))
            recolor.extend([now_lbl, next_lbl])  # progress recolors via its own bg
            refs.update({"now": now_lbl, "progress": prog, "next": next_lbl})
            self.live_cards.append(refs)
            if self.epg_ready:
                self.apply_epg(refs, self.epg_guide.now_next(tvg_id))
            else:
                now_lbl.config(text="Guide loading…")

        def progress_bg(bg):
            if refs["progress"]:
                refs["progress"].config(bg=bg)

        on_enter, on_leave = row_hover(row, recolor, play_glyph, extras=progress_bg)
        clickable = [row, inner, mid, name_row, name_lbl, logo_lbl, play_glyph]
        for w in clickable:
            w.bind("<Enter>", on_enter, add="+")
            w.bind("<Leave>", on_leave, add="+")
        bind_click(clickable, lambda: self.play(channel))

        url = channel.get("logo")
        if url:
            self.submit(lambda u=url: self.image_store.get(u, LOGO_SIZE),
                        lambda img, lbl=logo_lbl: self.set_logo(lbl, img), gen)
        return refs

    def set_logo(self, label, pil_img):
        if pil_img is None:
            return
        try:
            photo = ImageTk.PhotoImage(round_logo(pil_img))
        except Exception:
            return
        self.view_images.append(photo)
        try:
            label.config(image=photo)
        except tk.TclError:
            pass

    def apply_epg(self, refs, now_next):
        now_lbl, prog, next_lbl = refs.get("now"), refs.get("progress"), refs.get("next")
        if not now_lbl:
            return
        current, nxt = (now_next or (None, None))
        try:
            if current:
                now_lbl.config(text=current.title or "On now", fg=TEXT)
                if prog:
                    prog.set_fraction(current.fraction())
                until = f"  ·  until {_format_time(current.stop)}" if current.stop else ""
                if nxt and next_lbl:
                    next_lbl.config(text=f"Next: {nxt.title}  ·  {_format_time(nxt.start)}{until}")
                elif next_lbl:
                    next_lbl.config(text=until.strip(" ·"))
            else:
                now_lbl.config(text="No guide data", fg=TEXT_FAINT)
                if prog:
                    prog.set_fraction(0.0)
                if next_lbl:
                    next_lbl.config(text="")
        except tk.TclError:
            pass

    def epg_tick(self, gen, cards):
        if gen != self.view_gen or not cards or not self.epg_guide:
            return
        # Recompute now/next from the local guide so the programme + progress bar
        # advance as time passes (no network, just an indexed DB lookup).
        for refs in cards:
            tvg = refs["channel"].get("tvg_id")
            if tvg:
                self.apply_epg(refs, self.epg_guide.now_next(tvg))
        self.root.after(self.EPG_TICK_MS, lambda: self.epg_tick(gen, cards))

    def create_series_row(self, parent, item, gen, on_back=None, on_fav_change=None):
        """A collapsed-series row: logo, SERIES tag, name, and episode count.
        Clicking opens the season/episode list."""
        row = tk.Frame(parent, bg=SURFACE_2, highlightthickness=1,
                       highlightbackground=BORDER_SOFT, highlightcolor=BORDER_SOFT)
        row.pack(fill=tk.X, pady=4)
        inner = tk.Frame(row, bg=SURFACE_2)
        inner.pack(fill=tk.X, padx=14, pady=12)

        logo_lbl = tk.Label(inner, image=self.placeholder_img, bg=SURFACE_2)
        logo_lbl.pack(side=tk.LEFT, padx=(0, 14))

        mid = tk.Frame(inner, bg=SURFACE_2)
        mid.pack(side=tk.LEFT, fill=tk.X, expand=True)
        name_row = tk.Frame(mid, bg=SURFACE_2)
        name_row.pack(fill=tk.X)
        recolor = [row, inner, mid, logo_lbl, name_row]

        pill = tk.Label(name_row, text="SERIES", font=FONT_PILL, fg=INDIGO, bg=SURFACE_2)
        pill.pack(side=tk.LEFT, padx=(0, 8))
        name_lbl = tk.Label(name_row, text=item["label"], font=FONT_ROW, fg=TEXT, bg=SURFACE_2,
                            anchor="w", justify="left",
                            wraplength=max(260, self.root.winfo_width() - 360))
        name_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.name_labels.append(name_lbl)
        recolor.extend([pill, name_lbl])

        chevron = tk.Label(inner, text="›", font=("Segoe UI", 15), fg=TEXT_FAINT, bg=SURFACE_2)
        chevron.pack(side=tk.RIGHT, padx=(12, 6))
        recolor.append(chevron)

        def toggle_fav():
            fav = self.config.toggle_favourite_series(
                {"name": item["label"], "grp": item.get("grp", ""),
                 "logo": item.get("logo", ""), "ep_count": item.get("ep_count", 0)})
            if on_fav_change:
                on_fav_change()
            return fav

        star = star_button(inner, SURFACE_2,
                           lambda: self.config.is_favourite_series(item["label"], item.get("grp", "")),
                           toggle_fav)
        star.pack(side=tk.RIGHT, padx=(8, 0))
        recolor.append(star)

        count = item.get("ep_count") or 0
        cap = tk.Label(mid, text=(f"{count:,} episodes" if count != 1 else "1 episode"),
                       font=FONT_CAPTION, fg=TEXT_MUTED, bg=SURFACE_2, anchor="w")
        cap.pack(fill=tk.X, pady=(2, 0))
        recolor.append(cap)

        on_enter, on_leave = row_hover(row, recolor, chevron)
        clickable = [row, inner, mid, name_row, name_lbl, logo_lbl, chevron, pill, cap]
        for w in clickable:
            w.bind("<Enter>", on_enter, add="+")
            w.bind("<Leave>", on_leave, add="+")
        bind_click(clickable, lambda: self.show_series(item["label"], item.get("grp"), on_back))

        url = item.get("logo")
        if url:
            self.submit(lambda u=url: self.image_store.get(u, LOGO_SIZE),
                        lambda img, lbl=logo_lbl: self.set_logo(lbl, img), gen)

    def show_series(self, series_name, group=None, on_back=None):
        """List a series' episodes in season / episode order, each playable."""
        gen = self.new_view()
        self.root.title(f"{series_name} · IPTV Stream Browser")
        self.clear_header()
        ghost_button(self.header_actions, "‹ Back", on_back or self.show_groups).pack(side=tk.LEFT)

        disabled = sorted(self.config.disabled_groups)
        head = tk.Frame(self.body, bg=BG_SUNKEN)
        head.pack(fill=tk.X, padx=20, pady=(20, 6))
        tk.Label(head, text=series_name, font=FONT_H2, fg=TEXT, bg=BG_SUNKEN, anchor="w").pack(anchor="w")
        sub = tk.Label(head, text="Loading episodes…", font=FONT_SMALL, fg=TEXT_MUTED,
                       bg=BG_SUNKEN, anchor="w")
        sub.pack(anchor="w", pady=(2, 0))

        listing = tk.Frame(self.body, bg=BG_SUNKEN)
        listing.pack(fill=tk.BOTH, expand=True, padx=20, pady=(8, 0))

        def render(episodes):
            if gen != self.view_gen:
                return
            sub.config(text=f"{len(episodes):,} episodes")
            for ep in episodes:
                self.create_channel_row(listing, ep, gen)
            self.relayout_names()

        self.submit(lambda: self.catalog.series_episodes(series_name, disabled, group), render, gen)

    def show_channels(self, group, page=0):
        gen = self.new_view()
        total = self.catalog.browse_count(group)
        pages = max(1, (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        page = max(0, min(page, pages - 1))
        self.root.title(f"{group} · IPTV Stream Browser")

        self.header_back()

        head = tk.Frame(self.body, bg=BG_SUNKEN)
        head.pack(fill=tk.X, padx=20, pady=(20, 6))
        tk.Label(head, text=group, font=FONT_H2, fg=TEXT, bg=BG_SUNKEN, anchor="w").pack(anchor="w")
        tk.Label(head, text=f"{total:,} items · page {page + 1} of {pages:,}",
                 font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_SUNKEN, anchor="w").pack(anchor="w", pady=(2, 0))

        listing = tk.Frame(self.body, bg=BG_SUNKEN)
        listing.pack(fill=tk.BOTH, expand=True, padx=20, pady=(8, 0))

        def render(items):
            if gen != self.view_gen:
                return
            for it in items:
                if it.get("kind") == "series":
                    self.create_series_row(listing, it, gen,
                                           on_back=lambda: self.show_channels(group, page))
                else:
                    self.create_channel_row(listing, it, gen)
            self.build_pager(self.body, page, lambda p: self.show_channels(group, p), pages=pages)
            if self.live_cards:
                self.root.after(self.EPG_TICK_MS, lambda: self.epg_tick(gen, list(self.live_cards)))
            self.relayout_names()

        self.submit(lambda: self.catalog.browse_items(group, page * self.PAGE_SIZE, self.PAGE_SIZE),
                    render, gen)
