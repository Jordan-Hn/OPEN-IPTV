"""
IPTV Stream Browser, application core.

`App` owns the window, the shared state (catalog, config, playback, EPG guide,
background runner, navigation), and the screens. The screen methods live in mixin
modules under ``views/`` and are composed onto `App` here, which keeps each screen
in its own small file while still sharing one object.
"""

from __future__ import annotations

import os
import queue
import sys
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import messagebox

import lock
import catalog as catalog_mod
from config_store import Config
from epg_guide import EpgGuide
from images import ImageStore
from player import Playback
from PIL import ImageTk

from theme import BG, FONT_SUB, FONT_SMALL, FONT_TITLE, TEXT, TEXT_MUTED, TEXT_FAINT, \
    ACCENT, ACCENT_INK, SURFACE, BG_SUNKEN, BORDER_SOFT
from widgets import ghost_button, make_placeholder
from views.groups import GroupsView
from views.channels import ChannelsView
from views.search import SearchView
from views.favourites import FavouritesView
from views.filters import FiltersView
from views.settings import SettingsView

# Channel names contain Arabic, emoji, etc. The default Windows console codec
# (cp1252) raises on those, which would otherwise crash click handlers that log.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# --------------------------------------------------------------------------- #
# Paths & constants
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# When packaged with PyInstaller, user data (config, caches) lives next to the
# executable, while bundled read-only resources (the icon) live in the temporary
# extraction dir. Running from source, both are the project folder.
if getattr(sys, "frozen", False):
    DATA_DIR = os.path.dirname(sys.executable)
    RESOURCE_DIR = getattr(sys, "_MEIPASS", DATA_DIR)
else:
    DATA_DIR = BASE_DIR
    RESOURCE_DIR = BASE_DIR

M3U_PATH = os.path.join(DATA_DIR, "IPTV.m3u")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
LOCK_FILE = os.path.join(DATA_DIR, "iptv_launcher.lock")
DB_PATH = os.path.join(DATA_DIR, "iptv_catalog.db")
EPG_DB_PATH = os.path.join(DATA_DIR, "iptv_epg.db")
LOGO_CACHE_DIR = os.path.join(DATA_DIR, ".cache", "logos")
ICON_ICO = os.path.join(RESOURCE_DIR, "assets", "icon.ico")
ICON_PNG = os.path.join(RESOURCE_DIR, "assets", "icon.png")

PAGE_SIZE = 50          # channels per page
QUEUE_PUMP_MS = 30      # how often the UI drains background results
EPG_TICK_MS = 20000     # how often visible EPG is refreshed
RESIZE_DEBOUNCE_MS = 140  # content reflows this long after a resize settles
WORKERS = 12


