#LOG——————————————————————————————————————————————————————————————————————————————————————————————————
import io
import time
import queue
import tkinter as tk

import re
import sys as _sys
from decimal import Decimal, InvalidOperation


def natural_sort_key(name):
    """Sort path components hierarchically, treating decimal number chunks numerically."""
    def component_key(component):
        tokens = []
        for chunk in re.split(r"(\d+(?:\.\d+)?)", component):
            if not chunk:
                continue
            if re.fullmatch(r"\d+(?:\.\d+)?", chunk):
                try:
                    tokens.append((0, Decimal(chunk)))
                except InvalidOperation:
                    tokens.append((1, chunk.casefold()))
            else:
                tokens.append((1, chunk.casefold()))
        return tuple(tokens)

    parts = str(name).replace("\\", "/").split("/")
    return tuple(component_key(part) for part in parts)


# One pair of symbols used for EVERY collapsible thing (sidebar tools and log sections):
# ▶ = collapsed (click to open), ▼ = open (click to close).
EXPAND   = "▶"
COLLAPSE = "▼"


class LogRedirect(io.TextIOBase):
    """Replaces sys.stdout / sys.stderr and shows everything in the log widget.

    IMPORTANT: Tkinter may only be touched from the main thread, but the tools run in worker
    threads. So write() and friends never touch the widget. They only drop a request into a
    queue, and the main thread calls drain() many times a second to do the actual drawing.
    """
    REPEAT_THRESHOLD = 1

    def __init__(self, log_widget, app):
        self.log = log_widget
        self.app = app
        self._pending = queue.SimpleQueue()   # thread-safe
        self._last_msg = None
        self._rep_count = 0
        self._active_section = None
        self._tag_counter = 0

    # ── safe to call from ANY thread ──────────────────────────────────────────

    def write(self, msg):
        self._pending.put(("write", msg))
        return len(msg)

    def flush(self):
        pass

    def start_section(self, header):
        self._pending.put(("start_section", header))

    def end_section(self):
        self._pending.put(("end_section", None))

    def write_with_preview(self, msg, image_path):
        self._pending.put(("preview", (msg, image_path)))

    def header(self, text):
        """Timestamp / divider block printed before a job."""
        self._pending.put(("header", text))

    def clear(self):
        self._pending.put(("clear", None))

    # ── MAIN THREAD ONLY ──────────────────────────────────────────────────────

    def drain(self, budget=0.02):
        """Draw everything that is waiting. Stops after `budget` seconds so the window stays responsive."""
        if self._pending.empty():
            return
        deadline = time.monotonic() + budget
        self.log.configure(state='normal')
        try:
            while time.monotonic() < deadline:
                try:
                    op, arg = self._pending.get_nowait()
                except queue.Empty:
                    break
                getattr(self, "_do_" + op)(arg)
            self.log.see(tk.END)
        finally:
            self.log.configure(state='disabled')

    def _next_tag(self, prefix):
        self._tag_counter += 1
        return f"{prefix}_{self._tag_counter}"

    def _do_clear(self, _):
        self.log.delete('1.0', tk.END)
        # Remove the per-line tags too, otherwise they pile up forever.
        for tag in self.log.tag_names():
            if tag.startswith(("section_", "hide_", "preview_")):
                self.log.tag_delete(tag)
        self._last_msg = None
        self._rep_count = 0
        self._active_section = None

    def _do_header(self, text):
        self.log.tag_configure("ts_dim", foreground=self.app._theme()["log_dim"])
        self.log.insert(tk.END, text, ("ts_dim",))
        # A new job starts here: forget any section a crashed job left open, and the repeat counter.
        self._active_section = None
        self._last_msg = None

    def _do_start_section(self, header):
        self._active_section = None
        self._last_msg = None

        tag = self._next_tag("section")
        hide_tag = f"hide_{tag}"
        expanded = self.app.config.get("log_default_expanded", False)

        arrow = COLLAPSE if expanded else EXPAND
        self.log.tag_configure(tag, foreground=self.app._theme()["log_fg"])
        self.log.insert(tk.END, f"{arrow} {header}\n", (tag,))
        self.log.tag_configure(hide_tag, elide=not expanded)

        self.log.tag_bind(tag, "<Button-1>", lambda e, t=tag, h=hide_tag: self._toggle(t, h))
        self.log.tag_bind(tag, "<Enter>", lambda e: self.log.configure(cursor="hand2"))
        self.log.tag_bind(tag, "<Leave>", lambda e: self.log.configure(cursor=""))

        self._active_section = {"tag": tag, "hide_tag": hide_tag, "expanded": expanded}

    def _do_end_section(self, _):
        self._active_section = None
        self._last_msg = None

    def _do_preview(self, args):
        msg, image_path = args
        self._last_msg = None
        hide_tag = self._active_section["hide_tag"] if self._active_section else None

        tag = self._next_tag("preview")
        self.log.tag_configure(tag, foreground=self.app._theme()["log_dim"])

        insert_tags = tuple(t for t in (hide_tag, tag) if t)
        indent = len(msg) - len(msg.lstrip())
        if indent and hide_tag:
            self.log.insert(tk.END, msg[:indent], (hide_tag,))
            self.log.insert(tk.END, msg[indent:] + "\n", insert_tags)
        else:
            self.log.insert(tk.END, msg + "\n", insert_tags)

        def show_preview(e, path=image_path):
            def _show(path=path):
                if not hasattr(self.app, '_preview_after') or self.app._preview_after is None:
                    return
                self.app._preview_after = None
                try:
                    from PIL import Image, ImageTk
                    img = Image.open(path)
                    img.thumbnail((300, 400))
                    photo = ImageTk.PhotoImage(img)

                    if hasattr(self.app, '_preview_popup') and self.app._preview_popup:
                        self.app._preview_popup.destroy()

                    popup = tk.Toplevel(self.app.root)
                    popup.wm_overrideredirect(True)
                    mx = self.app.root.winfo_pointerx()
                    my = self.app.root.winfo_pointery()
                    px = min(mx + 10, self.app.root.winfo_screenwidth() - 320)
                    py = min(my, self.app.root.winfo_screenheight() - 420)
                    popup.wm_geometry(f"+{px}+{py}")

                    lbl = tk.Label(popup, image=photo, bg="#000000")
                    lbl.image = photo
                    lbl.pack()

                    self.app._preview_popup = popup
                except Exception:
                    pass

            if hasattr(self.app, '_preview_after') and self.app._preview_after:
                self.app.root.after_cancel(self.app._preview_after)
            self.app._preview_after = self.app.root.after(150, _show)

        def hide_preview(e):
            if hasattr(self.app, '_preview_after') and self.app._preview_after:
                self.app.root.after_cancel(self.app._preview_after)
                self.app._preview_after = None
            if hasattr(self.app, '_preview_popup') and self.app._preview_popup:
                self.app._preview_popup.destroy()
                self.app._preview_popup = None

        self.log.tag_bind(tag, "<Enter>", lambda e: (show_preview(e), self.log.configure(cursor="hand2")))
        self.log.tag_bind(tag, "<Leave>", lambda e: (hide_preview(e), self.log.configure(cursor="")))

    def _toggle(self, tag, hide_tag):
        """Click handler for a section header (runs on the main thread)."""
        self.log.configure(state='normal')
        currently_elided = self.log.tag_cget(hide_tag, "elide")
        is_hidden = str(currently_elided) in ("1", "True", "true")
        self.log.tag_configure(hide_tag, elide=not is_hidden)

        ranges = self.log.tag_ranges(tag)
        if ranges:
            start = ranges[0]
            first = self.log.get(start, f"{start}+1c")
            if first in (EXPAND, COLLAPSE):
                self.log.delete(start, f"{start}+1c")
                self.log.insert(start, COLLAPSE if first == EXPAND else EXPAND, (tag,))

        self.log.configure(state='disabled')

    def _style_tag_for(self, msg):
        t = self.app._theme()
        s = msg.strip()

        if s.startswith("✖") or "Failed" in s:
            tag = "log_error"
            cfg = {"foreground": t["log_error"]}
        elif s.startswith("⚠") or "skipped (unsupported type)" in s:
            tag = "log_warn"
            cfg = {"foreground": t["log_warn"]}
        elif s.startswith("Done!") or s.startswith("→") or "Done!" in s:
            tag = "log_success"
            cfg = {"foreground": t["log_success"]}
        elif (s.startswith("Saved:") or s.startswith("converted:") or
              s.startswith("Total:") or s.startswith("PDFs saved:") or
              s.startswith("combined:") or s.startswith("renamed:") or s.startswith("EPUBs saved:") or
              s.startswith("copied:") or s.startswith("pages exported:") or
              s.startswith("scanned:") or s.startswith("extracted:")):
            tag = "log_bold"
            cfg = {}
        elif msg.startswith("    ") and self._active_section:
            tag = "log_dim"
            cfg = {"foreground": t["log_dim"]}
        else:
            return None

        self.log.tag_configure(tag, **cfg)
        return tag

    def _do_write(self, msg):
        msg = msg.rstrip('\n')
        hide_tag = self._active_section["hide_tag"] if self._active_section else None

        if not msg.strip():
            if self.app.config.get("log_blank_lines", False):
                self.log.insert(tk.END, '\n', (hide_tag,) if hide_tag else ())
                self._last_msg = None   # a blank line now sits between messages: don't merge repeats across it
            return

        style_tag = self._style_tag_for(msg)
        insert_tags = tuple(t for t in (hide_tag, style_tag) if t)

        if msg == self._last_msg:
            self._rep_count += 1
            if self._rep_count >= self.REPEAT_THRESHOLD:
                self.log.delete("end-2l", "end-1l")
                self.log.insert(tk.END, f"{msg}  ×{self._rep_count}\n", insert_tags)
                return
        else:
            self._last_msg = msg
            self._rep_count = 1

        self.log.insert(tk.END, msg + '\n', insert_tags)


