#IMPORTS——————————————————————————————————————————————————————————————————————————————————————————————————
import tkinter as tk
from tkinter import scrolledtext, filedialog, messagebox
from pathlib import Path
from datetime import datetime
import threading
import queue
import sys
import shutil
import time
import traceback
import webbrowser

#IMPORT FROM MANAGER——————————————————————————————————————————————————————————————————————————————————————
from Manager import (
    load_config, save_config, get_input,
    folders_to_pdf, images_to_pdf, folder_renamer, file_renamer,
    combine_image_sets, image_converter, pdf_splitter, pdf_combiner,
    pdf_to_images, status, find_duplicates, DEFAULTS, SENTINEL, take_config_warning,
    settle_runs, recover_stale_runs
)

#IMPORT FROM LOG, PREFERENCES AND THEMES—————————————————————————————————————————————————————————————————
from Log import LogRedirect, input_queue, thread_safe_input, patch_input, EXPAND, COLLAPSE
from Preferences import show_preferences
from Themes import THEMES

#INPUT QUEUE——————————————————————————————————————————————————————————————————————————————————————————————


TOOL_LABELS = {
    "Folders to PDF", "Images to PDF", "Folder Renamer", "File Renamer",
    "Combine Image Sets", "Image Converter", "Find Duplicates",
    "PDF Combiner", "PDF Splitter", "PDF to Images",
}


