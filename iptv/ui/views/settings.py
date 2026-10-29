"""Settings, first-run setup, and the catalog build/progress screen."""

import os
import tkinter as tk
from tkinter import filedialog, messagebox

from iptv.system import net
from iptv.ui.theme import (BG, BG_SUNKEN, BORDER, FONT_BODY, FONT_CAPTION, FONT_CARD,
                   FONT_H2, FONT_SMALL, FONT_TITLE, SURFACE_2, TEXT, TEXT_FAINT,
                   TEXT_MUTED)
from iptv.ui.widgets import MiniProgress, ghost_button, primary_button, settings_entry


DEFAULT_DOWNLOAD_TIMEOUT = 300


def _source_timeout(source, default=DEFAULT_DOWNLOAD_TIMEOUT):
    try:
        raw = (source or {}).get("download_timeout")
        timeout = default if raw in (None, "") else int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, timeout)


def _download_to_file(url, dest, timeout, on_bytes=None):
    """Stream a (possibly large) URL to a local file."""
    got = 0
    with net.open_url(url, timeout=timeout) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(1_000_000)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            if on_bytes:
                on_bytes(got)
    return got


def _source_changed(old, new):
    """True if the channel source (not the player or other settings) changed, so
    the catalog is rebuilt only when it actually needs to be."""
    old = old or {}
    if old.get("type") != new.get("type"):
        return True
    t = new.get("type")
    if t == "xtream":
        return (str(old.get("server", "")).rstrip("/") != str(new.get("server", "")).rstrip("/")
                or old.get("username") != new.get("username")
                or old.get("password") != new.get("password"))
    if t == "m3u_url":
        return old.get("m3u_url") != new.get("m3u_url")
    if t == "m3u_file":
        return old.get("m3u_file") != new.get("m3u_file")
    return True