# ── input() replacement ───────────────────────────────────────────────────────
# The tools still call input(). Each call gets its OWN reply queue, so two jobs asking
# questions at the same time can never receive each other's answers.

input_queue = queue.Queue()


def thread_safe_input(prompt=""):
    reply = queue.Queue()
    input_queue.put((prompt, reply))
    return reply.get()


def patch_input():
    import builtins
    builtins.input = thread_safe_input


def _get_log_section_fns():
    import sys as _sys
    log = _sys.stdout
    if hasattr(log, 'start_section') and hasattr(log, 'end_section'):
        return log.start_section, log.end_section
    return lambda header: None, lambda: None


# ══ LOG EXPANSION FRAMEWORK ═══════════════════════════════════════════════════
# Every tool describes what it is working on the same way:
#   [Folder]  12 image(s)  ·  34.5 MB          <- always visible (the ▶ header)
#       page01.jpg                              <- shown when expanded, at most
#       page02.jpg                                 "preview_limit" lines, then
#       … and 40 more                              "… and N more"
# and a tool's own preview ("Preview", "Excluding", "Pages per PDF", ...) uses the same
# limited list. Both are controlled from Preferences.

DEFAULT_PREVIEW_LIMIT = 10
ROOT_LABEL = "(Input root)"          # label for files that sit directly in Input, not in a folder


