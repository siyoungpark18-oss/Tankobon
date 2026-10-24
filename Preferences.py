#PREFERENCES——————————————————————————————————————————————————————————————————————————————————————————————————
import tkinter as tk
from tkinter import messagebox, filedialog
from pathlib import Path
import re

from Themes import THEMES
from ToolNames import DOCUMENT_FORMATS, FOLDER_FORMATS
from ManagerConfig import save_config


PATH_TIPS = {
    "input":  "The folder Tankobon reads from. Files and folders copied here are processed by tools that you can operate.\n You can use the 'open input' tool to view the current input folder",
    "output": "The folder Tankobon writes results to. Once the Input is processed by tools, the result is here.\n You can use the 'open output' tool to view the current output folder",
}

PREF_TIPS = {
    "allow_concurrent_jobs":      "Run multiple different tools at the same time. With this, outputs can generate in quick succession.\n To prevent new outputs from replacing old ones, use the run name option and disable replace output.\n\n Generally leads to more errors and bugs and tends to spread computer resources thin.",
    "auto_clear_input":           "Automatically Empties the Input folder after an Operation is completed.",
    "confirm_clear_input":        "Asks before deleting everything inside the Input folder. Input usually holds copies of your files.",
    "confirm_clear_output":       "Asks before deleting everything inside the Output folder. Output results may not be backed up anywhere.",
    "ask_run_name":               "Prompts you to name each output. The results are then placed in a folder with that name inside the output folder.\n Leave the name blank, and results go straight into the main output folder.\n Does not name individual files.",
    "replace_output":             "When enabled, a new result replaces an older one with the same name. In your own run / operation folders that is the whole folder; in the main output folder it is each file, folder or archive with the same name.\n When disabled, the new result gets a number added to its name instead (Ch 1 (2).cbz).",
    "sort_output":                "Puts results in a subfolder named after each tool.\n If outputs are also named, they are named within the tool folder.\n With this and Run Name both off, results go straight into the main output folder.",
    "guide_empty_input":          "Dims the tool buttons when the Input folder is empty\n A visual reminder to add an Input.",
    "show_tooltips":              "Shows the small 'i' hint icons next to buttons and settings. This setting applies immediately.",
    "log_default_expanded":       "Collapsible areas of the Log, not the toolbar,\n are expanded by default rather than collapsed.",
    "show_timestamps":            "Prints a timestamp and divider line in the log before each Operation.\n Happens for all Tools and the Status Utility.",
    "open_output_recent":         "When enabled, the Open Output button opens the most recent output rather than the entire Output folder.\n Useful for jumping straight to your latest result.",
    "min_free_gb":                "The Minimum Storage Requirement to begin an Operation in GB.\n The program will usually warn you of low storage before this.",
    "default_doc_format":         "The format selected when Tankobon starts. The PDF/EPUB selector in the main interface changes the current session only; use this setting to change the startup default. An EPUB is one multi-page book. In Individual mode, each folder becomes one book with images as its pages; Combine makes one book from all folders. EPUB output from a PDF renders its pages as images, which can take longer and need extra space.",
    "epub_direction":             "Reading direction of the EPUBs Tankobon writes. RTL (right to left) is how manga is read.\n It only changes the page-turn direction; the pages stay in the order they were processed.",
    "default_folders_to_pdf_mode":"The 'Combine' option makes one PDF or EPUB book from all folders. The 'Individual' option makes one book per folder. In an EPUB, each image is a page in that book.",
    "default_sort":               "Controls the Order files are processed in.\n Natural mode reads numbers in names as numbers.\n So 'chapter 2' comes before 'chapter 10' (a plain alphabetical sort would put 10 first).\n\n 'None' simply uses your filesystem order.",
    "default_folder_renamer_mode":"The mode the Folder Renamer uses by default.\n\n The 'Extract Number' mode names the folder after any numbers in its name to make it easier to sort.\n This mode doesn't work very well if there are multiple unrelated numbers in the file name",
    "default_file_renamer_mode":  "The mode the File Renamer uses by default.\n\n The 'Sequence' mode names the file after numerical order in the file system with the option to add a base.\n This base replaces the current file name and is applied to all files.\n\n This mode doesn't work very well if there are multiple unrelated numbers in the file name",
    "default_img_fmt":            "The default image format the Image Converter converts to.",
    "default_pdf_to_images_fmt":  "The default image format for images exported from a PDF.",
    "default_dedupe_mode":        "'Keep one copy' excludes all identical files except the original from the output.\n Delete all excludes all identical files from output, including its original.",
    "default_dedupe_hash":        "Exact (SHA-256) is fast and only matches files that are byte-for-byte identical, including metadata.\n Perceptual (pHash) matches images that look visually identical regardless of format or metadata, but is significantly slower.",
    "phash_threshold":            "How close two perceptual hashes must be to count as duplicates. Lower values are stricter; higher values may group images that differ more.",
    "default_dpi":                "The Default DPI when converting a PDF to Images.\n A DPI higher than 72 or 96 is generally only useful for paper printing.",
    "hotkey_continue":            "The key you press to continue or confirm when a tool asks for input.",
    "hotkey_cancel":              "The key you press to cancel when a tool asks for input. Not all operations can be canceled consistently once they are running.",
    "throttle_cpu":               "Throttles the Operation if CPU usage exceeds the threshold.\n If your computer already exceeds this limit, it will throttle until it drops below it.\n 0 removes the limit, and can cause your computer to crash.",
    "throttle_mem":               "Throttles the Operation if RAM usage exceeds the threshold.\n If your computer already exceeds this limit, it will throttle until it drops below it.\n 0 removes the limit, and can cause your computer to crash.",
    "log_blank_lines":            "Double spaces most text in the log.\nDisable for a more compact log.",
    "preview_limit":              "How many lines a Log expansion lists before it shows '… and N more'.\n Type any whole number. 0 shows everything.\n Anything that isn't a whole number is replaced with 10.",
    "zip_extract_input":          "Zip (.zip) and comic archive (.cbz) files in Input are unpacked into folders before any tool runs, so they work like normal folders.\n The zip is removed after it is unpacked (Input only holds copies). Zips inside zips are unpacked too.\n Zips that are broken or password-protected are left alone and reported.",
    "zip_compression":            "The folder format selected when Tankobon starts. The CBZ/ZIP/Uncompressed selector in the main interface changes the current session only; use this setting to change the startup default. Each result folder becomes one CBZ or ZIP, or stays as ordinary files and folders when Uncompressed is selected. A CBZ may only hold images, so a folder with other file types is saved as ZIP instead.",
    "keep_documents_unarchived":  "Writes PDFs and EPUBs directly into the Output folder, regardless of the selected folder format. Other files follow the CBZ, ZIP, or Uncompressed choice.",
    "show_folder_stats":          "Shows the number of images and the total size next to each folder in the Log.\n Needs a quick look at each file's size, so turn this off if scanning very large Inputs feels slow.",
}

