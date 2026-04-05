#LOG——————————————————————————————————————————————————————————————————————————————————————————————————
import io
import tkinter as tk
import queue

class LogRedirect(io.TextIOBase):
    REPEAT_THRESHOLD = 1

    def __init__(self, log_widget, app):
        self.log = log_widget
        self.app = app
        self._last_msg = None
        self._rep_count = 0
        self._active_section = None

    def start_section(self, header):
        self._active_section = None

        tag = f"section_{id(header)}_{self.log.index(tk.END)}"
        hide_tag = f"hide_{tag}"
        expanded = self.app.config.get("log_default_expanded", False)

        self.log.configure(state='normal')
        arrow = "▲" if expanded else "▼"
        line = f"{arrow} {header}\n"

        self.log.tag_configure(tag, foreground=self.app._theme()["log_fg"])
        self.log.insert(tk.END, line, (tag,))
        self.log.tag_configure(hide_tag, elide=not expanded)

        self.log.tag_bind(tag, "<Button-1>", lambda e, t=tag, h=hide_tag: self._toggle(t, h))
        self.log.tag_bind(tag, "<Enter>", lambda e: self.log.configure(cursor="hand2"))
        self.log.tag_bind(tag, "<Leave>", lambda e: self.log.configure(cursor=""))

        self.log.see(tk.END)
        self.log.configure(state='disabled')

        self._active_section = {"tag": tag, "hide_tag": hide_tag, "expanded": expanded}
        return tag

    def end_section(self):
        self._active_section = None

    def write_with_preview(self, msg, image_path):
        self.log.configure(state='normal')
        hide_tag = self._active_section["hide_tag"] if self._active_section else None

        tag = f"preview_{id(image_path)}_{self.log.index(tk.END)}"
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

        self.log.see(tk.END)
        self.log.configure(state='disabled')

    def _toggle(self, tag, hide_tag):
        self.log.configure(state='normal')
        currently_elided = self.log.tag_cget(hide_tag, "elide")
        is_hidden = str(currently_elided) in ("1", "True", "true")
        new_elide = not is_hidden
        self.log.tag_configure(hide_tag, elide=new_elide)

        ranges = self.log.tag_ranges(tag)
        if ranges:
            start, end = ranges[0], ranges[1]
            text = self.log.get(start, end)
            if text.startswith("▼"):
                self.log.delete(start, f"{start}+1c")
                self.log.insert(start, "▲", (tag,))
            elif text.startswith("▲"):
                self.log.delete(start, f"{start}+1c")
                self.log.insert(start, "▼", (tag,))

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
              s.startswith("combined:") or s.startswith("renamed:") or
              s.startswith("copied:") or s.startswith("pages exported:") or
              s.startswith("scanned:")):
            tag = "log_bold"
            cfg = {}
        elif msg.startswith("    ") and self._active_section:
            tag = "log_dim"
            cfg = {"foreground": t["log_dim"]}
        else:
            return None

        self.log.tag_configure(tag, **cfg)
        return tag

    def write(self, msg):
        msg = msg.rstrip('\n')
        if not msg.strip():
            if self.app.config.get("log_blank_lines", False):
                self.log.configure(state='normal')
                hide_tag = self._active_section["hide_tag"] if self._active_section else None
                if hide_tag:
                    self.log.insert(tk.END, '\n', (hide_tag,))
                else:
                    self.log.insert(tk.END, '\n')
                self.log.see(tk.END)
                self.log.configure(state='disabled')
            return len(msg)
        self.log.configure(state='normal')

        style_tag = self._style_tag_for(msg)
        hide_tag = self._active_section["hide_tag"] if self._active_section else None
        insert_tags = tuple(t for t in (hide_tag, style_tag) if t)

        if msg == self._last_msg:
            self._rep_count += 1
            if self._rep_count >= self.REPEAT_THRESHOLD:
                self.log.delete("end-2l", "end-1l")
                self.log.insert(tk.END, f"{msg}  ×{self._rep_count}\n", insert_tags)
                self.log.see(tk.END)
                self.log.configure(state='disabled')
                return len(msg)
        else:
            self._last_msg = msg
            self._rep_count = 1

        self.log.insert(tk.END, msg + '\n', insert_tags)
        self.log.see(tk.END)
        self.log.configure(state='disabled')
        return len(msg)

    def flush(self):
        pass

input_queue  = queue.Queue()
result_queue = queue.Queue()


def thread_safe_input(prompt=""):
    input_queue.put(prompt)
    return result_queue.get()


def patch_input():
    import builtins
    builtins.input = thread_safe_input