def _int_cfg(config, key, default):
    """Read a whole number from the config. Anything unusable (text, blank, a hand-edited file) gives `default`."""
    try:
        return int(str(config.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def get_preview_limit(config):
    """How many lines a Log expansion may show. 0 means unlimited. Bad values fall back to 10."""
    n = _int_cfg(config, "preview_limit", DEFAULT_PREVIEW_LIMIT)
    return n if n >= 0 else DEFAULT_PREVIEW_LIMIT


def _fmt_size(num_bytes):
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def _total_size(files):
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return total


def _size_suffix(config, files):
    """'  ·  34.5 MB', or '' when folder stats are switched off in Preferences."""
    if not config.get("show_folder_stats", True):
        return ""
    return f"  ·  {_fmt_size(_total_size(files))}"


def _stats_text(config, files, noun):
    if not config.get("show_folder_stats", True):
        return ""
    return f"  {len(files)} {noun}(s){_size_suffix(config, files)}"


def _rel(path, base):
    try:
        return str(path.relative_to(base))
    except (ValueError, TypeError):
        return path.name


class _Section:
    """A collapsible Log section that lists at most `preview_limit` lines, then '… and N more'.

        with _Section(config, "  Preview (120)") as s:
            for ... : s.add("    a  →  b")

    Do NOT print anything else while one is open: it would be hidden inside the section.
    Outside the GUI (plain console) the header is simply printed as a normal line."""

    def __init__(self, config, header, unlimited=False):
        self.limit = 0 if unlimited else get_preview_limit(config)
        self.shown = 0
        self.extra = 0
        start, self._end = _get_log_section_fns()
        if not hasattr(_sys.stdout, "start_section"):
            print(header)
        start(header)

    def add(self, line, preview_path=None):
        if self.limit and self.shown >= self.limit:
            self.extra += 1
            return
        self.shown += 1
        log = _sys.stdout
        if preview_path is not None and hasattr(log, "write_with_preview"):
            log.write_with_preview(line, preview_path)
        else:
            print(line)

    def close(self):
        if self.extra:
            print(f"    … and {self.extra} more  (list size is set in Preferences)")
        self._end()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _log_group(config, label, files, base=None, noun="image", extra="", names=None,
               numbers=None, unlimited=False):
    """One folder in the Log: a header with the count and size, and the file names when expanded.
    A group with nothing to list is a plain line, so there is never an expander that opens onto nothing."""
    header = f"  [{label}]{_stats_text(config, files, noun)}{extra}"
    lines = names if names is not None else [_rel(f, base) if base else f.name for f in files]
    if not lines:
        print(header)
        return
    with _Section(config, header, unlimited) as s:
        for i, line in enumerate(lines):
            s.add(f"    {numbers[i]}. {line}" if numbers else f"    {line}")


def _group_by_top_folder(files, src):
    """[(label, files, base_folder), ...] using the first folder level under Input."""
    groups = {}
    for f in files:
        parts = f.relative_to(src).parts
        groups.setdefault(parts[0] if len(parts) > 1 else ROOT_LABEL, []).append(f)
    return [(label, fl, src if label == ROOT_LABEL else src / label) for label, fl in groups.items()]


def _log_scan(config, files, src, noun="image", numbering=None, details=None, unlimited=False):
    """Show every folder the tool is working with, using the shared format.
    numbering = {path: number} prints '12. name' and lists everything (needed to pick files by number).
    details   = {path: text}   appends '  —  text' to a file's line."""
    if numbering is None and config.get("default_sort", "natural") != "none":          # keep the display in natural order (numbered lists keep their numbers)
        files = sorted(files, key=lambda x: natural_sort_key(_rel(x, src)))
    groups = _group_by_top_folder(files, src)
    for label, fl, base in groups:
        names = [_rel(f, base) + (f"  —  {details[f]}" if details and f in details else "") for f in fl]
        numbers, extra = None, ""
        if numbering:
            numbers = [numbering[f] for f in fl]
            if numbers == list(range(numbers[0], numbers[0] + len(numbers))):   # consecutive: show the range
                extra = f"  ·  #{numbers[0]}–#{numbers[-1]}"
        _log_group(config, label, fl, base, noun=noun, names=names, numbers=numbers,
                   extra=extra, unlimited=unlimited)
    if len(groups) > 1:
        print(f"  Total: {len(files)} {noun}(s) in {len(groups)} folder(s){_size_suffix(config, files)}")