KEY_LABELS = {
    "bg":          "Background",
    "fg":          "Text",
    "log_bg":      "Log Background",
    "log_fg":      "Log Text",
    "entry_bg":    "Input Field Background",
    "entry_fg":    "Input Field Text",
    "btn_bg":      "Button Background",
    "btn_fg":      "Button Text",
    "hint_fg":     "Hint / Dim Text",
    "hover":       "Hover Highlight",
    "log_error":   "Log Error",
    "log_warn":    "Log Warning",
    "log_success": "Log Success",
    "log_dim":     "Log Dim Text",
}


def _safe_int(val, default, lo=None, hi=None):
    """Whole number from text. Anything unusable (letters, blank, out of range) gives `default` instead of crashing."""
    try:
        n = int(str(val).strip().split()[0])
    except (ValueError, IndexError):
        return default
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        return default
    return n


class FlatDropdown(tk.Frame):
    def __init__(self, parent, variable, options, t, win, **kwargs):
        super().__init__(parent, bg=t["btn_bg"], relief='flat', bd=0, **kwargs)
        self._var = variable
        self._opts = options
        self._t = t
        self._win = win

        self._btn = tk.Label(self, textvariable=variable, bg=t["btn_bg"], fg=t["fg"],
                             font=('', 10), padx=6, pady=2, cursor="hand2",
                             relief='flat', width=16, anchor='w')
        self._btn.pack(side='left')
        self._btn.bind("<Button-1>", self._show_menu)

        arr = tk.Label(self, text="▾", bg=t["btn_bg"], fg=t["hint_fg"],
                       font=('', 10), cursor="hand2", padx=2)
        arr.pack(side='left')
        arr.bind("<Button-1>", self._show_menu)

    def _show_menu(self, e=None):
        t = self._t
        menu = tk.Menu(self._win, tearoff=0, bg=t["bg"], fg=t["fg"],
                       activebackground=t["hover"], activeforeground=t["fg"],
                       relief='flat', bd=0, font=('', 10))
        for opt in self._opts:
            menu.add_command(label=opt, command=lambda o=opt: self._var.set(o))
        menu.tk_popup(self._btn.winfo_rootx(), self._btn.winfo_rooty() + self._btn.winfo_height())


