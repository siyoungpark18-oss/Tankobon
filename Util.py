"""Shared utilities used by Tankobon tools and output handling."""

import errno
import re
import sys
import time
from decimal import Decimal
from pathlib import Path

import psutil

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif"}
DOCUMENT_EXTENSIONS = {".pdf", ".epub"}
ARCHIVE_EXTENSIONS = {".zip", ".cbz"}
CBZ_IMAGE_EXTENSIONS = IMAGE_EXTENSIONS | {".webp"}
ALREADY_COMPRESSED = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".zip", ".cbz"}
EPUB_IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".gif": "image/gif", ".webp": "image/webp"}
READABLE_EPUB_IMAGES = set(EPUB_IMAGE_TYPES) | {".bmp", ".tif", ".tiff"}

_last_throttle_check = 0.0
psutil.cpu_percent(interval=None)  # Prime psutil's first CPU measurement.


def natural_sort_key(name):
    """Sort paths by components, treating dotted number runs as decimal values."""
    def component_key(component):
        tokens = []
        for chunk in re.split(r"(\d+(?:\.\d+)?)", component):
            if not chunk:
                continue
            if re.fullmatch(r"\d+(?:\.\d+)?", chunk):
                tokens.append((0, Decimal(chunk), chunk.casefold()))
            else:
                tokens.append((1, chunk.casefold(), ""))
        return tuple(tokens)

    parts = str(name).replace("\\", "/").split("/")
    return tuple(component_key(part) for part in parts)


def int_cfg(config, key, default):
    """Read an integer setting, falling back for blank or invalid values."""
    try:
        return int(str(config.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def fmt_size(num_bytes):
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def total_size(files):
    total = 0
    for file in files:
        try:
            total += file.stat().st_size
        except OSError:
            pass
    return total


def rel(path, base):
    try:
        return str(path.relative_to(base))
    except (ValueError, TypeError):
        return path.name


def is_no_space(error):
    quota_error = getattr(errno, "EDQUOT", -1)
    return isinstance(error, OSError) and error.errno in (errno.ENOSPC, quota_error)


def resolve_sort(config):
    """Whether tools should use the configured natural sort order."""
    return config.get("default_sort", "natural") != "none"


def get_poppler_path():
    """Locate bundled Poppler binaries in a PyInstaller build, if present."""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    return str(Path(bundle_dir) / "poppler" / "bin") if bundle_dir else None


def throttle_if_needed(config, cancel=None):
    """Pause a job while CPU or memory use exceeds its configured limits."""
    global _last_throttle_check
    cpu_limit = int_cfg(config, "throttle_cpu", 80)
    mem_limit = int_cfg(config, "throttle_mem", 80)
    if cpu_limit == 0 and mem_limit == 0:
        return
    now = time.monotonic()
    if now - _last_throttle_check < 0.5:
        return
    _last_throttle_check = now
    warned = False
    while True:
        if cancel is not None and cancel.is_set():
            return
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent
        if cpu <= cpu_limit and mem <= mem_limit:
            break
        if not warned:
            reasons = []
            if cpu > cpu_limit:
                reasons.append(f"CPU at {cpu:.0f}% (limit: {cpu_limit}%)")
            if mem > mem_limit:
                reasons.append(f"RAM at {mem:.0f}% (limit: {mem_limit}%)")
            print(f"  Paused — {', '.join(reasons)}. Free up resources or raise the throttle limit in Preferences.")
            warned = True
        time.sleep(0.5)
    if warned:
        print("  Resuming...")


def friendly_error(error):
    """Translate common file and storage failures into a suggested next step."""
    detail = str(error).strip()
    text = detail.lower()
    if "no space left" in text or "disk full" in text or "not enough space" in text:
        return "The disk is full. Free space on the output drive, then try again."
    if "permission denied" in text or "access is denied" in text or "operation not permitted" in text:
        return "Permission denied. Check folder access and close any app using the file, then try again."
    if "no such file or directory" in text or "cannot find the path" in text:
        return "The file or folder could not be found. Check that it still exists and try again."
    if any(term in text for term in ("not a valid zip", "badzipfile", "crc-32", "checksum", "cannot identify image", "unidentifiedimageerror", "truncated", "unexpected eof", "damaged", "corrupt")):
        return "The file appears to be corrupt or incomplete. Try opening it; if it will not open, replace it with a fresh copy."
    if any(term in text for term in ("pdfreaderror", "pdf read", "invalid pdf", "malformed pdf", " eof marker not found")):
        return "The PDF could not be read. Open it in a PDF viewer; if it will not open, replace it with a fresh copy."
    if detail:
        return f"Could not complete this file operation. Check that the file opens and the destination is writable, then try again. Details: {detail}"
    return "Could not complete this file operation. Check that the file opens and the destination is writable, then try again."