class SettingsView:
    def _build_catalog(self, source, progress=None, status=None, include_vod=True):
        stype = source.get("type")

        def say(msg):
            if status:
                status(msg)

        try:
            if stype == "xtream":
                self.catalog.build_from_api(
                    source["server"], source["username"], source["password"],
                    progress=progress, status=status, include_vod=include_vod)
            elif stype == "m3u_url":
                tmp = self.DB_PATH + ".m3u"
                say("Downloading playlist...")
                _download_to_file(
                    source["m3u_url"], tmp, _source_timeout(source),
                    on_bytes=lambda b: say(f"Downloading... {b // 1_000_000} MB"))
                self.catalog.build(
                    tmp, progress=progress, source_type="m3u_url",
                    source_key=source["m3u_url"],
                    epg_url_override=source.get("epg_url"))
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            elif stype == "m3u_file":
                path = source.get("m3u_file") or self.M3U_PATH
                self.catalog.build(
                    path, progress=progress, source_type="m3u_file",
                    epg_url_override=source.get("epg_url"))
            else:
                raise ValueError(f"Unsupported source type: {stype}")
            return ("ok", None)
        except Exception as exc:
            return ("err", str(exc))

    def show_build_screen(self, source, on_complete, include_vod=None):
        """Build the catalog from the configured source (Xtream / M3U url / file).

        ``include_vod`` defaults to live-first for Xtream (usable fast, movies
        fill in behind the UI) and everything at once for M3U sources.
        """
        self.new_view()
        self.hide_search_row()  # nothing to search until the build finishes
        stype = source.get("type")
        if include_vod is None:
            include_vod = stype != "xtream"
        build_gen = self.view_gen
        self.subtitle_label.config(text="Preparing channels…")
        self.clear_header()
        box = tk.Frame(self.body, bg=BG_SUNKEN)
        box.pack(pady=(150, 40))  # packed (not placed) so it has real height and shows
        headline = {
            "xtream": "Loading channels from your provider",
            "m3u_url": "Downloading your playlist",
            "m3u_file": "Indexing your playlist",
        }.get(stype, "Loading channels")
        tk.Label(box, text=headline, font=FONT_H2, fg=TEXT, bg=BG_SUNKEN).pack()
        tk.Label(box, text="This refreshes so stream links stay current.",
                 font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_SUNKEN).pack(pady=(6, 22))
        bar_holder = tk.Frame(box, bg=BORDER, width=420, height=6)
        bar_holder.pack()
        bar_holder.pack_propagate(False)
        bar = MiniProgress(bar_holder, bg=BORDER, height=6, width=420)
        bar.pack(fill=tk.BOTH, expand=True)
        count_lbl = tk.Label(box, text="Starting…", font=FONT_SMALL, fg=TEXT_FAINT, bg=BG_SUNKEN)
        count_lbl.pack(pady=(16, 0))
        actions = tk.Frame(box, bg=BG_SUNKEN)
        actions.pack(pady=(18, 0))
        def change_source():
            try:
                bar.stop_pulse()
            except Exception:
                pass
            # Leave the build screen for a usable one, which also invalidates the
            # in-flight build (its callback is gated on this view generation).
            if self.catalog.total_channels() > 0:
                self.show_groups()
            else:
                self.show_setup_screen()
            self.open_settings()

        ghost_button(actions, "Change source", change_source, large=False).pack()
        self.apply_layout()
        # The channel lists arrive as a few big downloads, so a determinate bar
        # would sit still for long stretches. A pulse keeps it visibly working.
        bar.start_pulse()

        def set_text(msg):
            try:
                count_lbl.config(text=msg)
            except tk.TclError:
                pass

        def work():
            def progress(done):
                self.post(lambda d=done: set_text(f"{done:,} channels indexed"))

            def status(msg):
                self.post(lambda m=msg: set_text(m))
            return self._build_catalog(source, progress=progress, status=status,
                                       include_vod=include_vod)

        def done(result):
            try:
                bar.stop_pulse()
            except Exception:
                pass
            kind, msg = result
            if kind == "err":
                self.subtitle_label.config(text="Could not load channels")
                where = (source.get("server") or source.get("m3u_url")
                         or source.get("m3u_file") or "your source")
                messagebox.showerror(
                    "Could not load channels",
                    f"Could not load channels from {where}.\n\n"
                    f"Details: {msg}\n\n"
                    "Check that the address and any login are correct and that you "
                    "are online, then try again.")
                self.open_settings()
                return
            if not self.catalog.total_channels():
                self.subtitle_label.config(text="No channels found")
                messagebox.showwarning(
                    "No channels found",
                    "The source loaded but returned no channels. Double-check the "
                    "details (for Xtream, the server, username, and password).")
                self.open_settings()
                return
            on_complete()
            if stype == "xtream" and not include_vod:
                self.refresh_catalog_in_background(source, include_vod=True)

        self.submit(work, done, build_gen)

    def refresh_catalog_in_background(self, source, include_vod=True):
        """Refresh the catalog without blocking the current screen.

        Only one rebuild runs at a time. They all write to the same temporary DB
        and swap it into place at the end, so a second one started by an impatient
        second press of Refresh would race the first.
        """
        if self.catalog_refreshing:
            self.status_label.config(text="Refreshing channels… already running")
            return
        self.catalog_refreshing = True

        def work():
            def status(msg):
                self.post(lambda m=msg: self.status_label.config(text=f"Refreshing: {m}"))

            return self._build_catalog(source, status=status, include_vod=include_vod)

        def done(result):
            # Delivered on the UI thread by the queue pump, so touch widgets directly.
            self.catalog_refreshing = False
            kind, msg = result
            if kind != "ok":
                self.status_label.config(text="Catalog refresh failed")
                print(f"Catalog refresh failed: {msg}")
                return
            self.status_label.config(
                text=f"{self.catalog.total_channels():,} channels indexed (refreshed)")
            # Categories and their counts can have changed, so redraw the home
            # screen if that is where the user is. Any other screen is left alone
            # rather than yanked away mid-browse.
            if self.view_gen == self.groups_gen:
                self.show_groups()

        self.submit(work, done, None)

    def open_settings(self, first_run=False):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.configure(bg=BG)
        win.transient(self.root)
        win.grab_set()
        w, h = 640, 660
        win.geometry(
            f"{w}x{h}+{(win.winfo_screenwidth() - w) // 2}+{(win.winfo_screenheight() - h) // 2}")

        src = dict(self.config.get("source") or {})
        outer = tk.Frame(win, bg=BG)
        outer.pack(fill=tk.BOTH, expand=True, padx=26, pady=22)
        tk.Label(outer, text="Set up your IPTV source", font=FONT_H2, fg=TEXT, bg=BG).pack(anchor="w")
        tk.Label(outer, text="Pick how you connect, then enter your details.",
                 font=FONT_SMALL, fg=TEXT_MUTED, bg=BG).pack(anchor="w", pady=(4, 14))

        type_var = tk.StringVar(value=src.get("type") or "xtream")
        type_row = tk.Frame(outer, bg=BG)
        type_row.pack(fill=tk.X, pady=(0, 6))
        for val, lbl in (("xtream", "Xtream Codes login"),
                         ("m3u_url", "M3U URL"), ("m3u_file", "M3U file")):
            tk.Radiobutton(type_row, text=lbl, value=val, variable=type_var,
                           bg=BG, fg=TEXT, selectcolor=SURFACE_2, activebackground=BG,
                           activeforeground=TEXT, font=FONT_BODY, cursor="hand2",
                           highlightthickness=0, command=lambda: render_fields()).pack(side=tk.LEFT, padx=(0, 16))

        fields = tk.Frame(outer, bg=BG)
        fields.pack(fill=tk.X)
        entries = {}

        def render_fields():
            for child in fields.winfo_children():
                child.destroy()
            entries.clear()
            t = type_var.get()
            if t == "xtream":
                entries["server"] = settings_entry(fields, "Server URL (e.g. http://host:port)", src.get("server", ""))
                entries["username"] = settings_entry(fields, "Username", src.get("username", ""))
                entries["password"] = settings_entry(fields, "Password", src.get("password", ""), show="*")
            elif t == "m3u_url":
                entries["m3u_url"] = settings_entry(fields, "M3U playlist URL", src.get("m3u_url", ""))
                entries["epg_url"] = settings_entry(fields, "EPG XMLTV URL (optional)", src.get("epg_url", ""))
                entries["download_timeout"] = settings_entry(
                    fields, "Playlist download timeout (seconds)",
                    str(src.get("download_timeout") or DEFAULT_DOWNLOAD_TIMEOUT))
            else:
                def browse_m3u():
                    path = filedialog.askopenfilename(
                        parent=win, title="Select M3U playlist",
                        filetypes=[("Playlists", "*.m3u *.m3u8"), ("All files", "*.*")])
                    if path:
                        entries["m3u_file"].delete(0, tk.END)
                        entries["m3u_file"].insert(0, path)
                entries["m3u_file"] = settings_entry(fields, "M3U file path",
                                                     src.get("m3u_file") or self.M3U_PATH, browse=browse_m3u)
                entries["epg_url"] = settings_entry(fields, "EPG XMLTV URL (optional)", src.get("epg_url", ""))

        render_fields()

        tk.Frame(outer, bg=BORDER, height=1).pack(fill=tk.X, pady=(20, 0))
        tk.Label(outer, text="Media player", font=FONT_CARD, fg=TEXT, bg=BG).pack(anchor="w", pady=(14, 0))

        def browse_player():
            path = filedialog.askopenfilename(
                parent=win, title="Select media player (e.g. vlc.exe)",
                filetypes=[("Programs", "*.exe"), ("All files", "*.*")])
            if path:
                player_entry.delete(0, tk.END)
                player_entry.insert(0, path)

        player_entry = settings_entry(
            outer, "Player path. Leave empty to auto-detect VLC.",
            self.config.get("player_path") or "", browse=browse_player)
        detected = self.playback.find_player()
        tk.Label(outer, text=(f"Auto-detected: {detected}" if detected else "No player detected. Browse to your vlc.exe."),
                 font=FONT_CAPTION, fg=TEXT_FAINT, bg=BG, anchor="w").pack(anchor="w", pady=(4, 0))

        btns = tk.Frame(outer, bg=BG)
        btns.pack(fill=tk.X, pady=(22, 0))

        def do_save():
            t = type_var.get()
            new = {"type": t}
            if t == "xtream":
                server = entries["server"].get().strip().rstrip("/")
                if server and not server.startswith("http"):
                    server = "http://" + server
                new.update(server=server, username=entries["username"].get().strip(),
                           password=entries["password"].get().strip())
            elif t == "m3u_url":
                try:
                    timeout = int(entries["download_timeout"].get().strip() or DEFAULT_DOWNLOAD_TIMEOUT)
                except ValueError:
                    messagebox.showwarning("Invalid timeout",
                                           "Playlist download timeout must be a whole number.",
                                           parent=win)
                    return
                new.update(m3u_url=entries["m3u_url"].get().strip(),
                           epg_url=entries["epg_url"].get().strip(),
                           download_timeout=max(1, timeout))
            else:
                new.update(m3u_file=entries["m3u_file"].get().strip(),
                           epg_url=entries["epg_url"].get().strip())
            if not self.source_is_usable(new):
                messagebox.showwarning("Missing details",
                                       "Please fill in the required fields for this source.", parent=win)
                return
            self.config["source"] = new
            player = player_entry.get().strip()
            if player:
                self.config["player_path"] = player
            elif "player_path" in self.config:
                del self.config["player_path"]
            self.config.save()
            self.playback.refresh()
            close()
            # Only re-download when the channel source actually changed. Editing
            # just the player path (or other settings) should not rebuild.
            if _source_changed(src, new) or not self.catalog.total_channels():
                self.show_build_screen(new, self.finish_startup)
                return
            if (src.get("epg_url") or "") != (new.get("epg_url") or ""):
                self.init_epg()  # guide url changed: reload the guide, not the catalog
            self.show_groups()

        def close():
            self.set_scroll_target(self.canvas)
            win.destroy()

        primary_button(btns, "Save and load", do_save, large=True).pack(side=tk.RIGHT)
        if not first_run:
            ghost_button(btns, "Cancel", close, large=True).pack(side=tk.RIGHT, padx=(0, 10))
        win.protocol("WM_DELETE_WINDOW", (lambda: None) if first_run else close)

    def show_setup_screen(self):
        self.new_view()
        self.hide_search_row()
        self.root.title("IPTV Stream Browser")
        self.subtitle_label.config(text="Welcome")
        self.clear_header()
        box = tk.Frame(self.body, bg=BG_SUNKEN)
        box.pack(pady=(140, 40))  # packed (not placed) so it has real height and shows
        tk.Label(box, text="Welcome to IPTV Stream Browser", font=FONT_TITLE,
                 fg=TEXT, bg=BG_SUNKEN).pack()
        tk.Label(box, text="Add your IPTV provider to get started.\n"
                 "An Xtream Codes login works best: live channels stay current and the guide loads.",
                 font=FONT_BODY, fg=TEXT_MUTED, bg=BG_SUNKEN, justify="center").pack(pady=(12, 24))
        primary_button(box, "Add your IPTV source", lambda: self.open_settings(first_run=True), large=True).pack()
        self.apply_layout()
