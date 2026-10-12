"""Plain-text and collapsible formatting for operation logs."""

import sys as _sys

from ManagerConfig import resolve_sort
from Util import friendly_error, fmt_size, int_cfg, natural_sort_key, rel, total_size

def section_callbacks():
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


def preview_limit(config):
    """How many lines a Log expansion may show. 0 means unlimited. Bad values fall back to 10."""
    n = int_cfg(config, "preview_limit", DEFAULT_PREVIEW_LIMIT)
    return n if n >= 0 else DEFAULT_PREVIEW_LIMIT


def size_suffix(config, files):
    """'  ·  34.5 MB', or '' when folder stats are switched off in Preferences."""
    if not config.get("show_folder_stats", True):
        return ""
    return f"  ·  {fmt_size(total_size(files))}"


def stats_text(config, files, noun):
    if not config.get("show_folder_stats", True):
        return ""
    return f"  {len(files)} {noun}(s){size_suffix(config, files)}"



class Section:
    """A collapsible Log section that lists at most `preview_limit` lines, then '… and N more'.

        with Section(config, "  Preview (120)") as s:
            for ... : s.add("    a  →  b")

    Do NOT print anything else while one is open: it would be hidden inside the section.
    Outside the GUI (plain console) the header is simply printed as a normal line."""

    def __init__(self, config, header, unlimited=False):
        self.limit = 0 if unlimited else preview_limit(config)
        self.shown = 0
        self.extra = 0
        start, self._end = section_callbacks()
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


def log_group(config, label, files, base=None, noun="image", extra="", names=None,
               numbers=None, unlimited=False):
    """One folder in the Log: a header with the count and size, and the file names when expanded.
    A group with nothing to list is a plain line, so there is never an expander that opens onto nothing."""
    header = f"  [{label}]{stats_text(config, files, noun)}{extra}"
    lines = names if names is not None else [rel(f, base) if base else f.name for f in files]
    if not lines:
        print(header)
        return
    with Section(config, header, unlimited) as s:
        for i, line in enumerate(lines):
            s.add(f"    {numbers[i]}. {line}" if numbers else f"    {line}")


def group_by_top_folder(files, src):
    """[(label, files, base_folder), ...] using the first folder level under Input."""
    groups = {}
    for f in files:
        parts = f.relative_to(src).parts
        groups.setdefault(parts[0] if len(parts) > 1 else ROOT_LABEL, []).append(f)
    return [(label, fl, src if label == ROOT_LABEL else src / label) for label, fl in groups.items()]


def log_scan(config, files, src, noun="image", numbering=None, details=None, unlimited=False):
    """Show every folder the tool is working with, using the shared format.
    numbering = {path: number} prints '12. name' and lists everything (needed to pick files by number).
    details   = {path: text}   appends '  —  text' to a file's line."""
    if numbering is None and resolve_sort(config):
        # Keep the display in natural order; numbered lists keep their chosen order.
        files = sorted(files, key=lambda x: natural_sort_key(rel(x, src)))
    groups = group_by_top_folder(files, src)
    for label, fl, base in groups:
        names = [rel(f, base) + (f"  —  {details[f]}" if details and f in details else "") for f in fl]
        numbers, extra = None, ""
        if numbering:
            numbers = [numbering[f] for f in fl]
            if numbers == list(range(numbers[0], numbers[0] + len(numbers))):   # consecutive: show the range
                extra = f"  ·  #{numbers[0]}–#{numbers[-1]}"
        log_group(config, label, fl, base, noun=noun, names=names, numbers=numbers,
                   extra=extra, unlimited=unlimited)
    if len(groups) > 1:
        print(f"  Total: {len(files)} {noun}(s) in {len(groups)} folder(s){size_suffix(config, files)}")



def print_summary(config, copied=0, failed=None, skipped=None, label="processed"):
    parts = [f"  {label}: {copied}"]
    if failed:
        parts.append(f"Failed: {len(failed)}")
    if skipped:
        parts.append(f"Skipped: {len(skipped)}")
    print("  " + "   ".join(parts))
    if failed:                                  # failures are never truncated: you need to see all of them
        with Section(config, f"  Failed files ({len(failed)})", unlimited=True) as s:
            for p, reason in failed:
                s.add(f"    {p.name}: {friendly_error(reason)}")
    if skipped:
        with Section(config, f"  Skipped files ({len(skipped)})") as s:
            for p, reason in skipped:
                s.add(f"    {p.name}: {reason}")



