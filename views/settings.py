"""Settings, first-run setup, and the catalog build/progress screen."""

import os
import tkinter as tk
from tkinter import filedialog, messagebox

import net
from theme import (BG, BG_SUNKEN, BORDER, FONT_BODY, FONT_CAPTION, FONT_CARD,
                   FONT_H2, FONT_SMALL, FONT_TITLE, SURFACE_2, TEXT, TEXT_FAINT,
                   TEXT_MUTED)
from widgets import MiniProgress, ghost_button, primary_button, settings_entry


def _download_to_file(url, dest, on_bytes=None):
    """Stream a (possibly large) URL to a local file."""
    got = 0
    with net.open_url(url, timeout=60) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(1_000_000)
            if not chunk:
                break
            out.write(chunk)
            got += len(chunk)
            if on_bytes:
                on_bytes(got)
    return got


class SettingsView:
    def show_build_screen(self, source, on_complete):
        """Build the catalog from the configured source (Xtream / M3U url / file)."""
        self.new_view()
        stype = source.get("type")
        self.subtitle_label.config(text="Preparing channels…")
        box = tk.Frame(self.body, bg=BG_SUNKEN)
        box.place(relx=0.5, rely=0.42, anchor="center")
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
            try:
                if stype == "xtream":
                    self.catalog.build_from_api(source["server"], source["username"],
                                                source["password"], progress=progress,
                                                status=status)
                elif stype == "m3u_url":
                    tmp = self.DB_PATH + ".m3u"
                    self.post(lambda: set_text("Downloading playlist…"))
                    _download_to_file(
                        source["m3u_url"], tmp,
                        on_bytes=lambda b: self.post(lambda mb=b // 1_000_000: set_text(f"Downloading… {mb} MB")))
                    self.catalog.build(tmp, progress=progress, source_type="m3u_url",
                                       source_key=source["m3u_url"],
                                       epg_url_override=source.get("epg_url"))
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                elif stype == "m3u_file":
                    path = source.get("m3u_file") or self.M3U_PATH
                    self.catalog.build(path, progress=progress, source_type="m3u_file",
                                       epg_url_override=source.get("epg_url"))
                return ("ok", None)
            except Exception as exc:
                return ("err", str(exc))

        def done(result):
            kind, msg = result
            if kind == "err":
                self.subtitle_label.config(text="Could not load channels")
                messagebox.showerror(
                    "Could not load channels",
                    f"Loading from your source failed:\n\n{msg}\n\n"
                    "Check the details in Settings and try again.")
                self.open_settings()
                return
            on_complete()

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
                new.update(m3u_url=entries["m3u_url"].get().strip(),
                           epg_url=entries["epg_url"].get().strip())
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
            self.set_scroll_target(self.canvas)
            win.destroy()
            self.show_build_screen(new, self.finish_startup)  # rebuild from the new source

        primary_button(btns, "Save and load", do_save, large=True).pack(side=tk.RIGHT)
        if not first_run:
            ghost_button(btns, "Cancel",
                         lambda: (self.set_scroll_target(self.canvas), win.destroy()), large=True).pack(side=tk.RIGHT, padx=(0, 10))
        win.protocol("WM_DELETE_WINDOW",
                     (lambda: None) if first_run else (lambda: (self.set_scroll_target(self.canvas), win.destroy())))

    def show_setup_screen(self):
        self.new_view()
        self.root.title("IPTV Stream Browser")
        self.subtitle_label.config(text="Welcome")
        for child in self.header_actions.winfo_children():
            child.destroy()
        box = tk.Frame(self.body, bg=BG_SUNKEN)
        box.place(relx=0.5, rely=0.4, anchor="center")
        tk.Label(box, text="Welcome to IPTV Stream Browser", font=FONT_TITLE,
                 fg=TEXT, bg=BG_SUNKEN).pack()
        tk.Label(box, text="Add your IPTV provider to get started.\n"
                 "An Xtream Codes login works best: live channels stay current and the guide loads.",
                 font=FONT_BODY, fg=TEXT_MUTED, bg=BG_SUNKEN, justify="center").pack(pady=(12, 24))
        primary_button(box, "Add your IPTV source", lambda: self.open_settings(first_run=True), large=True).pack()
        self.apply_layout()