#APP——————————————————————————————————————————————————————————————————————————————————————————————————
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Tankobon")
        self.root.resizable(True, True)
        self.config = load_config()
        self._migrate_legacy_theme_colors()
        self.cancel_event = threading.Event()
        self._jobs_lock = threading.Lock()          # _running_jobs is touched by several threads
        self._running_jobs = {}
        self._jobs_changed = threading.Event()      # set by worker threads, handled in _tick()
        self._main_calls = queue.SimpleQueue()      # things worker threads want the main thread to do
        self._pending_reply = None                  # the question currently shown in the log, if any
        self._input_frame = None
        self._tooltip = None
        self._help_win = None
        self._docs_win = None
        self._status_running = False
        self._closing = False
        self._open_accordion = {}
        self._dark = self.config.get("dark_mode", False)
        self._btn_labels = {}
        self._suboption_labels = {}
        self._last_input_count = -1
        self._last_count_check = 0.0
        self._ui_size = self._clamp_ui_size(self.config.get("ui_font_size", self.UI_BASE))
        self._mini_lbls = []                        # title-row buttons (resized together)
        self._moon_image = self._load_moon_icon()
        patch_input()
        self._build_ui()
        self._apply_theme()
        self._fit_sidebar()
        self._map_bind = self.root.bind("<Map>", self._on_first_map, add="+")
        # ONE shared log writer for stdout and stderr, so their lines stay in order.
        self._log_redirect = LogRedirect(self.log, self)
        sys.stdout = self._log_redirect
        sys.stderr = self._log_redirect
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        warning = take_config_warning()
        if warning:
            print(warning)
        try:
            recover_stale_runs(self.config)       # results left behind by a crash or force-quit
        except Exception:
            pass
        if not (self.config.get("input") and self.config.get("output")):
            print("Welcome! First set your Input and Output folders: click ≡ (top left), then Paths.")
        self._tick()
        if self.config.get("first_launch", True):
            self.config["first_launch"] = False
            save_config(self.config)
            self.root.after(500, self._show_help)

    TOOL_OPTIONS = {
        "Folders to PDF":     ["combine", "individual"],
        "Images to PDF":      ["combine", "reorder & combine"],
        "Folder Renamer":     ["prefix", "suffix", "replace", "extract number"],
        "File Renamer":       ["prefix", "suffix", "replace", "sequence"],
        "Combine Image Sets": [],
        "Image Converter":    ["jpg", "png", "webp", "bmp", "tiff"],
        "Find Duplicates":    ["keep one copy", "delete all"],
        "PDF Combiner":       [],
        "PDF Splitter":       [],
        "PDF to Images":      ["jpg", "png"],
        "Add Input":          ["files", "folder", "output"],
    }

    TOOL_MODE_CONFIG_KEY = {
        "Folders to PDF":  "default_folders_to_pdf_mode",
        "Folder Renamer":  "default_folder_renamer_mode",
        "File Renamer":    "default_file_renamer_mode",
        "Image Converter": "default_img_fmt",
        "Find Duplicates": "default_dedupe_mode",
        "PDF to Images":   "default_pdf_to_images_fmt",
        "Images to PDF":   "default_images_to_pdf_mode",

    }

    OPTION_LABELS = {
        "combine":        "Combine all → one PDF",
        "individual":     "One PDF per folder",
        "prefix":         "Prefix",
        "suffix":         "Suffix",
        "replace":        "Find & Replace",
        "extract number": "Extract Number",
        "sequence":       "Sequence",
        "jpg":            "JPG",
        "png":            "PNG",
        "webp":           "WebP",
        "bmp":            "BMP",
        "tiff":           "TIFF",
        "keep one copy":  "Keep one copy",
        "delete all":     "Delete all instances",
        "files":          "Files",
        "folder":         "Individual Folder",
        "output":         "From Output",
        "reorder & combine": "Reorder & Combine",
    }

    TOGGLEABLE = [
        ("show_folders_to_pdf", "Folder", "Folders to PDF",     "run_folders_to_pdf"),
        ("show_images_to_pdf",  "Folder", "Images to PDF",      "run_images_to_pdf"),
        ("show_folder_renamer", "Folder", "Folder Renamer",     "run_folder_renamer"),
        ("show_file_renamer",   "Folder", "File Renamer",       "run_file_renamer"),
        ("show_combine",        "Folder", "Combine Image Sets", "run_combine"),
        ("show_converter",      "Folder", "Image Converter",    "run_converter"),
        ("show_duplicates",     "Folder", "Find Duplicates",    "run_duplicates"),
        ("show_pdf_combiner",   "Folder", "PDF Combiner",       "run_pdf_combiner"),
        ("show_pdf_splitter",   "File",   "PDF Splitter",       "run_pdf_splitter"),
        ("show_pdf_to_images",  "File",   "PDF to Images",      "run_pdf_to_images"),
    ]

    TOOLTIPS = {
        "Folders to PDF":     "Turns the image folders in Input into PDFs: one combined PDF, or one PDF per folder.",
        "Images to PDF":      "Converts all images in Input into a single PDF.",
        "Folder Renamer":     "Renames folders: add a prefix or suffix, find & replace, or keep just the number (handy for sorting chapters). Renamed copies go to Output.",
        "File Renamer":       "Renames files by prefix, suffix, find/replace, or sequence numbering. Renamed copies go to Output.",
        "Combine Image Sets": "Merges multiple folders of images into one flat folder, preserving order.",
        "Image Converter":    "Converts all images in Input to a chosen format (jpg, png, webp, etc).",
        "Find Duplicates":    "Finds duplicate images (exact, or look-alike: set in Preferences > Tools) and copies the rest to Output. Nothing in Input is changed.",
        "PDF Combiner":       "Combines multiple PDFs into one.",
        "PDF Splitter":       "Splits a PDF into parts at page numbers you specify.",
        "PDF to Images":      "Converts a PDF into individual image files. Resource intensive.",
        "Add Input":          "Copies files or a folder into the Input directory for processing.\nZip (.zip / .cbz) files can be added as they are: every tool unpacks them automatically.",
        "Clear Input":        "Deletes everything in the Input folder. Your original files are not affected (Input only holds copies).\nTo be asked first, turn on Preferences > General > Confirm before Clearing.",
        "Status":             "Shows what is currently in the Input and Output folders.",
        "Clear Log":          "Clears the log display.",
        "Clear Output":       "Deletes everything in the Output folder.\nTo be asked first, turn on Preferences > General > Confirm before Clearing.",
        "Cancel Operation":   "Stops the running job at its next safe point. PDF writing itself cannot be interrupted.",
        "Open Input":         "Opens the input folder in Finder/Explorer.",
        "Open Output":        "Opens the output folder in Finder/Explorer and prints the path to the log.",
    }

    # ── sidebar text size ─────────────────────────────────────────────────────

    UI_BASE, UI_MIN, UI_MAX = 7, 6, 16      # UI_BASE is the size the app always used
    SIDEBAR_BASE_W = 200                    # sidebar width (px) at UI_BASE, before the -/+ buttons
    MIN_LOG_W = 400                         # the window grows rather than squeeze the log below this

    def _clamp_ui_size(self, v):
        try:
            return max(self.UI_MIN, min(self.UI_MAX, int(v)))
        except (TypeError, ValueError):
            return self.UI_BASE

    def _uf(self, delta=0, bold=False):
        """Font for sidebar text. delta is relative to the current sidebar size."""
        size = max(5, self._ui_size + delta)
        return ('', size, 'bold') if bold else ('', size)

    def _ensure_size(self):
        """Grow the window (never shrink it) so nothing in the sidebar is cut off and the log keeps room."""
        if not self.root.winfo_ismapped():
            return          # window size is not known yet; _on_first_map() calls this again
        # A maximized / fullscreen window already fills the screen. Calling geometry() on it shrinks it
        # and leaves a black, unpainted strip until the window is redrawn, so leave it completely alone.
        try:
            if self.root.state() == "zoomed":
                return
        except tk.TclError:
            pass
        try:
            if self.root.attributes("-fullscreen"):
                return
        except tk.TclError:
            pass
        try:
            self.root.update_idletasks()
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            cur_w, cur_h = self.root.winfo_width(), self.root.winfo_height()
            need_h = (sum(c.winfo_reqheight() for c in self._left.winfo_children()) + 2 + 4 + 20)
            need_w = int(self._left.cget("width")) + 30 + self.MIN_LOG_W
            # Only the SIZE WE WANT is capped to the screen; the current size is never reduced.
            new_w = max(cur_w, min(need_w, sw))
            new_h = max(cur_h, min(need_h, sh - 80))        # 80 ≈ taskbar / title bar
            if (new_w, new_h) != (cur_w, cur_h):
                self.root.geometry(f"{new_w}x{new_h}")
        except tk.TclError:
            pass

    def _on_first_map(self, event):
        if event.widget is self.root:
            self.root.unbind("<Map>", self._map_bind)
            self._ensure_size()

    def _fit_sidebar(self):
        """Set the sidebar width for the current text size. The -/+ buttons sit at the far right of the title
        row, so the sidebar is widened by exactly their width and every other button keeps its position."""
        self.root.update_idletasks()
        s = self._ui_size
        extra = sum(b.winfo_reqwidth() + 4 for b in self._size_btns)        # +4 = their padding
        width = max(round(self.SIDEBAR_BASE_W * s / self.UI_BASE),
                    self._btn_frame.winfo_reqwidth() + round(22 * s / self.UI_BASE)) + extra
        width = max(width, self._title_row.winfo_reqwidth() + 4)
        self._left.configure(width=width)
        self._ensure_size()

    def _size_dark_btn(self):
        """Give the moon/sun button one fixed hit area: the larger of the two pictures. Without this the moon
        (an image) and the sun (a text symbol) are different sizes, so the clickable area changed on every click."""
        lbl = self._dark_btn
        keep = (str(lbl.cget("image")), lbl.cget("text"))
        w = h = 0
        for image, text in ((self._moon_image or "", "" if self._moon_image else "🌙"), ("", "☀")):
            lbl.configure(image=image, text=text)
            lbl.update_idletasks()
            w, h = max(w, lbl.winfo_reqwidth()), max(h, lbl.winfo_reqheight())
        if keep[0] and self._moon_image and keep[0] == str(self._moon_image):
            lbl.configure(image=self._moon_image, text=keep[1])
            lbl.image = self._moon_image
        else:
            lbl.configure(image="", text=keep[1])
        lbl._box.configure(width=w, height=h)

    def _change_ui_font(self, delta):
        new = self._clamp_ui_size(self._ui_size + delta)
        if new == self._ui_size:
            return
        self._ui_size = new
        self.config["ui_font_size"] = new
        save_config(self.config)
        self._hide_tooltip()
        self._moon_image = self._load_moon_icon()
        self._title_lbl.configure(font=self._uf(1, bold=True))
        for lbl in self._mini_lbls:
            lbl.configure(font=self._uf())
        self._input_status_lbl.configure(font=('Courier', new))
        if not self._dark and self._moon_image:
            self._dark_btn.configure(image=self._moon_image, text="")
            self._dark_btn.image = self._moon_image
        self._size_dark_btn()
        self._rebuild_buttons()
        self._fit_sidebar()

    # ── theme ─────────────────────────────────────────────────────────────────

    def _theme(self):
        base = "dark" if self._dark else "light"
        hardcoded = THEMES[base]
        custom = self.config.get("themes", {}).get(base, {})
        return {**hardcoded, **custom}

    def _migrate_legacy_theme_colors(self):
        """Older versions saved EVERY colour into config.json the first time Preferences was saved,
        which froze the old default colours in place. Drop saved values that are identical to a built-in
        default (old or new) so the current defaults apply. Real customisations are kept."""
        themes = self.config.get("themes")
        if not isinstance(themes, dict):
            return
        changed = False
        for mode, saved in themes.items():
            if mode not in THEMES or not isinstance(saved, dict):
                continue
            for key in list(saved):
                val = str(saved[key]).lower()
                if val == THEMES[mode].get(key, "").lower():
                    del saved[key]
                    changed = True
        if changed:
            save_config(self.config)

    def _load_moon_icon(self):
        try:
            from PIL import Image, ImageTk
            assets = Path(__file__).parent / "assets"
            px = max(8, round(14 * self._ui_size / self.UI_BASE))
            img = Image.open(assets / "moon.png").resize((px, px), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _apply_theme(self):
        t = self._theme()
        self.root.configure(bg=t["bg"])
        for widget in [self.root, self._btn_frame, self._right]:
            self._apply_to_widget(widget, t)
        self.log.configure(bg=t["log_bg"], fg=t["log_fg"],
                           insertbackground=t["log_fg"])
        for tag in self.log.tag_names():
            if tag.startswith("section_"):
                self.log.tag_configure(tag, foreground=t["log_fg"])
            elif tag in ("ts_dim",) or tag.startswith(("log_dim", "preview_")):
                self.log.tag_configure(tag, foreground=t["log_dim"])
            elif tag.startswith("log_error"):
                self.log.tag_configure(tag, foreground=t["log_error"])
            elif tag.startswith("log_warn"):
                self.log.tag_configure(tag, foreground=t["log_warn"])
            elif tag.startswith("log_success"):
                self.log.tag_configure(tag, foreground=t["log_success"])
        self._update_button_states()

    def _apply_to_widget(self, widget, t):
        if isinstance(widget, tk.Toplevel):
            return
        cls = widget.winfo_class()
        try:
            if cls in ("Frame",):
                widget.configure(bg=t["bg"])
            elif cls in ("Label",):
                widget.configure(bg=t["bg"], fg=t["fg"])
            elif cls in ("Button",):
                widget.configure(bg=t["btn_bg"], fg=t["btn_fg"],
                                 activebackground=t["bg"], activeforeground=t["fg"])
            elif cls in ("Entry",):
                widget.configure(bg=t["entry_bg"], fg=t["entry_fg"],
                                 insertbackground=t["entry_fg"])
            elif cls in ("Text", "ScrolledText"):
                widget.configure(bg=t["log_bg"], fg=t["log_fg"])
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._apply_to_widget(child, t)

    def _toggle_dark(self):
        self._dark = not self._dark
        self.config["dark_mode"] = self._dark
        save_config(self.config)
        self._apply_theme()
        if self._dark:
            self._dark_btn.configure(image="", text="☀")
        else:
            if self._moon_image:
                self._dark_btn.configure(image=self._moon_image, text="")
                self._dark_btn.image = self._moon_image
            else:
                self._dark_btn.configure(image="", text="🌙")
        new_tip = "Bright Mode" if self._dark else "Dark Mode"
        self._dark_btn.bind("<Enter>", lambda e: (
            self._dark_btn.configure(bg=self._theme()["hover"]),
            self._show_tooltip_popup(self._dark_btn, new_tip)
        ))

    # ── input state ───────────────────────────────────────────────────────────

    def _get_input_count(self):
        try:
            if not self.config.get("input"):
                return 0
            input_dir = get_input(self.config)
            if not input_dir.exists():
                return 0
            return sum(1 for _ in input_dir.iterdir())
        except Exception:
            return 0

    def _update_button_states(self):
        t = self._theme()

        if not self.config.get("guide_empty_input", True):
            for label, lbl in self._btn_labels.items():
                try:
                    lbl.configure(fg=t["fg"], bg=t["bg"])
                except tk.TclError:
                    pass
            return

        count = self._get_input_count()
        empty = count == 0

        for label, lbl in self._btn_labels.items():
            try:
                bare = label.replace("⚠ ", "")
                if bare == "Add Input":
                    lbl.configure(fg=t["fg"], bg=t["hover"] if empty else t["bg"])
                elif bare in TOOL_LABELS:
                    lbl.configure(fg=t["hint_fg"] if empty else t["fg"], bg=t["bg"])
            except tk.TclError:
                pass

        for tool_label, opt_lbls in self._suboption_labels.items():
            color = t["hint_fg"] if (empty and tool_label != "Add Input") else t["fg"]
            for lbl in opt_lbls:
                try:
                    lbl.configure(fg=color)
                except tk.TclError:
                    pass

    def _refresh_input_count(self):
        count = self._get_input_count()
        if count != self._last_input_count:
            self._last_input_count = count
            label = f"[{count} item{'s' if count != 1 else ''}]"
            try:
                self._input_status_lbl.configure(text=label)
            except tk.TclError:
                pass
            self._update_button_states()

    def _call_in_main(self, fn):
        """Worker threads use this to ask the main thread to run something (e.g. open a file dialog)."""
        self._main_calls.put(fn)

    def _tick(self):
        """Runs about 40 times a second on the main thread. Everything worker threads want drawn or asked
        happens here, because Tkinter must only be used from the main thread."""
        if self._closing:
            return
        try:
            self._log_redirect.drain()

            while True:
                try:
                    call = self._main_calls.get_nowait()
                except queue.Empty:
                    break
                try:
                    call()
                except Exception:
                    traceback.print_exc()

            if self._pending_reply is None:          # only ONE question on screen at a time
                try:
                    prompt, reply = input_queue.get_nowait()
                except queue.Empty:
                    pass
                else:
                    try:
                        self._show_inline_input(prompt, reply)
                    except Exception:
                        traceback.print_exc()
                        self._pending_reply = None
                        reply.put(SENTINEL)          # never leave a job waiting forever

            if self._jobs_changed.is_set():
                self._jobs_changed.clear()
                self._update_status_label()
                self._last_count_check = 0.0         # re-count Input right after a job ends

            now = time.monotonic()
            if now - self._last_count_check >= 1.0:  # looking at the disk once a second is plenty
                self._last_count_check = now
                self._refresh_input_count()
        except tk.TclError:
            pass                                      # window is being destroyed
        if not self._closing:
            self.root.after(25, self._tick)

    def _on_close(self):
        self._closing = True
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
        self.root.destroy()

    # ── inline input ──────────────────────────────────────────────────────────

    def _show_inline_input(self, prompt, reply):
        t = self._theme()
        self._pending_reply = reply
        self._input_frame = tk.Frame(self._right, bg=t["bg"])
        self._input_frame.pack(fill='x', pady=(4, 0))

        if prompt.strip():
            print(f"{prompt.strip()}")

        is_pick = "Waiting for key" in prompt

        if is_pick:
            hint = "  (Enter = Files  •  Space = Folder  •  Escape = Cancel)"
        else:
            continue_key = self.config.get("hotkey_continue", "Return")
            cancel_key   = self.config.get("hotkey_cancel", "Escape")
            hint = f"  ({continue_key} = confirm  •  {cancel_key} = cancel)"

        tk.Label(self._input_frame, text=hint, fg=t["hint_fg"],
                 bg=t["bg"], font=('Courier', 7)).pack(side='left')

        var   = tk.StringVar()
        entry = tk.Entry(self._input_frame, textvariable=var, font=('Courier', 11),
                         bg=t["entry_bg"], fg=t["entry_fg"],
                         insertbackground=t["entry_fg"],
                         relief='flat', highlightthickness=1,
                         highlightbackground=t["entry_bg"], highlightcolor=t["entry_bg"])
        entry.pack(side='left', fill='x', expand=True, padx=4)
        entry.focus_set()

        # Every handler returns "break" so the key press is not ALSO typed into the box.
        if is_pick:
            def pick_files(e=None):
                self._finish_input("FILES")
                return "break"
            def pick_folder(e=None):
                self._finish_input("FOLDER")
                return "break"
            def cancel(e=None):
                self._finish_input(SENTINEL)
                return "break"
            entry.bind("<Return>", pick_files)
            entry.bind("<space>",  pick_folder)
            entry.bind("<Escape>", cancel)
        else:
            def confirm(e=None):
                self._finish_input(var.get())
                return "break"

            def cancel(e=None):
                self._finish_input(SENTINEL)
                return "break"

            entry.bind(f"<{continue_key}>", confirm)
            entry.bind(f"<{cancel_key}>",   cancel)

    def _finish_input(self, value):
        """Close the question box and hand the answer to the job that asked."""
        reply, self._pending_reply = self._pending_reply, None
        if self._input_frame is not None:
            try:
                self._input_frame.destroy()
            except tk.TclError:
                pass
            self._input_frame = None
        if reply is not None:
            reply.put(value)

    # ── help / docs ───────────────────────────────────────────────────────────

    def _show_help(self):
        if self._help_win is not None and self._help_win.winfo_exists():
            self._help_win.lift()
            self._help_win.focus_force()
            return
        win = tk.Toplevel(self.root)
        self._help_win = win
        win.title("Help")
        win.geometry("600x620")
        win.resizable(False, False)

        text = scrolledtext.ScrolledText(win, wrap='word', font=('Courier', 11),
                                         padx=10, pady=10)
        text.pack(fill='both', expand=True)

        text.tag_configure("h1",   font=('Courier', 13, 'bold'))
        text.tag_configure("h2",   font=('Courier', 11, 'bold'))
        text.tag_configure("body", font=('Courier', 10))
        text.tag_configure("dim",  font=('Courier', 9), foreground="gray")

        def h1(s):   text.insert(tk.END, s + "\n", "h1")
        def h2(s):   text.insert(tk.END, s + "\n", "h2")
        def body(s): text.insert(tk.END, s + "\n", "body")
        def gap():   text.insert(tk.END, "\n")

        h1("Tankobon")
        h2("Controls")
        gap()
        body("☀ to switch between light and dark mode")
        body("? for help")
        body("i for documentation and resources")
        body("≡ for settings")
        gap()
        body("For Information on specific tools or preferences, hover over the 'i' tooltips")
        gap()
        h2("Setup")
        body("When you first begin the program, please set the directories for the Input and Output within your file system and give the application permissions to read/write files there for actual functionality")
        gap()
        h2("Intro")
        body("Tankobon is a file manager.")
        body("To be more specific, it's specialized for image management en masse. It's meant to manage, convert, and compress folders with images or individual images in the thousands at a time and to do this with speed.")
        gap()
        body("And with this comes its true Niche or intended use. Ultimately, Tankobon is a companion to large scale Manga Piracy. To those who wish to own and obtain manga from third party sources you may find that a multitude of reasons can impede time-efficient management of what could be thousands of manga pages, each stored as an individual image.")
        gap()
        h1("Example Workflow")
        gap()
        text.insert(tk.END, "1. ", "h2")
        body("Click Add Input and add your files, folder or zip files.")
        text.insert(tk.END, "2. ", "h2")
        body("Select a tool and run it.")
        text.insert(tk.END, "3. ", "h2")
        body("Take the output from the output folder.")
        text.insert(tk.END, "4. ", "h2")
        body("Clear input when done.")
        gap()

        text.configure(state='disabled')

    def _show_docs(self):
        if self._docs_win is not None and self._docs_win.winfo_exists():
            self._docs_win.lift()
            self._docs_win.focus_force()
            return
        win = tk.Toplevel(self.root)
        self._docs_win = win
        win.title("Documentation")
        win.geometry("600x620")
        win.resizable(False, False)

        text = scrolledtext.ScrolledText(win, wrap='word', font=('Courier', 8),
                                         padx=10, pady=10)
        text.pack(fill='both', expand=True)

        def insert_link(url):
            tag = f"link_{url}"
            text.tag_configure(tag, foreground="blue", underline=True)
            text.insert(tk.END, url, (tag,))
            text.tag_bind(tag, "<Button-1>", lambda e, u=url: webbrowser.open(u))
            text.tag_bind(tag, "<Enter>", lambda e: text.configure(cursor="hand2"))
            text.tag_bind(tag, "<Leave>", lambda e: text.configure(cursor=""))

        text.configure(state='normal')
        text.insert(tk.END, "Tankobon\n\n")
        text.insert(tk.END,
                    "Tankobon is a file manager. It's an open source project that specializes in managing image files in bulk. It's under the AGPL license.\n\n")
        text.insert(tk.END, "Github:\n")
        insert_link("https://github.com/siyoungpark18-oss/Tankobon")
        text.insert(tk.END, "\n\nMacOS .dmg:\n")
        insert_link("https://drive.google.com/drive/u/2/folders/1gjRlr2hV7RjLBTGlGs2SqQgKNaW4T0Il")
        text.insert(tk.END, "\n\nPrevious versions:\n")
        insert_link("https://drive.google.com/drive/u/2/folders/1jT_qMHEpWVczcIwBHTYJt1QkrE6WL8pb")
        text.insert(tk.END, "\n")
        text.configure(state='disabled')


    # ── tooltips ──────────────────────────────────────────────────────────────

    def _hide_tooltip(self):
        if self._tooltip is not None:
            try:
                self._tooltip.destroy()
            except tk.TclError:
                pass
            self._tooltip = None

    def _place_tooltip(self, widget, text, wrap=0, below=False, force_light=False):
        """Show a tooltip next to `widget`, always fully inside the window."""
        self._hide_tooltip()
        t = THEMES["light"] if force_light else self._theme()
        tip = tk.Label(self.root, text=text, wraplength=wrap, justify='left',
                       font=('Courier', self._ui_size), bg=t["bg"], fg=t["fg"], padx=6, pady=4)
        tip.update_idletasks()
        x = widget.winfo_rootx() - self.root.winfo_rootx()
        y = widget.winfo_rooty() - self.root.winfo_rooty()
        if below:
            y += widget.winfo_height() + 2
        else:
            x += widget.winfo_width() + 4
        x = max(0, min(x, self.root.winfo_width()  - tip.winfo_reqwidth()  - 4))
        y = max(0, min(y, self.root.winfo_height() - tip.winfo_reqheight() - 4))
        tip.place(x=x, y=y)
        tip.lift()
        self._tooltip = tip

    def _show_tooltip(self, widget, text):
        # add="+" keeps the hover highlight that was bound to the same events before.
        widget.bind("<Enter>", lambda e: self._place_tooltip(widget, text, wrap=round(220 * self._ui_size / self.UI_BASE)), add="+")
        widget.bind("<Leave>", lambda e: self._hide_tooltip(), add="+")

    def _show_tooltip_popup(self, widget, text, force_light=False, below=False):
        self._place_tooltip(widget, text, below=below, force_light=force_light)

    def _tool_tip_text(self, label, has_default):
        text = self.TOOLTIPS[label]
        if has_default:
            mode = self.config.get(self.TOOL_MODE_CONFIG_KEY[label])
            text += (f"\n\nRuns with your default: {self.OPTION_LABELS.get(mode, mode)}."
                     f"\nChange it in Preferences (≡) > Tools.")
        return text

    # ── ui build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._log_font_size = self.config.get("log_font_size", 8)

        left = tk.Frame(self.root, width=self.SIDEBAR_BASE_W)
        left.pack(side='left', fill='y', padx=10, pady=10)
        left.pack_propagate(False)
        self._left = left

        title_row = tk.Frame(left)
        title_row.pack(fill='x', pady=(0, 2))
        self._title_row = title_row
        self._title_lbl = tk.Label(title_row, text="Tankobon", font=self._uf(1, bold=True))
        self._title_lbl.pack(side='left')

        def _mini_btn(parent, text_or_var, cmd, side='right', tooltip=None, boxed=False):
            t = self._theme()
            # boxed: the label sits in a fixed-size frame and fills it, so the clickable / highlighted
            # area stays the same whatever is drawn inside (used by the moon/sun button, see _size_dark_btn).
            box = None
            if boxed:
                box = tk.Frame(parent, bg=t["bg"])
                box.pack_propagate(False)
            lbl = tk.Label(box or parent, bg=t["bg"], fg=t["fg"], font=self._uf(), width=0, cursor="hand2")
            if isinstance(text_or_var, str):
                lbl.configure(text=text_or_var)
            self._mini_lbls.append(lbl)

            _clicked = {"v": False}

            def on_click(e, _clicked=_clicked):
                if _clicked["v"]:
                    return "break"
                _clicked["v"] = True
                self.root.after(100, lambda: _clicked.update({"v": False}))
                cmd()
                return "break"

            lbl.bind("<Button-1>", on_click)
            lbl.bind("<Enter>", lambda e: lbl.configure(bg=self._theme()["hover"]))
            lbl.bind("<Leave>", lambda e: lbl.configure(bg=self._theme()["bg"]))

            if tooltip:
                def show_tip(e):
                    self._show_tooltip_popup(lbl, tooltip)
                def hide_tip(e):
                    if hasattr(self, '_tooltip') and self._tooltip:
                        self._tooltip.destroy()
                        self._tooltip = None
                lbl.bind("<Enter>", lambda e: (lbl.configure(bg=self._theme()["hover"]), show_tip(e)))
                lbl.bind("<Leave>", lambda e: (lbl.configure(bg=self._theme()["bg"]), hide_tip(e)))

            if box is not None:
                lbl.pack(fill='both', expand=True)
                box.pack(side=side, padx=(3, 1), pady=1)
                lbl._box = box
            else:
                lbl.pack(side=side, padx=(3, 1), pady=1)
            return lbl

        # Packed first = furthest right. Everything packed after keeps its place relative to these.
        self._size_btns = [
            _mini_btn(title_row, "+", lambda: self._change_ui_font(+1), tooltip="Larger sidebar text"),
            _mini_btn(title_row, "−", lambda: self._change_ui_font(-1), tooltip="Smaller sidebar text"),
        ]
        _mini_btn(title_row, "?", self._show_help,        tooltip="Help")
        _mini_btn(title_row, "i", self._show_docs,        tooltip="Documentation")
        _mini_btn(title_row, "≡", self._show_preferences, tooltip="Preferences")
        moon_icon = self._moon_image if (self._moon_image and not self._dark) else ("☀" if self._dark else "🌙")
        self._dark_btn = _mini_btn(title_row, moon_icon, self._toggle_dark, boxed=True,
                                   tooltip="Dark Mode" if not self._dark else "Bright Mode")
        if self._moon_image and not self._dark:
            self._dark_btn.configure(image=self._moon_image, text="")
            self._dark_btn.image = self._moon_image
        self._size_dark_btn()

        status_row = tk.Frame(left)
        status_row.pack(fill='x', pady=(0, 4))
        self._status_row = status_row
        t = self._theme()
        self._input_status_lbl = tk.Label(
            status_row, text="[0 items]",
            font=('Courier', self._ui_size), fg=t["hint_fg"], bg=t["bg"], anchor='w'
        )
        self._input_status_lbl.pack(side='left')

        self._btn_frame = tk.Frame(left)
        self._btn_frame.pack(fill='both', expand=True)

        self._right = tk.Frame(self.root)
        self._right.pack(side='left', fill='both', expand=True, padx=(0, 10), pady=10)


        log_header = tk.Frame(self._right)
        log_header.pack(fill='x')

        tk.Label(log_header, text="Log", font=('', 8, 'bold')).pack(side='left')
        self._status_lbl = tk.Label(log_header, text="", font=('Courier', 9))
        self._status_lbl.pack(side='left', padx=(8, 0))

        def _change_font(delta):
            self._log_font_size = max(7, min(24, self._log_font_size + delta))
            self.log.configure(font=('Courier', self._log_font_size))
            self.config["log_font_size"] = self._log_font_size
            save_config(self.config)

        for symbol, delta, tip in (('+', 1, "Larger log text"), ('-', -1, "Smaller log text")):
            btn = tk.Label(log_header, text=symbol, font=('', 10, ''),
                           bg=t["bg"], fg=t["fg"], padx=6, cursor="hand2")
            btn.pack(side='right', padx=(2, 0))
            btn.bind("<Button-1>", lambda e, d=delta: _change_font(d))
            btn.bind("<Enter>", lambda e, b=btn, tp=tip: (
                b.configure(bg=self._theme()["hover"]),
                self._show_tooltip_popup(b, tp, below=True)))
            btn.bind("<Leave>", lambda e, b=btn: (
                b.configure(bg=self._theme()["bg"]), self._hide_tooltip()))

        log_frame = tk.Frame(self._right)
        log_frame.pack(fill='both', expand=True)

        self._scrollbar = tk.Scrollbar(log_frame)
        self._scrollbar.pack(side='right', fill='y')

        self.log = tk.Text(log_frame, state='disabled', wrap='word',
                           font=('Courier', self._log_font_size),
                           yscrollcommand=self._on_scroll,
                           relief='flat', bd=0, highlightthickness=0)
        self.log.pack(side='left', fill='both', expand=True)
        self._scrollbar.config(command=self.log.yview)
        self._scrollbar.pack_forget()

        self._rebuild_buttons()

    def _on_scroll(self, first, last):
        self._scrollbar.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            self._scrollbar.pack_forget()
        else:
            self._scrollbar.pack(side='right', fill='y')

    # ── button building ───────────────────────────────────────────────────────

    def _rebuild_buttons(self):
        self._btn_labels.clear()
        self._suboption_labels.clear()
        self._open_accordion = {}
        for w in self._btn_frame.winfo_children():
            w.destroy()

        if self.config.get("ui_mode", "dropdown") == "dropdown":
            self._build_dropdown_buttons()
        else:
            self._build_classic_buttons()

        self._apply_theme()
        self._update_button_states()
        self.root.after_idle(self._ensure_size)

    def _build_classic_buttons(self):
        from collections import OrderedDict
        toggleable_sections = OrderedDict()
        for key, section, label, method in self.TOGGLEABLE:
            if self.config.get(key, True):
                toggleable_sections.setdefault(section, []).append(
                    (label, getattr(self, method)))

        fixed_sections = [
            ("Input", [
                ("Add Input",   self.pick_files),
                ("Clear Input", self.clear_input),
                ("Open Input",  self.open_input),
            ]),
            ("Utility", [
                ("Status",       self.run_status),
                ("Clear Log",    self.clear_log),
                ("Open Output",  self.open_output),
                ("Clear Output", self.clear_output),
                ("Cancel Operation",   self.cancel_job),
            ]),
        ]

        for section_label, cmds in list(toggleable_sections.items()) + fixed_sections:
            tk.Label(self._btn_frame, text=section_label,
                     font=self._uf(bold=True)).pack(anchor='w', pady=(8, 2))
            for label, cmd in cmds:
                self._make_button(self._btn_frame, label, cmd,
                                  tooltip=self.TOOLTIPS.get(label))

    def _build_dropdown_buttons(self):
        t = self._theme()
        from collections import OrderedDict
        tool_rows = OrderedDict()
        for key, section, label, method in self.TOGGLEABLE:
            if self.config.get(key, True):
                tool_rows.setdefault(section, []).append(
                    (label, getattr(self, method)))

        for section_label, items in tool_rows.items():
            tk.Label(self._btn_frame, text=section_label,
                     font=self._uf(bold=True), bg=t["bg"], fg=t["fg"]
                     ).pack(anchor='w', pady=(8, 2))
            for label, run_fn in items:
                self._make_tool_accordion(self._btn_frame, label, run_fn)

        tk.Label(self._btn_frame, text="Input",
                 font=self._uf(bold=True), bg=t["bg"], fg=t["fg"]
                 ).pack(anchor='w', pady=(8, 2))
        self._make_tool_accordion(self._btn_frame, "Add Input", self.pick_files)
        for label, cmd in [("Clear Input", self.clear_input), ("Open Input", self.open_input)]:
            self._make_button(self._btn_frame, label, cmd, tooltip=self.TOOLTIPS.get(label))

        tk.Label(self._btn_frame, text="Utility",
                 font=self._uf(bold=True), bg=t["bg"], fg=t["fg"]
                 ).pack(anchor='w', pady=(8, 2))
        for label, cmd in [
            ("Status",       self.run_status),
            ("Clear Log",    self.clear_log),
            ("Open Output",  self.open_output),
            ("Clear Output", self.clear_output),
            ("Cancel Operation",   self.cancel_job),
        ]:
            self._make_button(self._btn_frame, label, cmd, tooltip=self.TOOLTIPS.get(label))

    def _make_button(self, parent, text, cmd, tooltip=None):
        t = self._theme()
        row = tk.Frame(parent, bg=t["bg"])
        row.pack(pady=2, fill='x')

        lbl = tk.Label(row, text=text, bg=t["bg"], fg=t["fg"],
                       font=self._uf(), width=22, cursor="hand2")
        lbl.bind("<Button-1>", lambda e: cmd())
        lbl.bind("<Enter>", lambda e: lbl.configure(bg=self._theme()["hover"]))
        lbl.bind("<Leave>", lambda e: lbl.configure(bg=self._theme()["bg"]))
        lbl.pack(side='left', padx=1, pady=1)

        self._btn_labels[text] = lbl

        if tooltip and self.config.get("show_tooltips", True):
            info = tk.Label(row, text="i", bg=t["bg"], fg=t["hint_fg"],
                            font=self._uf(-1), cursor="hand2", padx=2)
            info.pack(side='left', padx=(3, 0))
            info.bind("<Enter>", lambda e: info.configure(bg=self._theme()["hover"]))
            info.bind("<Leave>", lambda e: info.configure(bg=self._theme()["bg"]))
            self._show_tooltip(info, tooltip)

        return lbl

    def _make_tool_accordion(self, parent, label, run_fn):
        t = self._theme()
        options = self.TOOL_OPTIONS.get(label, [])
        config_key  = self.TOOL_MODE_CONFIG_KEY.get(label)
        has_default = (config_key is not None and self.config.get(config_key, "ask") != "ask")

        outer   = tk.Frame(parent, bg=t["bg"])
        outer.pack(fill='x', pady=1)
        hdr_row = tk.Frame(outer, bg=t["bg"])
        hdr_row.pack(fill='x')

        hdr_lbl = tk.Label(hdr_row, text=label, bg=t["bg"], fg=t["fg"],
                           font=self._uf(), width=22, cursor="hand2")
        hdr_lbl.pack(side='left', padx=1, pady=1)

        self._btn_labels[label] = hdr_lbl

        if self.config.get("show_tooltips", True) and label in self.TOOLTIPS:
            info = tk.Label(hdr_row, text="i", bg=t["bg"], fg=t["hint_fg"],
                            font=self._uf(-1), cursor="hand2", padx=2)
            info.pack(side='left', padx=(3, 0))
            info.bind("<Enter>", lambda e: info.configure(bg=self._theme()["hover"]))
            info.bind("<Leave>", lambda e: info.configure(bg=self._theme()["bg"]))
            self._show_tooltip(info, self._tool_tip_text(label, has_default))

        body  = tk.Frame(outer, bg=t["bg"])
        state = {"open": False}

        def _update_label(is_open, lbl=hdr_lbl, lbl_text=label):
            lbl.configure(text=(COLLAPSE if is_open else EXPAND) + " " + lbl_text)

        _update_label(False)

        def set_open(label=label, state=state, body=body):
            for lbl_key, info in self._open_accordion.items():
                if lbl_key != label and info["open"]:
                    info["close"]()
            if state["open"]:
                body.pack_forget()
                _update_label(False)
                state["open"] = False
                self._open_accordion[label]["open"] = False
            else:
                body.pack(fill='x')
                _update_label(True)
                state["open"] = True
                self._open_accordion[label]["open"] = True
                self.root.after_idle(self._ensure_size)

        def close_fn(body=body, state=state):
            body.pack_forget()
            _update_label(False)
            state["open"] = False

        self._open_accordion[label] = {"open": False, "close": close_fn}

        if not options or has_default:
            # Nothing to choose: the box itself runs the tool (same as Clear Input, Status, ...).
            hdr_lbl.configure(text=label)
            on_click = lambda e, fn=run_fn, jn=label: self._inject_and_run(fn, None, jn)
        else:
            on_click = lambda e, fn=set_open: fn()          # opens/closes the option list
        hdr_lbl.bind("<Button-1>", on_click)
        hdr_lbl.bind("<Enter>", lambda e: hdr_lbl.configure(bg=self._theme()["hover"]))
        hdr_lbl.bind("<Leave>", lambda e: hdr_lbl.configure(bg=self._theme()["bg"]))

        if options and not has_default:
            # Each option is a box like the other buttons: its right edge lines up with the header's.
            import tkinter.font as tkfont
            indent = 18
            char_w = max(1, tkfont.Font(root=self.root, font=self._uf()).measure("0"))
            opt_chars = max(8, 22 - round(indent / char_w))
            for opt in options:
                opt_label = self.OPTION_LABELS.get(opt, opt)
                row = tk.Frame(body, bg=t["bg"])
                row.pack(fill='x', pady=1)

                opt_lbl = tk.Label(row, text=opt_label, bg=t["bg"], fg=t["fg"],
                                   font=self._uf(), width=opt_chars, anchor='w', padx=4,
                                   cursor="hand2")
                opt_lbl.pack(side='left', padx=(indent + 1, 1), pady=1)
                self._suboption_labels.setdefault(label, []).append(opt_lbl)

                opt_lbl.bind("<Button-1>",
                             lambda e, fn=run_fn, o=opt, jn=label:
                             self._inject_and_run(fn, o, jn))
                opt_lbl.bind("<Enter>", lambda e, b=opt_lbl: b.configure(bg=self._theme()["hover"]))
                opt_lbl.bind("<Leave>", lambda e, b=opt_lbl: b.configure(bg=self._theme()["bg"]))

    # ── preferences / tabs ────────────────────────────────────────────────────

    def _show_preferences(self):
        show_preferences(self)

    # ── job running ───────────────────────────────────────────────────────────

    TOOL_FUNCS = {
        "Folders to PDF":     folders_to_pdf,
        "Images to PDF":      images_to_pdf,
        "Folder Renamer":     folder_renamer,
        "File Renamer":       file_renamer,
        "Combine Image Sets": combine_image_sets,
        "Image Converter":    image_converter,
        "Find Duplicates":    find_duplicates,
        "PDF Combiner":       pdf_combiner,
        "PDF Splitter":       pdf_splitter,
        "PDF to Images":      pdf_to_images,
    }

    # Which folders must be set before a job can run (everything else needs both).
    JOB_NEEDS = {"Add Input": ("input",), "Clear Input": ("input",), "Clear Output": ("output",)}

    def _path_problem(self, *needs):
        """Returns a message if a required folder is not set or has disappeared, else None."""
        for key in needs:
            val = self.config.get(key, "")
            if not val:
                return f"  The {key} folder is not set. Click ≡, then Paths, and choose one."
            if not Path(val).is_dir():
                return (f"  The {key} folder no longer exists: {val}\n"
                        f"  Click ≡, then Paths, and choose another.")
        return None

    def _busy_jobs(self):
        with self._jobs_lock:
            return [n for n in self._running_jobs if n != "Status"]

    def _inject_and_run(self, run_fn, choice, job_name):
        if job_name == "Add Input":
            self._run(lambda c=choice: self._pick_files_work(c), job_name=job_name)
            return

        tool = self.TOOL_FUNCS.get(job_name)
        if tool is None:
            self._run(run_fn, job_name=job_name)
            return

        # The tool gets its OWN copy of the settings with the chosen option filled in.
        # The real settings (self.config) are never touched, so nothing can leak into config.json.
        cfg = dict(self.config)
        config_key = self.TOOL_MODE_CONFIG_KEY.get(job_name)
        if choice is not None and config_key:
            cfg[config_key] = choice
        self._run(lambda: tool(cfg, self.cancel_event), job_name=job_name)

    def _run(self, fn, ignore_lock=False, job_name="Job"):
        problem = self._path_problem(*self.JOB_NEEDS.get(job_name, ("input", "output")))
        if problem:
            print(problem)
            return

        with self._jobs_lock:
            busy = [n for n in self._running_jobs if n != "Status"]
            refusal = None
            if busy and not ignore_lock:
                if not self.config.get("allow_concurrent_jobs", False):
                    refusal = "  A job is already running. Wait for it to finish or cancel it first."
                elif job_name in self._running_jobs:
                    refusal = f"  {job_name} is already running."
            if refusal is None:
                if not busy:
                    # Only reset Cancel when nothing else is running, otherwise starting
                    # (or clicking Status) would silently undo the user's Cancel.
                    self.cancel_event.clear()
                self._running_jobs[job_name] = self._running_jobs.get(job_name, 0) + 1
        if refusal:
            print(refusal)
            return

        if job_name != "Add Input" and self.config.get("show_timestamps", True):
            line_len = max(10, int(55 - (self._log_font_size - 7) * 2.5))
            timestamp = datetime.now().strftime("%H:%M:%S")
            prefix = "\n" if self.config.get("log_blank_lines", False) else ""
            self._log_redirect.header(f"{prefix}{timestamp}\n{'─' * line_len}\n\n")

        self._update_status_label()

        def wrapper():
            try:
                fn()
            except Exception as e:
                self._report_job_error(job_name, e)
            finally:
                try:
                    settle_runs(self.config)      # a tool that stopped early leaves its results in a hidden folder
                except Exception:
                    traceback.print_exc()
                with self._jobs_lock:
                    count = self._running_jobs.get(job_name, 1) - 1
                    if count <= 0:
                        self._running_jobs.pop(job_name, None)
                    else:
                        self._running_jobs[job_name] = count
                self._jobs_changed.set()      # the main thread updates the label and Input count

        threading.Thread(target=wrapper, daemon=True).start()

    def _report_job_error(self, job_name, error):
        """A tool crashed: show a short readable message, and keep the technical traceback collapsed."""
        log = sys.stdout
        if hasattr(log, "end_section"):
            log.end_section()                 # a crash can leave a section open, which would hide this message
        print(f"  ✖ {job_name} stopped because of an error: {error}")
        if hasattr(log, "start_section"):
            log.start_section("  Error details (include this if you report a bug)")
            for line in traceback.format_exc().rstrip().splitlines():
                print("    " + line)
            log.end_section()
        print("")

    def cancel_job(self):
        if self._pending_reply is not None:
            # A job is waiting for an answer: closing the question cancels it, the job reports "Cancelled."
            self._finish_input(SENTINEL)
            return
        if not self._busy_jobs():
            print("  No job is running.")
            return
        self.cancel_event.set()
        print("  Cancel requested. Jobs stop at their next safe point (PDF writing can't be interrupted).")

    def _update_status_label(self):
        with self._jobs_lock:
            jobs = dict(self._running_jobs)
        if not jobs:
            self._status_lbl.configure(text="")
        else:
            names = []
            for name, count in jobs.items():
                names.append(f"{name}" if count == 1 else f"{name} ×{count}")
            self._status_lbl.configure(text="● " + "  |  ".join(names))

    # ── utility actions ───────────────────────────────────────────────────────

    def clear_log(self):
        self._log_redirect.clear()

    @staticmethod
    def _delete_contents(folder, label):
        removed = failed = 0
        for item in folder.iterdir():
            try:
                if item.is_dir() and not item.is_symlink():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                removed += 1
            except Exception as e:
                print(f"Failed to delete {item.name}: {e}")
                failed += 1
        print(f"{label} cleared." + (f" ({failed} item(s) could not be deleted)" if failed else ""))

    def _clear_folder(self, folder, label):
        """Shared by Clear Input / Clear Output: checks, asks first only if Preferences says so, then deletes in the background."""
        if self._busy_jobs():
            print("  A job is running. Wait for it to finish (or cancel it) before clearing.")
            return
        if not folder.exists() or not any(folder.iterdir()):
            print(f"{label} folder is already empty.")
            return
        if self.config.get("confirm_clear", False):
            if not messagebox.askyesno(
                    f"Clear {label}",
                    f"Delete everything inside this folder?\n\n{folder}\n\nThis cannot be undone.",
                    icon="warning", parent=self.root):
                print(f"  Clear {label.lower()} cancelled.")
                return
        self._run(lambda: self._delete_contents(folder, label), job_name=f"Clear {label}")

    def clear_output(self):
        problem = self._path_problem("output")
        if problem:
            print(problem)
            return
        self._clear_folder(Path(self.config["output"]) / "output", "Output")

    def clear_input(self):
        problem = self._path_problem("input")
        if problem:
            print(problem)
            return
        self._clear_folder(get_input(self.config), "Input")

    @staticmethod
    def _open_folder(path):
        import subprocess
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def open_input(self):
        problem = self._path_problem("input")
        if problem:
            print(problem)
            return
        input_dir = get_input(self.config)
        input_dir.mkdir(parents=True, exist_ok=True)
        print(f"  Input: {input_dir}")
        self._open_folder(input_dir)

    def open_output(self):
        problem = self._path_problem("output")
        if problem:
            print(problem)
            return
        out_dir = Path(self.config["output"]) / "output"
        if not out_dir.exists():
            print(f"  Output folder does not exist yet: {out_dir}")
            print("  It is created the first time a tool runs.")
            return

        target = out_dir
        if self.config.get("open_output_recent", False):
            subfolders = [f for f in out_dir.iterdir() if f.is_dir() and not f.name.startswith('.')]
            if subfolders:
                target = max(subfolders, key=lambda f: f.stat().st_mtime)
                print(f"  Output (most recent): {target}")
            else:
                print(f"  Output: {out_dir}")
        else:
            print(f"  Output: {out_dir}")

        self._open_folder(target)

    # ── file picking ──────────────────────────────────────────────────────────

    def pick_files(self, choice=None):
        self._run(lambda: self._pick_files_work(choice), job_name="Add Input")

    def _pick_files_work(self, choice=None):
        """Runs in a worker thread. File dialogs are opened by the main thread (see _call_in_main)."""
        input_dir = get_input(self.config)
        input_dir.mkdir(parents=True, exist_ok=True)

        if choice is None:
            print("Add to Input")
            print("  Enter = Files   Space = Folder   Escape = Cancel")
            raw = thread_safe_input("Waiting for key...").strip()
            c = {"FILES": "files", "FOLDER": "folder"}.get(raw)
            if c is None:
                print("  Cancelled.")
                return
        else:
            c = choice

        if c == "output":
            problem = self._path_problem("output")
            if problem:
                print(problem)
                return

        dialog_result = queue.Queue()

        def open_dialog():
            result = []
            try:
                if c == "files":
                    picked = filedialog.askopenfilenames(title="Select files to add to Input")
                    result = list(self.root.tk.splitlist(picked)) if isinstance(picked, str) else list(picked)
                elif c == "folder":
                    folder = filedialog.askdirectory(title="Select a folder to add to Input")
                    result = [folder] if folder else []
                elif c == "output":
                    out_dir = Path(self.config["output"]) / "output"
                    result = [str(f) for f in out_dir.iterdir() if not f.name.startswith('.')] if out_dir.exists() else []
            except Exception as e:
                print(f"  Could not open the file picker: {e}")
            finally:
                dialog_result.put(result)      # always answer, or this job would wait forever

        self._call_in_main(open_dialog)
        paths = dialog_result.get()
        if not paths:
            print("  Nothing selected.")
            return
        print(f"  Copying {len(paths)} item(s) to Input...")
        ok, fail = 0, 0
        for p in paths:
            src  = Path(p)
            dest = input_dir / src.name
            try:
                if src.is_dir():
                    shutil.copytree(str(src), str(dest), dirs_exist_ok=True)
                else:
                    shutil.copy2(str(src), str(dest))
                ok += 1
            except Exception as e:
                print(f"  Failed: {src.name}: {e}")
                fail += 1
        print(f"  Done! {ok} added{f', {fail} failed' if fail else ''} → {input_dir}")

    # ── tool runners ──────────────────────────────────────────────────────────

    def run_folders_to_pdf(self):
        self._run(lambda: folders_to_pdf(self.config, self.cancel_event), job_name="Folders to PDF")

    def run_images_to_pdf(self):
        self._run(lambda: images_to_pdf(self.config, self.cancel_event), job_name="Images to PDF")

    def run_folder_renamer(self):
        self._run(lambda: folder_renamer(self.config, self.cancel_event), job_name="Folder Renamer")

    def run_file_renamer(self):
        self._run(lambda: file_renamer(self.config, self.cancel_event), job_name="File Renamer")

    def run_combine(self):
        self._run(lambda: combine_image_sets(self.config, self.cancel_event), job_name="Combine Image Sets")

    def run_converter(self):
        self._run(lambda: image_converter(self.config, self.cancel_event), job_name="Image Converter")

    def run_duplicates(self):
        self._run(lambda: find_duplicates(self.config, self.cancel_event), job_name="Find Duplicates")

    def run_pdf_splitter(self):
        self._run(lambda: pdf_splitter(self.config, self.cancel_event), job_name="PDF Splitter")

    def run_pdf_combiner(self):
        self._run(lambda: pdf_combiner(self.config, self.cancel_event), job_name="PDF Combiner")

    def run_pdf_to_images(self):
        self._run(lambda: pdf_to_images(self.config, self.cancel_event), job_name="PDF to Images")

    def run_status(self):
        if self._status_running:
            return

        def _run():
            self._status_running = True
            try:
                status(self.config)
            finally:
                self._status_running = False

        self._run(_run, ignore_lock=True, job_name="Status")




if __name__ == "__main__":
    root = tk.Tk()
    root.geometry("900x600")
    app = App(root)
    root.mainloop()
