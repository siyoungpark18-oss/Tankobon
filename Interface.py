#IMPORTS——————————————————————————————————————————————————————————————————————————————————————————————————
import tkinter as tk
from tkinter import scrolledtext, filedialog
from pathlib import Path
import threading
import queue
import sys
import shutil
import webbrowser
import functools

#IMPORT FROM MANAGER——————————————————————————————————————————————————————————————————————————————————————
from Manager import (
    load_config, save_config, get_input,
    folders_to_pdf, images_to_pdf, folder_renamer, file_renamer,
    combine_image_sets, image_converter, pdf_splitter, pdf_combiner,
    pdf_to_images, status, find_duplicates, DEFAULTS, SENTINEL
)

#IMPORT FROM LOG, PREFERENCES AND THEMES—————————————————————————————————————————————————————————————————
from Log import LogRedirect, input_queue, result_queue, thread_safe_input, patch_input
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
        self.cancel_event = threading.Event()
        self._running_jobs = {}
        self._open_accordion = {}
        self._dark = self.config.get("dark_mode", False)
        self._btn_labels = {}
        self._suboption_labels = {}
        self._last_input_count = -1
        self._moon_image = self._load_moon_icon()
        patch_input()
        self._build_ui()
        self._apply_theme()
        sys.stdout = LogRedirect(self.log, self)
        sys.stderr = LogRedirect(self.log, self)
        self._poll_input()
        if self.config.get("first_launch", True):
            self.config["first_launch"] = False
            save_config(self.config)
            self.root.after(500, self._show_help)
        self._status_running = False

    TOOL_OPTIONS = {
        "Folders to PDF":     ["combine", "individual"],
        "Images to PDF":      [],
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
        "Folders to PDF":     "Combines all folders in Input into a single PDF. Each folder is treated as a chapter.",
        "Images to PDF":      "Converts all images in Input into a single PDF.",
        "Folder Renamer":     "Renames folders by extracting the number from their name. Useful for sorting chapters.",
        "File Renamer":       "Renames files by prefix, suffix, find/replace, or sequence numbering.",
        "Combine Image Sets": "Merges multiple folders of images into one flat folder, preserving order.",
        "Image Converter":    "Converts all images in Input to a chosen format (jpg, png, webp, etc).",
        "Find Duplicates":    "Finds and optionally deletes exact duplicate images by file hash.",
        "PDF Combiner":       "Combines multiple PDFs into one.",
        "PDF Splitter":       "Splits a PDF into parts at page numbers you specify.",
        "PDF to Images":      "Converts a PDF into individual image files. Resource intensive.",
        "Add Input":          "Copies files or a folder into the Input directory for processing.",
        "Clear Input":        "Deletes everything in the Input folder. Originals are not affected.",
        "Status":             "Shows what is currently in the Input and Output folders.",
        "Clear Log":          "Clears the log display.",
        "Clear Output":       "Deletes everything in the Output folder.",
        "Cancel Operation":         "Cancels the currently running job.",
        "Open Input":         "Opens the input folder in Finder/Explorer.",
        "Open Output":        "Opens the output folder in Finder/Explorer and prints the path to the log.",
    }

    # ── theme ─────────────────────────────────────────────────────────────────

    def _theme(self):
        base = "dark" if self._dark else "light"
        hardcoded = THEMES[base]
        custom = self.config.get("themes", {}).get(base, {})
        return {**hardcoded, **custom}

    def _load_moon_icon(self):
        try:
            from PIL import Image, ImageTk
            assets = Path(__file__).parent / "assets"
            img = Image.open(assets / "moon.png").resize((14, 14), Image.LANCZOS)
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
            elif tag in ("ts_dim",) or tag.startswith("log_dim"):
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

    def _poll_input(self):
        try:
            prompt = input_queue.get_nowait()
            self._show_inline_input(prompt)
        except queue.Empty:
            pass

        count = self._get_input_count()
        if count != self._last_input_count:
            self._last_input_count = count
            label = f"[{count} item{'s' if count != 1 else ''}]"
            try:
                self._input_status_lbl.configure(text=label)
            except tk.TclError:
                pass
            self._update_button_states()

        self.root.after(50, self._poll_input)

    # ── inline input ──────────────────────────────────────────────────────────

    def _show_inline_input(self, prompt):
        if hasattr(self, '_input_frame') and self._input_frame.winfo_exists():
            return

        t = self._theme()
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
                         insertbackground=t["entry_fg"])
        entry.pack(side='left', fill='x', expand=True, padx=4)
        entry.focus_set()

        if is_pick:
            def pick_files(e=None):
                self._input_frame.destroy()
                result_queue.put("FILES")
            def pick_folder(e=None):
                self._input_frame.destroy()
                result_queue.put("FOLDER")
            def cancel(e=None):
                self._input_frame.destroy()
                result_queue.put("CANCEL")
            entry.bind("<Return>", pick_files)
            entry.bind("<space>",  pick_folder)
            entry.bind("<Escape>", cancel)
        else:
            continue_key = self.config.get("hotkey_continue", "Return")
            cancel_key   = self.config.get("hotkey_cancel", "Escape")

            def confirm(e=None):
                val = var.get()
                self._input_frame.destroy()
                result_queue.put(val)

            def cancel(e=None):
                self._input_frame.destroy()
                result_queue.put(SENTINEL)

            entry.bind(f"<{continue_key}>", confirm)
            entry.bind(f"<{cancel_key}>",   cancel)

    # ── help / docs ───────────────────────────────────────────────────────────

    def _show_help(self):
        win = tk.Toplevel(self.root)
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
        body("Click Add Input and add your files or folder.")
        text.insert(tk.END, "2. ", "h2")
        body("Select a tool and run it.")
        text.insert(tk.END, "3. ", "h2")
        body("Take the output from the output folder.")
        text.insert(tk.END, "4. ", "h2")
        body("Clear input when done.")
        gap()

        text.configure(state='disabled')

    def _show_docs(self):
        win = tk.Toplevel(self.root)
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
                    "Tankobon is a file manager. It's an open source project that specializes in managing image files in bulk. Its under the AGPL license\n\n")
        text.insert(tk.END, "Github:\n")
        insert_link("https://github.com/siyoungpark18-oss/Tankobon")
        text.insert(tk.END, "\n\nMacOS .dmg:\n")
        insert_link("https://drive.google.com/drive/u/2/folders/1gjRlr2hV7RjLBTGlGs2SqQgKNaW4T0Il")
        text.insert(tk.END, "\n\nPrevious versions:\n")
        insert_link("https://drive.google.com/drive/u/2/folders/1jT_qMHEpWVczcIwBHTYJt1QkrE6WL8pb")
        text.insert(tk.END, "\n")
        text.configure(state='disabled')


    # ── tooltips ──────────────────────────────────────────────────────────────

    def _show_tooltip(self, widget, text):
        def on_enter(e):
            t = self._theme()
            x = widget.winfo_rootx() - self.root.winfo_rootx() + widget.winfo_width() + 4
            y = widget.winfo_rooty() - self.root.winfo_rooty()
            self._tooltip = tk.Label(self.root, text=text, wraplength=220,
                                     justify='left', font=('Courier', 7),
                                     bg=t["bg"], fg=t["fg"], padx=6, pady=4)
            self._tooltip.place(x=x, y=y)
            self._tooltip.lift()

        def on_leave(e):
            if hasattr(self, '_tooltip') and self._tooltip:
                self._tooltip.destroy()
                self._tooltip = None

        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)

    def _show_tooltip_popup(self, widget, text, force_light=False):
        if hasattr(self, '_tooltip') and self._tooltip:
            self._tooltip.destroy()
        t = THEMES["light"] if force_light else self._theme()
        x = widget.winfo_rootx() - self.root.winfo_rootx() + widget.winfo_width() + 4
        y = widget.winfo_rooty() - self.root.winfo_rooty()
        self._tooltip = tk.Label(self.root, text=text, font=('Courier', 7),
                                 bg=t["bg"], fg=t["fg"], padx=6, pady=4)
        self._tooltip.place(x=x, y=y)
        self._tooltip.lift()

    # ── ui build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._log_font_size = self.config.get("log_font_size", 8)

        left = tk.Frame(self.root, width=200)
        left.pack(side='left', fill='y', padx=10, pady=10)
        left.pack_propagate(False)

        title_row = tk.Frame(left)
        title_row.pack(fill='x', pady=(0, 2))
        tk.Label(title_row, text="Tankobon", font=('', 8, 'bold')).pack(side='left')

        def _mini_btn(parent, text_or_var, cmd, side='right', tooltip=None):
            t = self._theme()
            f = tk.Frame(parent, bg=t["fg"], padx=1, pady=1)
            lbl = tk.Label(f, bg=t["bg"], fg=t["fg"], font=('', 7), width=0, cursor="hand2")
            if isinstance(text_or_var, str):
                lbl.configure(text=text_or_var)
            lbl.pack()

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

            f.pack(side=side, padx=(2, 0))
            return lbl

        _mini_btn(title_row, "?", self._show_help,        tooltip="Help")
        _mini_btn(title_row, "i", self._show_docs,        tooltip="Documentation")
        _mini_btn(title_row, "≡", self._show_preferences, tooltip="Preferences")
        moon_icon = self._moon_image if (self._moon_image and not self._dark) else ("☀" if self._dark else "🌙")
        self._dark_btn = _mini_btn(title_row, moon_icon, self._toggle_dark,
                                   tooltip="Dark Mode" if not self._dark else "Bright Mode")
        if self._moon_image and not self._dark:
            self._dark_btn.configure(image=self._moon_image, text="")
            self._dark_btn.image = self._moon_image

        status_row = tk.Frame(left)
        status_row.pack(fill='x', pady=(0, 4))
        t = self._theme()
        self._input_status_lbl = tk.Label(
            status_row, text="[0 items]",
            font=('Courier', 7), fg=t["hint_fg"], bg=t["bg"], anchor='w'
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

        for symbol, delta in (('+', 1), ('-', -1)):
            btn = tk.Label(log_header, text=symbol, font=('', 7, 'bold'),
                           bg=t["bg"], fg=t["fg"], padx=6, cursor="hand2")
            btn.pack(side='right', padx=(2, 0))
            btn.bind("<Button-1>", lambda e, d=delta: _change_font(d))
            btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=self._theme()["hover"]))
            btn.bind("<Leave>", lambda e, b=btn: b.configure(bg=self._theme()["bg"]))

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
                     font=('', 7, 'bold')).pack(anchor='w', pady=(8, 2))
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
                     font=('', 7, 'bold'), bg=t["bg"], fg=t["fg"]
                     ).pack(anchor='w', pady=(8, 2))
            for label, run_fn in items:
                self._make_tool_accordion(self._btn_frame, label, run_fn)

        tk.Label(self._btn_frame, text="Input",
                 font=('', 7, 'bold'), bg=t["bg"], fg=t["fg"]
                 ).pack(anchor='w', pady=(8, 2))
        self._make_tool_accordion(self._btn_frame, "Add Input", self.pick_files)
        for label, cmd in [("Clear Input", self.clear_input), ("Open Input", self.open_input)]:
            self._make_button(self._btn_frame, label, cmd, tooltip=self.TOOLTIPS.get(label))

        tk.Label(self._btn_frame, text="Utility",
                 font=('', 7, 'bold'), bg=t["bg"], fg=t["fg"]
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

        f = tk.Frame(row, bg=t["fg"], padx=1, pady=1)
        lbl = tk.Label(f, text=text, bg=t["bg"], fg=t["fg"],
                       font=('', 7), width=22, cursor="hand2")
        lbl.pack()
        lbl.bind("<Button-1>", lambda e: cmd())
        lbl.bind("<Enter>", lambda e: lbl.configure(bg=self._theme()["hover"]))
        lbl.bind("<Leave>", lambda e: lbl.configure(bg=self._theme()["bg"]))
        f.pack(side='left')

        self._btn_labels[text] = lbl

        if tooltip and self.config.get("show_tooltips", True):
            info = tk.Label(row, text="i", bg=t["bg"], fg=t["hint_fg"],
                            font=('', 6), cursor="hand2", padx=2)
            info.pack(side='left', padx=(3, 0))
            info.bind("<Enter>", lambda e: info.configure(bg=self._theme()["hover"]))
            info.bind("<Leave>", lambda e: info.configure(bg=self._theme()["bg"]))
            self._show_tooltip(info, tooltip)

        return f, lbl

    def _make_tool_accordion(self, parent, label, run_fn):
        t = self._theme()
        options = self.TOOL_OPTIONS.get(label, [])

        outer   = tk.Frame(parent, bg=t["bg"])
        outer.pack(fill='x', pady=1)
        hdr_row = tk.Frame(outer, bg=t["bg"])
        hdr_row.pack(fill='x')

        hdr_f   = tk.Frame(hdr_row, bg=t["fg"], padx=1, pady=1)
        hdr_lbl = tk.Label(hdr_f, text=label, bg=t["bg"], fg=t["fg"],
                           font=('', 7), width=22, cursor="hand2")
        hdr_lbl.pack()
        hdr_f.pack(side='left')

        self._btn_labels[label] = hdr_lbl

        if self.config.get("show_tooltips", True) and label in self.TOOLTIPS:
            info = tk.Label(hdr_row, text="i", bg=t["bg"], fg=t["hint_fg"],
                            font=('', 6), cursor="hand2", padx=2)
            info.pack(side='left', padx=(3, 0))
            info.bind("<Enter>", lambda e: info.configure(bg=self._theme()["hover"]))
            info.bind("<Leave>", lambda e: info.configure(bg=self._theme()["bg"]))
            self._show_tooltip(info, self.TOOLTIPS[label])

        body  = tk.Frame(outer, bg=t["bg"])
        state = {"open": False}

        def _update_label(is_open, lbl=hdr_lbl, lbl_text=label):
            lbl.configure(text=("▼ " if is_open else "▶ ") + lbl_text)

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

        def close_fn(body=body, state=state):
            body.pack_forget()
            _update_label(False)
            state["open"] = False

        self._open_accordion[label] = {"open": False, "close": close_fn}

        config_key  = self.TOOL_MODE_CONFIG_KEY.get(label)
        has_default = (config_key is not None and self.config.get(config_key, "ask") != "ask")

        if not options or has_default:
            hdr_lbl.configure(text=label)
            rbf = tk.Frame(hdr_row, bg=t["fg"], padx=1, pady=1)
            rbl = tk.Label(rbf, text="▶", bg=t["bg"], fg=t["fg"],
                           font=('', 7), padx=5, cursor="hand2")
            rbl.pack()
            rbf.pack(side='right', padx=(4, 2))
            rbl.bind("<Button-1>", lambda e, fn=run_fn, jn=label:
                     self._inject_and_run(fn, None, jn))
            rbl.bind("<Enter>", lambda e, b=rbl: b.configure(bg=self._theme()["hover"]))
            rbl.bind("<Leave>", lambda e, b=rbl: b.configure(bg=self._theme()["bg"]))
        else:
            for w in (hdr_f, hdr_lbl):
                w.bind("<Button-1>", lambda e, fn=set_open: fn())
                w.bind("<Enter>", lambda e: hdr_lbl.configure(bg=self._theme()["hover"]))
                w.bind("<Leave>", lambda e: hdr_lbl.configure(bg=self._theme()["bg"]))

        if options and not has_default:
            for opt in options:
                opt_label = self.OPTION_LABELS.get(opt, opt)
                row = tk.Frame(body, bg=t["bg"])
                row.pack(fill='x', pady=1, padx=(18, 0))

                opt_lbl = tk.Label(row, text=opt_label, bg=t["bg"], fg=t["fg"],
                                   font=('', 7), anchor='w')
                opt_lbl.pack(side='left', fill='x', expand=True)
                self._suboption_labels.setdefault(label, []).append(opt_lbl)

                rbf = tk.Frame(row, bg=t["fg"], padx=1, pady=1)
                rbl = tk.Label(rbf, text="▶", bg=t["bg"], fg=t["fg"],
                               font=('', 7), padx=5, cursor="hand2")
                rbl.pack()
                rbf.pack(side='right', padx=(4, 2))

                rbl.bind("<Button-1>",
                         lambda e, fn=run_fn, o=opt, jn=label:
                         self._inject_and_run(fn, o, jn))
                rbl.bind("<Enter>", lambda e, b=rbl: b.configure(bg=self._theme()["hover"]))
                rbl.bind("<Leave>", lambda e, b=rbl: b.configure(bg=self._theme()["bg"]))
                opt_lbl.bind("<Enter>", lambda e, b=opt_lbl: b.configure(bg=self._theme()["hover"]))
                opt_lbl.bind("<Leave>", lambda e, b=opt_lbl: b.configure(bg=self._theme()["bg"]))

    # ── preferences / tabs ────────────────────────────────────────────────────

    def _show_preferences(self):
        show_preferences(self)

    # ── job running ───────────────────────────────────────────────────────────

    @functools.cached_property
    def _tool_fns(self):
        return {
            "Folders to PDF":     lambda: folders_to_pdf(self.config, self.cancel_event),
            "Images to PDF":      lambda: images_to_pdf(self.config, self.cancel_event),
            "Folder Renamer":     lambda: folder_renamer(self.config, self.cancel_event),
            "File Renamer":       lambda: file_renamer(self.config, self.cancel_event),
            "Combine Image Sets": lambda: combine_image_sets(self.config, self.cancel_event),
            "Image Converter":    lambda: image_converter(self.config, self.cancel_event),
            "Find Duplicates":    lambda: find_duplicates(self.config, self.cancel_event),
            "PDF Combiner":       lambda: pdf_combiner(self.config, self.cancel_event),
            "PDF Splitter":       lambda: pdf_splitter(self.config, self.cancel_event),
            "PDF to Images":      lambda: pdf_to_images(self.config, self.cancel_event),
            "Add Input":          lambda: self.pick_files(),
        }

    def _inject_and_run(self, run_fn, choice, job_name):
        tool_fns = self._tool_fns
        direct_fn = tool_fns.get(job_name)
        if direct_fn is None:
            self._run(run_fn, job_name=job_name)
            return

        if job_name == "Add Input":
            self._run(lambda c=choice: self._pick_files_work(c), job_name=job_name)
            return

        config_key = self.TOOL_MODE_CONFIG_KEY.get(job_name) if choice is not None else None

        if config_key:
            original    = self.config.get(config_key)
            self.config[config_key] = choice
            fn_snapshot = tool_fns[job_name]

            def run_with_restore():
                try:
                    fn_snapshot()
                finally:
                    self.config[config_key] = original

            self._run(run_with_restore, job_name=job_name)
        else:
            self._run(direct_fn, job_name=job_name)

    def _run(self, fn, ignore_lock=False, job_name="Job"):
        if self._running_jobs and not ignore_lock:
            if not self.config.get("allow_concurrent_jobs", False):
                print("  A job is already running. Wait for it to finish or cancel it first.")
                return
            if job_name in self._running_jobs:
                print(f"  {job_name} is already running.")
                return
        self.cancel_event.clear()

        if job_name != "Add Input" and self.config.get("show_timestamps", True):
            from datetime import datetime
            font_size = self._log_font_size
            line_len = max(10, int(55 - (font_size - 7) * 2.5))
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log.configure(state='normal')
            self.log.tag_configure("ts_dim", foreground=self._theme()["log_dim"])
            prefix = "\n" if self.config.get("log_blank_lines", False) else ""
            self.log.insert(tk.END, f"{prefix}{timestamp}\n{'─' * line_len}\n\n", ("ts_dim",))
            self.log.see(tk.END)
            self.log.configure(state='disabled')

        self._running_jobs[job_name] = self._running_jobs.get(job_name, 0) + 1
        self._update_status_label()

        def wrapper():
            try:
                fn()
            finally:
                count = self._running_jobs.get(job_name, 1) - 1
                if count <= 0:
                    self._running_jobs.pop(job_name, None)
                else:
                    self._running_jobs[job_name] = count
                self.root.after(0, self._update_status_label)

        threading.Thread(target=wrapper, daemon=True).start()

    def cancel_job(self):
        if not self._running_jobs:
            print("  No job is running.")
            return
        self.cancel_event.set()
        print("  Cancelling...")

    def _update_status_label(self):
        if not self._running_jobs:
            self._status_lbl.configure(text="")
        else:
            names = []
            for name, count in self._running_jobs.items():
                names.append(f"{name}" if count == 1 else f"{name} ×{count}")
            self._status_lbl.configure(text="● " + "  |  ".join(names))

    # ── utility actions ───────────────────────────────────────────────────────

    def clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', tk.END)
        self.log.configure(state='disabled')

    def clear_output(self):
        out_dir = Path(self.config["output"]) / "output"
        if not out_dir.exists() or not any(out_dir.iterdir()):
            print("Output folder is already empty.")
            return
        for item in out_dir.iterdir():
            try:
                shutil.rmtree(item) if item.is_dir() else item.unlink()
            except Exception as e:
                print(f"Failed to delete {item.name}: {e}")
        print("Output cleared.")

    def clear_input(self):
        input_dir = get_input(self.config)
        if not input_dir.exists() or not any(input_dir.iterdir()):
            print("Input folder is already empty.")
            return
        for item in input_dir.iterdir():
            try:
                shutil.rmtree(item) if item.is_dir() else item.unlink()
            except Exception as e:
                print(f"Failed to delete {item.name}: {e}")
        print("Input cleared.")

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
        input_dir = get_input(self.config)
        if not self.config.get("input", ""):
            print("  Input folder is not configured. Set it in Preferences (≡).")
            return
        if not input_dir.exists():
            print(f"  Input folder does not exist yet: {input_dir}")
            return
        print(f"  Input: {input_dir}")
        self._open_folder(input_dir)

    def open_output(self):
        out_dir = Path(self.config.get("output", "")) / "output"
        if not self.config.get("output", ""):
            print("  Output folder is not configured. Set it in Preferences (≡).")
            return
        if not out_dir.exists():
            print(f"  Output folder does not exist yet: {out_dir}")
            return

        target = out_dir
        if self.config.get("open_output_recent", False):
            subfolders = [f for f in out_dir.iterdir() if f.is_dir()]
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
        if not self.config.get("input", ""):
            print("  Input folder is not configured. Set it in Preferences (≡).")
            return

        input_dir = get_input(self.config)
        input_dir.mkdir(parents=True, exist_ok=True)
        dialog_result = queue.Queue()

        def open_dialog(c):
            if c == "files":
                paths = filedialog.askopenfilenames(title="Select files to add to Input")
                dialog_result.put(list(paths))
            elif c == "folder":
                folder = filedialog.askdirectory(title="Select a folder to add to Input")
                dialog_result.put([folder] if folder else [])
            elif c == "output":
                out_dir = Path(self.config.get("output", "")) / "output"
                if out_dir.exists():
                    dialog_result.put([str(f) for f in out_dir.iterdir()])
                else:
                    dialog_result.put([])
            else:
                dialog_result.put([])

        if choice is None:
            print("Add to Input")
            print("  Enter = Files   Space = Folder   Escape = Cancel")
            raw = thread_safe_input("Waiting for key...").strip()
            if raw == "FILES":
                c = "files"
            elif raw == "FOLDER":
                c = "folder"
            else:
                print("  Cancelled.")
                return
        else:
            c = choice

        self.root.after(0, lambda: open_dialog(c))
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
