#LOG——————————————————————————————————————————————————————————————————————————————————————————————————
import io
import time
import queue
import tkinter as tk

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