def _build_paths_page(app, t, pages, Label, paths, lbl_vals, pick, clear_path, pref_btn, show_tip, hide_tip):
    # ── PATHS tab ──────────────────────────────────────────────────────────
    p = pages["Paths"]
    r = 0
    Label(p, text="Paths", font=('', 10, 'bold'), bg=t["bg"]).grid(
        row=r, column=0, columnspan=2, sticky='w', pady=(0, 8))
    r += 1

    for key, title in (("input", "Input directory"), ("output", "Output directory")):
        row_f = tk.Frame(p, bg=t["bg"])
        row_f.grid(row=r, column=0, columnspan=2, sticky='w', pady=(6, 0))
        Label(row_f, text=title, anchor='w', bg=t["bg"]).pack(side='left')
        if app.config.get("show_tooltips", True):
            info = Label(row_f, text="i", bg=t["bg"], fg=t["hint_fg"],
                            font=('', 10), cursor="hand2", padx=2)
            info.pack(side='left', padx=(3, 0))
            info.bind("<Enter>", lambda e, w=info, txt=PATH_TIPS[key]: (
                w.configure(bg=t["hover"]), show_tip(w, txt)))
            info.bind("<Leave>", lambda e, w=info: (
                w.configure(bg=t["bg"]), hide_tip()))
        r += 1
        e = tk.Entry(p, width=40, bg=t["entry_bg"], fg=t["entry_fg"], readonlybackground=t["entry_bg"],
                     insertbackground=t["entry_fg"],
                     relief='flat', highlightthickness=1,
                     highlightbackground=t["entry_bg"], highlightcolor=t["entry_bg"])
        e.insert(0, paths[key].get())
        e.configure(state='readonly')
        e.grid(row=r, column=0, sticky='w')
        lbl_vals[key] = e
        btns = tk.Frame(p, bg=t["bg"])
        btns.grid(row=r, column=1, padx=(6, 0), sticky='w')
        pref_btn(btns, "Browse…", lambda k=key: pick(k)).pack(side='left')
        pref_btn(btns, "Clear", lambda k=key: clear_path(k)).pack(side='left', padx=(2, 0))
        r += 1
    return paths