class App(GroupsView, ChannelsView, SearchView, FavouritesView, FiltersView, SettingsView):
    PAGE_SIZE = PAGE_SIZE
    EPG_TICK_MS = EPG_TICK_MS
    M3U_PATH = M3U_PATH
    DB_PATH = DB_PATH

    def __init__(self):
        # Shared services and state.
        self.config = Config(CONFIG_FILE)
        self.config.load()
        self.playback = Playback(self.config, DATA_DIR)
        if not self.playback.player_path:
            print("No media player found. Install VLC (videolan.org) or set 'player_path' in config.json.")
        self.catalog = catalog_mod.Catalog(DB_PATH)
        self.image_store = ImageStore(LOGO_CACHE_DIR)
        self.epg_guide: EpgGuide | None = None
        self.epg_ready = False
        self.epg_refreshing = False
        self.executor = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="iptv")
        self.ui_queue: "queue.Queue" = queue.Queue()

        # Navigation / per-view state.
        self.view_gen = 0
        self.view_images: list = []
        self.live_cards: list = []
        self.name_labels: list = []

        # Scroll / resize state.
        self._active_scroll = None
        self._relayout_cb = None
        self._resize_after = None
        self._applied_width = 0
        self._resize_hidden = False

        self._build_window()

    # -- window scaffold ---------------------------------------------------- #
    def _set_window_icon(self):
        """Set the title-bar / taskbar icon (best effort across platforms)."""
        try:
            if os.name == "nt" and os.path.exists(ICON_ICO):
                self.root.iconbitmap(ICON_ICO)
        except tk.TclError:
            pass
        try:
            if os.path.exists(ICON_PNG):
                self._icon_img = ImageTk.PhotoImage(file=ICON_PNG)  # keep a ref
                self.root.iconphoto(True, self._icon_img)
        except Exception:
            pass

    def _build_window(self):
        self.root = tk.Tk()
        self.root.title("IPTV Stream Browser")
        self.root.configure(bg=BG)
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        wcfg = self.config.get("window", {})
        default_w = max(1100, min(2000, int(screen_w * 0.78)))
        default_h = max(760, min(1400, int(screen_h * 0.80)))
        win_w = max(900, min(screen_w - 80, wcfg.get("width", default_w)))
        win_h = max(640, min(screen_h - 80, wcfg.get("height", default_h)))
        if "x" in wcfg and "y" in wcfg:
            win_x = max(0, min(screen_w - win_w, wcfg["x"]))
            win_y = max(0, min(screen_h - win_h, wcfg["y"]))
        else:
            win_x = (screen_w - win_w) // 2
            win_y = (screen_h - win_h) // 2
        self.root.geometry(f"{win_w}x{win_h}+{win_x}+{win_y}")
        self.root.minsize(900, 640)
        self._set_window_icon()

        self.placeholder_img = ImageTk.PhotoImage(make_placeholder())

        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(shell, bg=BG)
        header.pack(fill=tk.X, padx=28, pady=(22, 14))
        logo_mark = tk.Canvas(header, width=40, height=40, bg=BG, highlightthickness=0, bd=0)
        logo_mark.create_rectangle(0, 0, 40, 40, fill=ACCENT, width=0)
        logo_mark.create_polygon(15, 11, 15, 29, 30, 20, fill=ACCENT_INK)
        logo_mark.pack(side=tk.LEFT, padx=(0, 14))
        title_box = tk.Frame(header, bg=BG)
        title_box.pack(side=tk.LEFT)
        tk.Label(title_box, text="Stream Browser", font=FONT_TITLE, fg=TEXT, bg=BG).pack(anchor="w")
        self.subtitle_label = tk.Label(title_box, text="Loading…", font=FONT_SUB, fg=TEXT_MUTED, bg=BG)
        self.subtitle_label.pack(anchor="w")
        self.header_actions = tk.Frame(header, bg=BG)
        self.header_actions.pack(side=tk.RIGHT)

        content_wrap = tk.Frame(shell, bg=BORDER_SOFT)
        content_wrap.pack(fill=tk.BOTH, expand=True, padx=28, pady=(0, 0))
        self.canvas = tk.Canvas(content_wrap, bg=BG_SUNKEN, highlightthickness=0, bd=0)
        self.scrollbar = tk.Scrollbar(
            content_wrap, orient=tk.VERTICAL, command=self.canvas.yview,
            bg=SURFACE, troughcolor=BG_SUNKEN, width=12,
            activebackground=ACCENT, relief="flat", bd=0)
        self.body = tk.Frame(self.canvas, bg=BG_SUNKEN)
        self.body_window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        footer = tk.Frame(shell, bg=BG)
        footer.pack(fill=tk.X, padx=28, pady=(10, 14))
        self.status_label = tk.Label(footer, text="", font=("Segoe UI", 9), fg=TEXT_FAINT, bg=BG, anchor="w")
        self.status_label.pack(side=tk.LEFT)
        self.epg_status_label = tk.Label(footer, text="", font=("Segoe UI", 9), fg=TEXT_FAINT, bg=BG, anchor="e")
        self.epg_status_label.pack(side=tk.RIGHT)

        self._active_scroll = self.canvas
        self.root.bind_all("<MouseWheel>", self._on_wheel)
        self.root.bind_all("<Button-4>", self._on_wheel)
        self.root.bind_all("<Button-5>", self._on_wheel)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # -- background runner -------------------------------------------------- #
    def submit(self, work, on_done=None, gen="current"):
        """Run ``work()`` on a worker thread; deliver its result to ``on_done`` on
        the UI thread. Stale results (from a view already left) are dropped.
        Pass ``gen=None`` to always deliver regardless of navigation."""
        target_gen = self.view_gen if gen == "current" else gen

        def task():
            try:
                result, err = work(), None
            except Exception as exc:  # pragma: no cover - defensive
                result, err = None, exc
            self.ui_queue.put((target_gen, on_done, result, err))

        self.executor.submit(task)

    def post(self, setter):
        """Queue a no-argument callable to run on the UI thread, regardless of
        navigation. The pump invokes queued callbacks with the worker result, so
        wrap the setter to swallow it and call with no args."""
        self.ui_queue.put((None, lambda _result=None: setter(), None, None))

    def pump_queue(self):
        drained = 0
        try:
            while drained < 200:  # bound work per tick to keep UI responsive
                target_gen, cb, result, err = self.ui_queue.get_nowait()
                drained += 1
                if err is not None or cb is None:
                    continue
                if target_gen is not None and target_gen != self.view_gen:
                    continue
                try:
                    cb(result)
                except tk.TclError:
                    pass  # widget went away mid-update
                except Exception as exc:  # pragma: no cover - defensive
                    print(f"UI callback error: {exc}")
        except queue.Empty:
            pass
        self.root.after(QUEUE_PUMP_MS, self.pump_queue)

    # -- scrolling ---------------------------------------------------------- #
    def set_scroll_target(self, target):
        self._active_scroll = target

    def _on_wheel(self, event):
        target = self._active_scroll
        if target is None:
            return
        try:
            if event.num == 4:
                target.yview_scroll(-3, "units")
            elif event.num == 5:
                target.yview_scroll(3, "units")
            else:
                target.yview_scroll(int(-event.delta / 40), "units")
        except tk.TclError:
            pass

    # -- resize handling ---------------------------------------------------- #
    def set_relayout(self, cb):
        self._relayout_cb = cb

    def apply_layout(self):
        """Sync body width to the canvas, run the view's relayout, then reveal it."""
        try:
            self._applied_width = self.canvas.winfo_width()
            self.canvas.itemconfig(self.body_window, width=self._applied_width)
        except tk.TclError:
            return
        if self._relayout_cb:
            try:
                self._relayout_cb()
            except tk.TclError:
                pass
        if self._resize_hidden:
            try:
                self.canvas.itemconfigure(self.body_window, state="normal")
            except tk.TclError:
                pass
            self._resize_hidden = False

    def _on_canvas_configure(self, event):
        if event.width == self._applied_width:
            return  # height-only change: nothing to reflow
        # Hide the heavy scroll content while the window is actively resizing, so
        # each drag frame is cheap; apply_layout reveals it once the drag settles.
        if not self._resize_hidden:
            try:
                self.canvas.itemconfigure(self.body_window, state="hidden")
                self._resize_hidden = True
            except tk.TclError:
                pass
        if self._resize_after:
            self.canvas.after_cancel(self._resize_after)
        self._resize_after = self.canvas.after(RESIZE_DEBOUNCE_MS, self.apply_layout)

    # -- navigation --------------------------------------------------------- #
    def new_view(self):
        """Start a fresh view: invalidate background callbacks, clear the body."""
        self.view_gen += 1
        self.set_relayout(None)
        self.live_cards.clear()
        self.name_labels.clear()
        for w in self.body.winfo_children():
            w.destroy()
        self.view_images.clear()
        self.canvas.yview_moveto(0)
        return self.view_gen

    def header_back(self, label="‹ Categories"):
        """Clear the header and add a single back-to-categories button."""
        for w in self.header_actions.winfo_children():
            w.destroy()
        ghost_button(self.header_actions, label, self.show_groups).pack(side=tk.LEFT)

    def relayout_names(self):
        """Install a resize handler that re-wraps the current page's channel-name
        labels, then apply it. Shared by the channel, search, and favourites lists."""
        labels = list(self.name_labels)

        def relayout():
            wrap = max(260, self.root.winfo_width() - 360)
            for lbl in labels:
                try:
                    lbl.config(wraplength=wrap)
                except tk.TclError:
                    pass

        self.set_relayout(relayout)
        self.apply_layout()

    def build_pager(self, parent, page, go, pages=None, has_more=None):
        """Prev / Next pager. Pass ``pages`` when the total is known, or
        ``has_more`` when it isn't (search pages one screen at a time)."""
        bar = tk.Frame(parent, bg=BG_SUNKEN)
        bar.pack(fill=tk.X, padx=20, pady=18)
        center = tk.Frame(bar, bg=BG_SUNKEN)
        center.pack()
        prev_btn = ghost_button(center, "‹ Previous", lambda: go(page - 1))
        prev_btn.pack(side=tk.LEFT, padx=(0, 10))
        if page <= 0:
            prev_btn.config(state="disabled", fg=TEXT_FAINT, cursor="arrow")
        label = f"Page {page + 1:,} of {pages:,}" if pages else f"Page {page + 1:,}"
        tk.Label(center, text=label, font=FONT_SMALL, fg=TEXT_MUTED, bg=BG_SUNKEN).pack(side=tk.LEFT, padx=12)
        next_btn = ghost_button(center, "Next ›", lambda: go(page + 1))
        next_btn.pack(side=tk.LEFT, padx=(10, 0))
        at_end = (page >= pages - 1) if pages else (not has_more)
        if at_end:
            next_btn.config(state="disabled", fg=TEXT_FAINT, cursor="arrow")
        tk.Frame(parent, bg=BG_SUNKEN, height=16).pack()

    # -- EPG ---------------------------------------------------------------- #
    def init_epg(self):
        self.epg_ready = False
        # A user-set EPG url wins, otherwise the catalog's (M3U url-tvg or xmltv.php).
        src = self.config.get("source") or {}
        epg_url = (src.get("epg_url") or "").strip() or self.catalog.epg_url()
        if not epg_url:
            self.epg_guide = None
            self.epg_status_label.config(text="EPG: unavailable (no guide URL)")
            return
        self.epg_guide = EpgGuide(EPG_DB_PATH, epg_url=epg_url)
        self.epg_ready = self.epg_guide.programme_count() > 0  # a stale guide is still usable
        if self.epg_guide.is_fresh():
            self._set_epg_ready_status()
        else:
            self.epg_status_label.config(text="EPG: updating guide…")
            self.start_epg_refresh()

    def _set_epg_ready_status(self):
        if self.epg_guide:
            self.epg_status_label.config(
                text=f"EPG: guide for {self.epg_guide.channel_count():,} channels")

    def start_epg_refresh(self):
        """Download + index the XMLTV guide in the background, then fill in EPG."""
        if not self.epg_guide or self.epg_refreshing:
            return
        self.epg_refreshing = True

        def work():
            def progress(phase, value):
                if phase == "download":
                    msg = f"EPG: downloading guide… {value // 1_000_000} MB"
                else:
                    msg = f"EPG: indexing guide… {value:,} shows"
                self.post(lambda m=msg: self.epg_status_label.config(text=m))
            try:
                return ("ok", self.epg_guide.refresh(progress=progress))
            except Exception as exc:
                return ("err", str(exc))

        def done(result):
            self.epg_refreshing = False
            kind, _payload = result
            if kind == "ok":
                self.epg_ready = True
                self._set_epg_ready_status()
                self.refresh_visible_epg()
            elif self.epg_ready:
                self._set_epg_ready_status()  # keep using the stale-but-loaded guide
            else:
                self.epg_status_label.config(text="EPG: guide unavailable (provider unreachable)")

        self.submit(work, done, None)

    def schedule_epg_maintenance(self):
        """Hourly check that re-downloads the guide once it goes stale."""
        if self.epg_guide and not self.epg_refreshing and not self.epg_guide.is_fresh():
            self.start_epg_refresh()
        self.root.after(3_600_000, self.schedule_epg_maintenance)

    def refresh_visible_epg(self):
        """Apply now/next to every live row currently on screen (after a guide load)."""
        if not self.epg_guide:
            return
        for refs in list(self.live_cards):
            tvg = refs["channel"].get("tvg_id")
            if tvg and refs.get("now"):
                self.apply_epg(refs, self.epg_guide.now_next(tvg))

    # -- startup ------------------------------------------------------------ #
    def finish_startup(self, _=None):
        self.init_epg()
        self.submit(self.catalog.ensure_indexes, None, None)  # enable fast "what's on" search
        total = self.catalog.total_channels()
        self.status_label.config(text=f"{total:,} channels indexed")
        self.show_groups()
        self.root.after(3_600_000, self.schedule_epg_maintenance)

    def source_key_of(self, src):
        """A stable identifier for a source, used to decide when to rebuild."""
        if not src:
            return ""
        t = src.get("type")
        if t == "xtream":
            return f"{(src.get('server') or '').rstrip('/')}|{src.get('username', '')}"
        if t == "m3u_url":
            return src.get("m3u_url", "")
        if t == "m3u_file":
            path = src.get("m3u_file") or M3U_PATH
            try:
                return catalog_mod.fingerprint(path)
            except OSError:
                return ""
        return ""

    def source_is_usable(self, src):
        if not src:
            return False
        t = src.get("type")
        if t == "xtream":
            return bool(src.get("server") and src.get("username") and src.get("password"))
        if t == "m3u_url":
            return bool(src.get("m3u_url"))
        if t == "m3u_file":
            return bool(os.path.exists(src.get("m3u_file") or M3U_PATH))
        return False

    def resolve_source(self):
        """Return the active source, migrating older setups (an existing catalog or
        a local IPTV.m3u) into a saved ``source`` so new users get the setup screen."""
        src = self.config.get("source")
        if self.source_is_usable(src):
            return src
        prov = self.catalog.provider()
        if prov and prov.get("username"):
            src = {"type": "xtream", "server": prov["base"],
                   "username": prov["username"], "password": prov["password"]}
            self.config["source"] = src
            self.config.save()
            return src
        if os.path.exists(M3U_PATH):
            prov = catalog_mod.provider_from_m3u(M3U_PATH)
            src = ({"type": "xtream", "server": prov["base"], "username": prov["username"],
                    "password": prov["password"]} if prov
                   else {"type": "m3u_file", "m3u_file": M3U_PATH})
            self.config["source"] = src
            self.config.save()
            return src
        return None

    def start(self):
        src = self.resolve_source()
        if not src:
            self.show_setup_screen()
            return
        key = self.source_key_of(src)
        fresh = (self.catalog.source_key() == key if src["type"] == "m3u_file"
                 else self.catalog.is_fresh_for(key, 24))
        if fresh and self.catalog.total_channels() > 0:
            self.finish_startup()
        else:
            self.show_build_screen(src, self.finish_startup)

    def on_closing(self):
        try:
            self.config["window"] = {
                "width": self.root.winfo_width(),
                "height": self.root.winfo_height(),
                "x": self.root.winfo_x(),
                "y": self.root.winfo_y(),
            }
            self.config.save()
        except Exception as exc:
            print(f"Error saving window config: {exc}")
        try:
            self.executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
        except OSError:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        # Background worker threads are non-daemon and may be mid-request, so exit
        # now instead of waiting out their network timeouts. Keeps closing instant.
        os._exit(0)

    def run(self):
        self.pump_queue()
        self.root.after(50, self.start)
        self.root.mainloop()


def main():
    # Make Windows show the app's own taskbar icon instead of python.exe's.
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("openiptv.app")
        except Exception:
            pass
    if not lock.acquire(LOCK_FILE):
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showwarning(
                "IPTV Stream Browser",
                "IPTV Stream Browser is already running.\n\nOnly one instance can be open at a time.")
            r.destroy()
        except Exception:
            print("IPTV Stream Browser is already running!")
        sys.exit(1)
    App().run()


if __name__ == "__main__":
    main()