def _build_primary_pages(app, t, win, pages, Label, info_btn, check_btn):
    # ── GENERAL / TOOLS / HOTKEYS / THROTTLE tabs ──────────────────────────
    general_fields = [
        ("log_blank_lines",       "Double Space in Log",            "check", None),
        ("auto_clear_input",      "Clear Input after Operation",    "check", None),
        ("confirm_clear_input",   "Confirm before Clearing Input",  "check", None),
        ("confirm_clear_output",  "Confirm before Clearing Output", "check", None),
        ("ask_run_name",          "Run Name",                       "check", None),
        ("replace_output",        "Replace Output",                 "check", None),
        ("guide_empty_input",     "Dim Tools when Input Empty",     "check", None),
        ("show_tooltips",         "Show Tooltips",                  "check", None),
        ("log_default_expanded",  "Expand Log Dialogue by Default", "check", None),
        ("preview_limit",         "Log List Size (0 = all)",        "entry", "10"),
        ("show_folder_stats",     "Show Folder Counts & Sizes",     "check", None),
        ("show_timestamps",       "Show Timestamps",                "check", None),
        ("open_output_recent",    "Open Most Recent Output",        "check", None),
        ("keep_documents_unarchived", "Write PDFs & EPUBs Directly to Output", "check", None),
        ("min_free_gb",           "Minimum Free Space",             "combo", ["0","1","2","3","5","10"]),
    ]

    tools_fields = [
        ("default_doc_format",          "Default File Production Format", "combo", list(DOCUMENT_FORMATS)),
        ("zip_compression",             "Default Folder Production Format", "combo", list(FOLDER_FORMATS)),
        ("epub_direction",              "EPUB Reading Direction",         "combo", ["rtl", "ltr"]),
        ("default_folders_to_pdf_mode", "Folders to File: Default Mode",  "combo", ["ask", "combine", "individual"]),
        ("default_images_to_pdf_mode", "Images to File: Default Mode", "combo", ["ask", "combine", "reorder & combine"]),
        ("default_sort",                "Default Sort Mode",              "combo", ["natural", "none"]),
        ("default_folder_renamer_mode", "Folder Renamer: Default Mode",  "combo", ["ask", "prefix", "suffix", "replace", "extract number"]),
        ("default_file_renamer_mode",   "File Renamer: Default Mode",    "combo", ["ask", "prefix", "suffix", "replace", "sequence"]),
        ("default_img_fmt",             "Image Converter: Default Format","combo", ["ask", "jpg", "png", "webp", "bmp", "tiff"]),
        ("default_pdf_to_images_fmt",   "PDF to Images: Default Format", "combo", ["ask", "jpg", "png"]),
        ("default_dedupe_mode",         "Find Duplicates: Default Mode", "combo", ["ask", "keep one copy", "delete all"]),
        ("default_dedupe_hash",         "Find Duplicates: Hash Method",  "combo", ["exact", "perceptual"]),
        ("phash_threshold",             "Find Duplicates: Similarity Threshold", "combo", ["0", "2", "4", "6", "8", "10", "12", "16"]),
        ("zip_extract_input",           "Unpack Zip Files in Input",     "check", None),
    ]

    advanced_fields = [
        ("allow_concurrent_jobs", "Concurrent Operations",          "check", None),
        ("sort_output",           "Sort Output by Operation",       "check", None),
        ("default_dpi",            "Default DPI (PDF to Images)",     "combo", ["ask","72","96","150","200","300","600"]),
        ("throttle_cpu", "Max CPU % (0 = off)",    "combo", ["0","50","60","70","80","90"]),
        ("throttle_mem", "Max Memory % (0 = off)", "combo", ["0","50","60","70","80","90"]),
    ]

    hotkey_fields = [
        ("hotkey_continue", "Continue Hotkey", "combo", ["Return","space","Right"]),
        ("hotkey_cancel",   "Cancel Hotkey",   "combo", ["Escape","space","Left"]),
    ]

    vars_ = {}

    def build_fields(page, fields, start_row=1):
        for i, (key, label, typ, opts) in enumerate(fields):
            row = start_row + i
            Label(page, text=label, anchor='w', bg=t["bg"], font=('', 10)).grid(
                row=row, column=0, sticky='w', pady=4, padx=(0, 16))
            info_btn(page, row, key, PREF_TIPS)
            if typ == "check":
                default = True if key in ("guide_empty_input", "show_tooltips", "show_folder_stats", "zip_extract_input", "keep_documents_unarchived", "confirm_clear_output", "ask_run_name") else False
                v = tk.BooleanVar(value=bool(app.config.get(key, default)))
                check_btn(page, v).grid(row=row, column=1, sticky='w')
            elif typ == "entry":
                v = tk.StringVar(value=str(app.config.get(key, opts)))
                tk.Entry(page, textvariable=v, width=8, font=('', 10),
                         bg=t["entry_bg"], fg=t["entry_fg"], insertbackground=t["entry_fg"],
                         relief='flat', highlightthickness=1,
                         highlightbackground=t["entry_bg"], highlightcolor=t["entry_bg"]
                         ).grid(row=row, column=1, sticky='w')
            else:
                raw_val = str(app.config.get(key, opts[0]))
                if raw_val not in opts:
                    opts = list(opts) + [raw_val]      # keep the real value instead of replacing it
                v = tk.StringVar(value=raw_val)
                FlatDropdown(page, v, opts, t, win).grid(row=row, column=1, sticky='w')
            vars_[key] = v

    p = pages["General"]
    Label(p, text="General", font=('', 10, 'bold'), bg=t["bg"]).grid(
        row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
    build_fields(p, general_fields)

    p = pages["Tools"]
    Label(p, text="Tools", font=('', 10, 'bold'), bg=t["bg"]).grid(
        row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
    build_fields(p, tools_fields)

    p = pages["Hotkeys"]
    Label(p, text="Hotkeys", font=('', 10, 'bold'), bg=t["bg"]).grid(
        row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
    build_fields(p, hotkey_fields)
    return vars_, advanced_fields, build_fields


def _build_buttons_page(app, t, pages, Label, check_btn):
    # ── BUTTONS tab ────────────────────────────────────────────────────────
    p = pages["Buttons"]
    Label(p, text="Visible Buttons", font=('', 10, 'bold'), bg=t["bg"]).grid(
        row=0, column=0, columnspan=2, sticky='w', pady=(0, 4))
    Label(p, text="Toggle which tool buttons are visible in the sidebar.", fg=t["hint_fg"],
             bg=t["bg"], font=('', 10)).grid(row=1, column=0, columnspan=2, sticky='w', pady=(0, 8))

    btn_vars = {}
    for i, (key, section, tool_id) in enumerate(app.TOGGLEABLE):
        v = tk.BooleanVar(value=bool(app.config.get(key, True)))
        Label(p, text=f"{app._tool_label(tool_id)}  ({section})", anchor='w', bg=t["bg"], font=('', 10)).grid(
            row=i + 2, column=0, sticky='w', pady=3)
        check_btn(p, v).grid(row=i + 2, column=1, sticky='w')
        btn_vars[key] = v
    return btn_vars


def _build_themes_page(app, t, pages, Label):
    # ── THEMES tab ────────────────────────────────────────────────────────
    p = pages["Themes"]
    Label(p, text="Themes", font=('', 10, 'bold'), bg=t["bg"]).grid(
        row=0, column=0, columnspan=4, sticky='w', pady=(0, 4))
    Label(p, text="Customise colours for light and dark mode. Type a 6-digit hex code.",
             fg=t["hint_fg"], bg=t["bg"], font=('', 10)).grid(
        row=1, column=0, columnspan=4, sticky='w', pady=(0, 8))

    theme_vars = {}
    hex_re = re.compile(r'^#[0-9a-fA-F]{6}$')

    headers = ["Key", "Light", "", "Dark", ""]
    for col, h in enumerate(headers):
        Label(p, text=h, font=('', 10, 'bold'), bg=t["bg"], fg=t["hint_fg"]).grid(
            row=2, column=col, padx=(0 if col == 0 else 4, 0), sticky='w')

    saved_themes = app.config.get("themes", {})

    for i, key in enumerate(THEMES["light"].keys()):
        row = i + 3
        Label(p, text=KEY_LABELS.get(key, key), anchor='w', bg=t["bg"], font=('', 10)).grid(
            row=row, column=0, sticky='w', pady=2, padx=(0, 12))

        for col, mode in ((1, "light"), (3, "dark")):
            default = THEMES[mode][key]
            current = saved_themes.get(mode, {}).get(key, default)
            v = tk.StringVar(value=current)
            theme_vars.setdefault(mode, {})[key] = v

            e = tk.Entry(p, textvariable=v, width=9, font=('Courier', 10),
                         bg=t["entry_bg"], fg=t["entry_fg"], insertbackground=t["entry_fg"],
                         relief='flat', highlightthickness=1,
                     highlightbackground=t["entry_bg"], highlightcolor=t["entry_bg"])
            e.grid(row=row, column=col, sticky='w', padx=(0, 2))

            swatch = Label(p, width=2, bg=current, relief='flat')
            swatch.grid(row=row, column=col + 1, padx=(0, 8))

            def update_swatch(var=v, sw=swatch):
                val = var.get()
                if hex_re.match(val):
                    sw.configure(bg=val)

            v.trace_add("write", lambda *_, var=v, sw=swatch: update_swatch(var, sw))

    def reset_themes():
        for mode, keys in theme_vars.items():
            for key, v in keys.items():
                v.set(THEMES[mode][key])

    bottom_row = len(THEMES["light"].keys()) + 3
    reset_btn = Label(p, text="Reset to Defaults", bg=t["bg"], fg=t["fg"],
                         font=('', 10), cursor="hand2", padx=4)
    reset_btn.grid(row=bottom_row, column=0, columnspan=5, sticky='w', pady=(8, 0))
    reset_btn.bind("<Button-1>", lambda e: reset_themes())
    reset_btn.bind("<Enter>", lambda e: reset_btn.configure(bg=t["hover"]))
    reset_btn.bind("<Leave>", lambda e: reset_btn.configure(bg=t["bg"]))
    return theme_vars, hex_re


def _build_advanced_page(t, pages, Label, build_fields, advanced_fields):
    p = pages["Advanced"]

    Label(p, text="Advanced", font=('', 10, 'bold'), bg=t["bg"]).grid(
        row=0, column=0, columnspan=2, sticky='w', pady=(0, 8))
    Label(p, text="Limit resource usage during jobs.", fg=t["hint_fg"],
          bg=t["bg"], font=('', 10)).grid(row=1, column=0, columnspan=2, sticky='w', pady=(0, 8))
    build_fields(p, advanced_fields, start_row=2)


def show_preferences(app):
    existing = getattr(app, "_prefs_win", None)
    if existing is not None and existing.winfo_exists():     # already open: don't open a second copy
        existing.lift()
        existing.focus_force()
        return

    t = app._theme()          # follows dark mode and any custom colours (it used to be hard-wired to light)

    def Label(parent, **kw):
        """tk.Label that defaults to the current theme's colours."""
        kw.setdefault("bg", t["bg"])
        kw.setdefault("fg", t["fg"])
        return tk.Label(parent, **kw)

    win = tk.Toplevel(app.root)
    app._prefs_win = win
    win.title("Preferences")
    win.resizable(True, True)
    win.geometry("640x580")
    win.configure(bg=t["bg"])

    outer = tk.Frame(win, bg=t["bg"])
    outer.pack(fill='both', expand=True)

    sidebar = tk.Frame(outer, width=110, bg=t["btn_bg"])
    sidebar.pack(side='left', fill='y')
    sidebar.pack_propagate(False)

    content_area = tk.Frame(outer, bg=t["bg"])
    content_area.pack(side='left', fill='both', expand=True)

    pages = {}
    for name in ("Paths", "General", "Tools", "Hotkeys", "Buttons", "Themes", "Advanced"):
        f = tk.Frame(content_area, bg=t["bg"], padx=16, pady=12)
        f.grid(row=0, column=0, sticky='nsew')
        pages[name] = f

    content_area.grid_rowconfigure(0, weight=1)
    content_area.grid_columnconfigure(0, weight=1)

    tab_lbls = {}
    active_tab = {"name": None}

    def show_tab(name):
        active_tab["name"] = name
        pages[name].tkraise()
        for n, lbl in tab_lbls.items():
            lbl.configure(bg=t["hover"] if n == name else t["btn_bg"], fg=t["fg"])
        win.update_idletasks()

    Label(sidebar, text="Preferences", font=('', 10),
             bg=t["btn_bg"], fg=t["hint_fg"], pady=8).pack(fill='x')

    for name in pages:
        lbl = Label(sidebar, text=name, anchor='w', padx=12,
                       bg=t["btn_bg"], fg=t["fg"], font=('', 10),
                       cursor="hand2")
        lbl.pack(fill='x', ipady=7)
        lbl.bind("<Button-1>", lambda e, n=name: show_tab(n))
        lbl.bind("<Enter>", lambda e, l=lbl, n=name: l.configure(
            bg=t["bg"] if active_tab["name"] == n else t["hover"]))
        lbl.bind("<Leave>", lambda e, l=lbl, n=name: l.configure(
            bg=t["bg"] if active_tab["name"] == n else t["btn_bg"]))
        tab_lbls[name] = lbl

    tk.Frame(sidebar, bg=t["btn_bg"]).pack(fill='both', expand=True)

    paths = {
        "input":  tk.StringVar(value=app.config.get("input", "")),
        "output": tk.StringVar(value=app.config.get("output", "")),
    }
    lbl_vals = {}

    def pick(key):
        chosen = filedialog.askdirectory(title=f"Select {key} directory")
        if chosen:
            paths[key].set(chosen)
            refresh()

    def clear_path(key):
        paths[key].set("")
        refresh()

    def refresh():
        for key in ("input", "output"):
            val = paths[key].get()
            lbl_vals[key].configure(state='normal')
            lbl_vals[key].delete(0, tk.END)
            lbl_vals[key].insert(0, val if val else "")
            lbl_vals[key].configure(state='readonly')

    _tip = [None]

    def _show_pref_tip(widget, text):
        if _tip[0]:
            _tip[0].destroy()
        x = widget.winfo_rootx() - win.winfo_rootx() + widget.winfo_width() + 4
        y = widget.winfo_rooty() - win.winfo_rooty()
        lbl = Label(win, text=text, wraplength=220, justify='left',
                       font=('Courier', 10), bg=t["bg"], fg=t["fg"], padx=6, pady=4)
        lbl.place(x=x, y=y)
        lbl.lift()
        _tip[0] = lbl

    def _hide_pref_tip():
        if _tip[0]:
            _tip[0].destroy()
            _tip[0] = None

    def _pref_btn(parent, text, cmd):
        lbl = Label(parent, text=text, bg=t["bg"], fg=t["fg"],
                       font=('', 10), padx=6, cursor="hand2")
        lbl.bind("<Button-1>", lambda e: cmd())
        lbl.bind("<Enter>", lambda e: lbl.configure(bg=t["hover"]))
        lbl.bind("<Leave>", lambda e: lbl.configure(bg=t["bg"]))
        return lbl

    def _check_btn(parent, var):
        lbl = Label(parent, text="✓" if var.get() else " ",
                       bg=t["btn_bg"], fg=t["fg"], font=('', 10),
                       width=2, cursor="hand2")
        def toggle(e):
            var.set(not var.get())
            lbl.configure(text="✓" if var.get() else " ")
        lbl.bind("<Button-1>", toggle)
        lbl.bind("<Enter>", lambda e: lbl.configure(bg=t["hover"]))
        lbl.bind("<Leave>", lambda e: lbl.configure(bg=t["btn_bg"]))
        return lbl

    def _info_btn(parent, row, key, tips_dict, col=2):
        if key in tips_dict and app.config.get("show_tooltips", True):
            info = Label(parent, text="i", bg=t["bg"], fg=t["hint_fg"],
                            font=('', 10), cursor="hand2", padx=2)
            info.grid(row=row, column=col, sticky='w')
            info.bind("<Enter>", lambda e, w=info, txt=tips_dict[key]: (
                w.configure(bg=t["hover"]), _show_pref_tip(w, txt)))
            info.bind("<Leave>", lambda e, w=info: (
                w.configure(bg=t["bg"]), _hide_pref_tip()))


    paths = _build_paths_page(app, t, pages, Label, paths, lbl_vals, pick, clear_path, _pref_btn, _show_pref_tip, _hide_pref_tip)


    vars_, advanced_fields, build_fields = _build_primary_pages(app, t, win, pages, Label, _info_btn, _check_btn)


    btn_vars = _build_buttons_page(app, t, pages, Label, _check_btn)


    theme_vars, hex_re = _build_themes_page(app, t, pages, Label)


    _build_advanced_page(t, pages, Label, build_fields, advanced_fields)

    # ── Immediate settings ─────────────────────────────────────────────────
    def _collect_themes(show_warning=False):
        """Returns {mode: {key: colour}} containing ONLY colours that differ from the built-in defaults
        (so future default improvements still reach you), or None if a hex code is invalid."""
        bad = []
        for mode, keys in theme_vars.items():
            for key, v in keys.items():
                val = v.get().strip()
                if not hex_re.match(val):
                    bad.append(f"{mode}/{key}: '{val}'")
        if bad and show_warning:
            messagebox.showwarning("Invalid Hex", "Fix these:\n" + "\n".join(bad), parent=win)
        if bad:
            return None
        custom = {}
        for mode, keys in theme_vars.items():
            changed = {k: v.get().strip() for k, v in keys.items()
                       if v.get().strip().lower() != THEMES[mode][k].lower()}
            if changed:
                custom[mode] = changed
        return custom

    pending = {}
    last_raw = {key: var.get() for key, var in vars_.items()}

    def _normalise(key, raw):
        if key in ("auto_clear_input", "confirm_clear_input", "confirm_clear_output", "replace_output", "sort_output", "ask_run_name",
                   "guide_empty_input", "show_tooltips", "allow_concurrent_jobs", "log_default_expanded",
                   "show_timestamps", "open_output_recent", "log_blank_lines", "show_folder_stats",
                   "zip_extract_input", "keep_documents_unarchived"):
            return bool(raw)
        if key in ("throttle_cpu", "throttle_mem"):
            return _safe_int(raw, 80, 0, 100)
        if key == "default_dpi":
            return raw if raw == "ask" else _safe_int(raw, 72, 10, 1200)
        if key == "min_free_gb":
            return _safe_int(raw, 10, 0)
        if key == "preview_limit":
            return _safe_int(raw, 10, 0)
        if key == "phash_threshold":
            return _safe_int(raw, 4, 0, 64)
        if key == "default_sort":
            return raw if raw in ("natural", "none") else "natural"
        return raw

    def _apply_side_effects(key):
        if key == "default_doc_format":
            app.current_doc_format = app.config[key]
            app._validate_production_formats()
            app._rebuild_buttons()
            app._update_format_selector()
        elif key == "zip_compression":
            app.current_folder_format = app.config[key]
            app._validate_production_formats()
            app._rebuild_buttons()
            app._update_format_selector()
        elif key in ("show_tooltips", "guide_empty_input"):
            app._rebuild_buttons()
        elif key in {item[0] for item in app.TOGGLEABLE}:
            app._rebuild_buttons()
        else:
            app._update_button_states()

    def _persist_setting(key, raw):
        if key in ("hotkey_continue", "hotkey_cancel"):
            other = "hotkey_cancel" if key == "hotkey_continue" else "hotkey_continue"
            if raw == vars_[other].get():
                messagebox.showwarning("Hotkeys", "The Continue and Cancel hotkeys must be different keys.", parent=win)
                vars_[key].set(last_raw[key])
                return
        app.config[key] = _normalise(key, raw)
        last_raw[key] = raw
        save_config(app.config)
        _apply_side_effects(key)

    def _queue_setting(key, raw):
        if key in ("preview_limit",):
            if key in pending:
                win.after_cancel(pending[key])
            def commit(k=key, value=raw):
                pending.pop(k, None)
                _persist_setting(k, value)
            pending[key] = win.after(350, commit)
        else:
            _persist_setting(key, raw)

    for key, variable in vars_.items():
        variable.trace_add("write", lambda *_, k=key, v=variable: _queue_setting(k, v.get()))
    for key, variable in paths.items():
        def apply_path(*_, k=key, v=variable):
            value = v.get().strip()
            if value and not Path(value).is_dir():
                return
            app.config[k] = value
            save_config(app.config)
            app._update_button_states()
        variable.trace_add("write", apply_path)
    for key, variable in btn_vars.items():
        variable.trace_add("write", lambda *_, k=key, v=variable: _persist_setting(k, bool(v.get())))

    def apply_themes(*_):
        custom = _collect_themes()
        if custom is None:
            return
        app.config["themes"] = custom
        save_config(app.config)
        app._apply_theme()
    for keys in theme_vars.values():
        for variable in keys.values():
            variable.trace_add("write", apply_themes)

    close_btn = Label(sidebar, text="Close", anchor='w', padx=12,
                      bg=t["btn_bg"], fg=t["fg"], font=('', 10), cursor="hand2")
    close_btn.pack(fill='x', ipady=7, side='bottom')
    close_btn.bind("<Button-1>", lambda e: win.destroy())
    close_btn.bind("<Enter>", lambda e: close_btn.configure(bg=t["hover"]))
    close_btn.bind("<Leave>", lambda e: close_btn.configure(bg=t["btn_bg"]))

    show_tab("Paths")
