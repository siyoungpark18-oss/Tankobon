#IMPORTS——————————————————————————————————————————————————————————————————————————————————————————————————
from PIL import Image
from pathlib import Path
import re
import shutil
import img2pdf
import json
import copy
import os
import hashlib
import time
import psutil
import errno
import zipfile
import tempfile
from pypdf import PdfReader, PdfWriter
import sys as _sys

def _get_poppler_path():
    base = getattr(_sys, '_MEIPASS', None)
    if base:
        return str(Path(base) / 'poppler' / 'bin')
    return None

_POPPLER_PATH = _get_poppler_path()

#PATHS————————————————————————————————————————————————————————————————————————————————————————————————————
CONFIG_PATH = Path.home() / ".tankobon" / "config.json"

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif'} #DEFAULT IMAGE EXTENSIONS FOR MOST TOOLS

SENTINEL = "\x00CANCELLED\x00"

#DEFAULTS——————————————————————————————————————————————————————————————————————————————————————————————————
DEFAULTS = { #The default configurations. These should all be able to change in preferences.py
    "input":                     "",
    "output":                    "",
    "default_sort":              "natural",
    "default_dpi":               "72",
    "default_img_fmt":           "ask",
    "default_pdf_to_images_fmt": "ask",
    "auto_clear_input":          False,
    "replace_output":            True,
    "sort_output":               False,
    "hotkey_continue":           "Return",
    "hotkey_cancel":             "Escape",
    "throttle_cpu":              80,
    "throttle_mem":              80,
    "dark_mode":                 False,
    "min_free_gb":               10,
    "log_default_expanded":      False,
    "ask_run_name":              False,
    "show_timestamps":           True,
    "open_output_recent":        False,
    "first_launch":              True,
    "log_blank_lines":           False,
    "ui_mode":                   "dropdown",
    "log_font_size":             8,
    "ui_font_size":              7,
    "default_images_to_pdf_mode": "ask",
    "default_folders_to_pdf_mode": "ask",
    "default_folder_renamer_mode": "ask",
    "default_file_renamer_mode":   "ask",
    "default_dedupe_mode":        "ask",
    "default_dedupe_hash":        "exact",
    "guide_empty_input":          True,
    "show_tooltips":              True,
    "allow_concurrent_jobs":      False,
    "themes":                     {},
    "preview_limit":              10,     # max lines a Log expansion shows (0 = show everything)
    "show_folder_stats":          True,   # show image count + size next to each folder in the Log
    "zip_extract_input":          True,   # unpack .zip / .cbz files found in Input before a tool runs
    "zip_compression":            "zip",  # "zip" = saved as a zip | "cbz" = saved as a .cbz (images only, else zip) | "folder" = loose files
}

#FUNCTIONS——————————————————————————————————————————————————————————————————————————————————————————————————
_config_warning = None


def take_config_warning():
    """Returns (once) a message if the config file had to be reset while loading."""
    global _config_warning
    msg, _config_warning = _config_warning, None
    return msg


def load_config():
    global _config_warning
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = None
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, 'r') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("the file does not contain a settings object")
        except (ValueError, OSError) as e:       # json.JSONDecodeError is a ValueError
            backup = CONFIG_PATH.with_name(CONFIG_PATH.name + ".bak")
            try:
                shutil.copy2(CONFIG_PATH, backup)
            except OSError:
                pass
            _config_warning = (f"  ⚠ Your settings file could not be read ({e}). Default settings are being used. "
                               f"The old file was kept as {backup.name}.")
            data = None
    if data is None:
        return copy.deepcopy(DEFAULTS)
    for k, v in DEFAULTS.items():
        data.setdefault(k, copy.deepcopy(v))
    return data


def save_config(config):
    # Write to a temp file first, then swap it in. A crash mid-write can no longer destroy the settings.
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    with open(tmp, 'w') as f:
        json.dump(config, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def get_input(config):
    return Path(config["input"]) / "Input"


def get_output(config, operation, run_name):
    if config.get("sort_output", False):
        base = Path(config["output"]) / "output" / operation / run_name
    else:
        base = Path(config["output"]) / "output" / run_name
    if config.get("replace_output", True) and base.exists():
        shutil.rmtree(base)
        folder = base
    elif not config.get("replace_output", True) and base.exists():
        counter = 1
        while True:
            candidate = base.parent / f"{base.name}{counter}"
            if not candidate.exists():
                folder = candidate
                break
            counter += 1
    else:
        folder = base
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def do_auto_clear(config):
    if config.get("auto_clear_input"):
        src = get_input(config)
        if src.exists():
            count = sum(1 for _ in src.iterdir())
            for item in src.iterdir():
                try:
                    shutil.rmtree(item) if item.is_dir() else item.unlink()
                except Exception as e:
                    print(f"  Auto-clear failed: {item.name}: {e}")
            print(f"  Input cleared ({count} item(s) removed).")


def resolve_sort(config):
    return config.get("default_sort", "natural") != "none"


_last_throttle_check = 0.0
psutil.cpu_percent(interval=None)     # first call only primes the counter


def throttle_if_needed(config, cancel=None):
    """Pause while the computer is busy. Cheap enough to call once per file:
    it only really measures twice a second, and it does not block while measuring.
    If `cancel` is set the pause ends immediately so the job can stop."""
    global _last_throttle_check
    cpu_limit = _int_cfg(config, "throttle_cpu", 80)
    mem_limit = _int_cfg(config, "throttle_mem", 80)
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
        print(f"  Resuming...")


def natural_sort_key(name):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', name)]


def _check_disk_space(path, config=None):
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        min_gb = _int_cfg(config, "min_free_gb", 2) if config else 2
        if free_gb < min_gb:
            print(f"  ✖ Not enough disk space: {free_gb:.1f} GB free (minimum: {min_gb} GB).")
            print(f"    Free up space or lower the minimum in Preferences (≡).")
            print("")
            return False
        elif free_gb < 10:
            print(f"  ⚠ Low disk space: {free_gb:.1f} GB free on output drive.")
        else:
            print(f"  Disk space: {free_gb:.1f} GB free.")
        return True
    except Exception as e:
        print(f"  Could not check disk space: {e}")
        return True


def _is_no_space(e):
    if isinstance(e, OSError):
        if e.errno == errno.ENOSPC:
            return True
        if hasattr(errno, 'EDQUOT') and e.errno == errno.EDQUOT:
            return True
    return False


def collect_image_paths(folder, image_extensions, sub_print=None, use_sort=True):
    paths = []
    skipped = []
    items = sorted(folder.iterdir(), key=lambda x: natural_sort_key(x.name)) if use_sort else list(folder.iterdir())
    for item in items:
        if item.is_file():
            if item.suffix.lower() in image_extensions:
                paths.append(item)
            else:
                skipped.append((item, "unsupported type"))
        elif item.is_dir():
            sub_paths, sub_skipped = collect_image_paths(item, image_extensions, sub_print, use_sort)
            paths.extend(sub_paths)
            skipped.extend(sub_skipped)
            if sub_paths and sub_print:
                sub_print(f"    {item.name}/  →  {len(sub_paths)} image(s)")
    return paths, skipped


def save_pdf(image_paths, output_path):
    if not image_paths:
        print("  No images to save.")
        return
    print(f"  Converting {len(image_paths)} images → PDF...")
    try:
        with open(output_path, 'wb') as f:
            f.write(img2pdf.convert([str(p) for p in image_paths]))
    except Exception as e:
        if _is_no_space(e):
            print(f"  ✖ Disk full — not enough space to write PDF.")
            raise
        print(f"  img2pdf failed ({e}), falling back to Pillow...")
        imgs = []
        for p in image_paths:
            img = Image.open(p).convert("RGB")
            imgs.append(img)
        if imgs:
            imgs[0].save(output_path, save_all=True, append_images=imgs[1:])
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  Saved: {output_path.name}  ({size_mb:.1f} MB)")


def _get_run_name(config, prompt="Run name (Enter to skip): "):
    if not config.get("ask_run_name", False):
        return "Output"
    val = input(prompt).strip()
    if val == SENTINEL:
        return None
    return val or "Output"


def _cancel():
    print("  Cancelled.")
    print("")


def _warn_name_collisions(config, preview):
    """preview = [(old_path, new_path), ...]. Warn if two items would end up with the same new name."""
    users = {}
    for old, new in preview:
        users.setdefault(new, []).append(old)
    clashes = {new: olds for new, olds in users.items() if len(olds) > 1}
    if not clashes:
        return
    print(f"  ⚠ {len(clashes)} new name(s) would be used more than once. Later items overwrite earlier ones!")
    with _Section(config, f"  Name clashes ({len(clashes)})") as s:
        for new, olds in clashes.items():
            s.add(f"    {new.name}  ←  " + ",  ".join(o.name for o in olds))


def _print_summary(config, copied=0, failed=None, skipped=None, label="processed"):
    parts = [f"  {label}: {copied}"]
    if failed:
        parts.append(f"Failed: {len(failed)}")
    if skipped:
        parts.append(f"Skipped: {len(skipped)}")
    print("  " + "   ".join(parts))
    if failed:                                  # failures are never truncated: you need to see all of them
        with _Section(config, f"  Failed files ({len(failed)})", unlimited=True) as s:
            for p, reason in failed:
                s.add(f"    {p.name}: {reason}")
    if skipped:
        with _Section(config, f"  Skipped files ({len(skipped)})") as s:
            for p, reason in skipped:
                s.add(f"    {p.name}: {reason}")


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


def _log_scan(config, files, src, noun="image", numbering=None, details=None):
    """Show every folder the tool is working with, using the shared format.
    numbering = {path: number} prints '12. name' and lists everything (needed to pick files by number).
    details   = {path: text}   appends '  —  text' to a file's line."""
    if numbering is None and resolve_sort(config):          # keep the display in natural order (numbered lists keep their numbers)
        files = sorted(files, key=lambda x: natural_sort_key(_rel(x, src)))
    groups = _group_by_top_folder(files, src)
    for label, fl, base in groups:
        names = [_rel(f, base) + (f"  —  {details[f]}" if details and f in details else "") for f in fl]
        numbers, extra = None, ""
        if numbering:
            numbers = [numbering[f] for f in fl]
            if numbers == list(range(numbers[0], numbers[0] + len(numbers))):   # consecutive: show the range
                extra = f"  ·  #{numbers[0]}–#{numbers[-1]}"
        _log_group(config, label, fl, base, noun=noun, names=names, numbers=numbers, extra=extra)
    if len(groups) > 1:
        print(f"  Total: {len(files)} {noun}(s) in {len(groups)} folder(s){_size_suffix(config, files)}")


def _get_working_folders(src, use_sort=True):
    top = sorted([f for f in src.iterdir() if f.is_dir()],
                 key=lambda x: natural_sort_key(x.name)) if use_sort else [f for f in src.iterdir() if f.is_dir()]
    if len(top) == 1:
        sub = sorted([f for f in top[0].iterdir() if f.is_dir()],
                     key=lambda x: natural_sort_key(x.name)) if use_sort else [f for f in top[0].iterdir() if f.is_dir()]
        if sub:
            print(f"  Found 1 top-level folder '{top[0].name}' — operating on its {len(sub)} subfolder(s).")
            return sub
    return top


# ══ ZIP SUPPORT ═══════════════════════════════════════════════════════════════
# INPUT : every tool unpacks .zip / .cbz files found anywhere in Input (even nested ones) into a
#         normal folder before it starts, so every tool treats them exactly like folders.
#         The zip is removed once it has been unpacked (Input only ever holds copies).
# OUTPUT: with Preferences > Tools > Zip Output turned on, every tool also packs its result
#         into "<run name>.zip" inside its output folder (the loose files are removed once the zip is checked).
#         With zip_compression = "cbz" the archive is "<run name>.cbz" instead. A .cbz is a plain zip, but
#         comic readers expect only images inside, so if the result contains anything else (PDFs, ...) the
#         tool falls back to a normal .zip and says so.

ARCHIVE_EXTENSIONS    = {'.zip', '.cbz'}                       # .cbz (comic book archive) is just a zip
ZIP_COMPRESSION_MODES = ("folder", "zip", "cbz")
CBZ_IMAGE_EXTENSIONS  = IMAGE_EXTENSIONS | {'.webp'}           # what may go inside a .cbz
_MAX_ARCHIVE_NESTING  = 3                                      # zip inside zip inside zip ...
_JUNK_FILES           = {'.ds_store', 'thumbs.db', 'desktop.ini'}
_ALREADY_COMPRESSED   = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.pdf', '.zip', '.cbz'}


def _is_junk_member(parts):
    last = parts[-1]
    return "__MACOSX" in parts or last.startswith("._") or last.lower() in _JUNK_FILES


def _member_name(info):
    """The real file name inside a zip. Old zip tools save names in a local encoding (very common
    for Japanese files) without saying so, and Python would show them as gibberish."""
    name = info.filename
    if info.flag_bits & 0x800 or name.isascii():              # 0x800 = the zip says "this is UTF-8"
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for encoding in ("utf-8", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return name


def _unique_path(path):
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.name} ({n})")
        if not candidate.exists():
            return candidate
        n += 1


def _plan_members(zf, dest):
    """Works out where every entry goes. Entries that would land OUTSIDE `dest` ("../../x" tricks)
    are dropped, as is Mac/Windows junk. Returns ([(info, target_path), ...], unsafe_count)."""
    root = dest.resolve()
    plan, unsafe = [], 0
    for info in zf.infolist():
        parts = [p for p in _member_name(info).replace("\\", "/").split("/") if p not in ("", ".")]
        if not parts:
            continue
        if ".." in parts or re.match(r"^[A-Za-z]:", parts[0]):
            unsafe += 1
            continue
        if _is_junk_member(parts):
            continue
        target = dest.joinpath(*parts)
        try:
            target.resolve().relative_to(root)
        except ValueError:
            unsafe += 1
            continue
        plan.append((info, target))
    return plan, unsafe


def _extract_archive(config, archive, cancel=None):
    """Unpacks one zip next to itself as a folder named after the zip.
    Everything is unpacked into a hidden temporary folder first, so a failure or a Cancel never
    leaves a half-unpacked folder behind. A zip that is just one wrapper folder is flattened.
    Returns (folder, file_count, bytes, unsafe_count), or None if cancelled. Raises on failure."""
    parent = archive.parent
    staging = Path(tempfile.mkdtemp(prefix=".tankobon_unzip_", dir=parent))
    try:
        with zipfile.ZipFile(archive) as zf:
            plan, unsafe = _plan_members(zf, staging)
            files = [(i, t) for i, t in plan if not i.is_dir()]
            if not files:
                raise RuntimeError("the zip file contains no files")
            if any(i.flag_bits & 0x1 for i, _ in files):
                raise RuntimeError("the zip file is password-protected")
            need = sum(i.file_size for i, _ in files)
            reserve = _int_cfg(config, "min_free_gb", 2) * (1024 ** 3)
            free = shutil.disk_usage(parent).free
            if need + reserve > free:
                raise OSError(errno.ENOSPC,
                              f"not enough disk space to unpack ({_fmt_size(need)} needed, "
                              f"{_fmt_size(free)} free, minimum free space is set in Preferences)")
            for info, target in plan:
                if cancel is not None and cancel.is_set():
                    return None
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as s, open(target, "wb") as d:
                    shutil.copyfileobj(s, d)
        kids = list(staging.iterdir())
        source = kids[0] if len(kids) == 1 and kids[0].is_dir() else staging
        final = _unique_path(parent / archive.stem)
        shutil.move(str(source), str(final))
        return final, len(files), need, unsafe
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def extract_archives(config, src, cancel=None):
    """Unpacks every .zip / .cbz in Input (and zips inside those). Returns False if cancelled."""
    if not config.get("zip_extract_input", True):
        return True
    rows, failed, attempted = [], [], set()
    unsafe_total = 0

    def find():
        return sorted((f for f in src.rglob("*")
                       if f.is_file() and f.suffix.lower() in ARCHIVE_EXTENSIONS and f not in attempted),
                      key=lambda x: natural_sort_key(_rel(x, src)))

    for _ in range(_MAX_ARCHIVE_NESTING):
        archives = find()
        if not archives:
            break
        if not attempted:
            print(f"  Zip file(s) found in Input. Unpacking...")
        for archive in archives:
            attempted.add(archive)
            if cancel is not None and cancel.is_set():
                return False
            throttle_if_needed(config, cancel)
            try:
                result = _extract_archive(config, archive, cancel)
            except zipfile.BadZipFile:
                failed.append((archive, "not a valid zip file"))
                continue
            except OSError as e:
                failed.append((archive, "disk full" if _is_no_space(e) and not e.strerror else (e.strerror or str(e))))
                continue
            except Exception as e:
                failed.append((archive, str(e)))
                continue
            if result is None:
                return False
            folder, count, size, unsafe = result
            unsafe_total += unsafe
            try:
                archive.unlink()
            except OSError as e:
                failed.append((archive, f"unpacked, but the zip could not be removed: {e}"))
            rows.append(f"    {_rel(archive, src)}  →  {_rel(folder, src)}/  ({count} file(s), {_fmt_size(size)})")

    for archive in find():                                      # still there after the nesting limit
        failed.append((archive, f"nested more than {_MAX_ARCHIVE_NESTING} levels deep"))

    if rows:
        with _Section(config, f"  Unpacked zip files ({len(rows)})") as s:
            for row in rows:
                s.add(row)
    if unsafe_total:
        print(f"  ⚠ {unsafe_total} unsafe path(s) inside zip files were skipped (they pointed outside the folder).")
    if rows or failed:
        _print_summary(config, copied=len(rows), failed=failed or None, label="extracted")
    return True


def zip_output(config, out, cancel=None):
    """Packs everything in `out` into out/<out name>.zip (or .cbz) when Preferences > Tools > Zip Compression
    is "zip" (or "cbz"). The loose files are removed afterwards, but only once the archive has been checked."""
    mode = config.get("zip_compression", "zip")
    if mode not in ("zip", "cbz"):
        return
    files = [f for f in out.rglob("*") if f.is_file()]
    if not files:
        return
    files.sort(key=lambda x: natural_sort_key(_rel(x, out)))
    ext = ".zip"
    if mode == "cbz":
        others = sorted({f.suffix.lower() or "(no extension)" for f in files if f.suffix.lower() not in CBZ_IMAGE_EXTENSIONS})
        if others:
            print(f"  ⚠ CBZ needs images only, but the output also contains: {', '.join(others)}. Saving a .zip instead.")
        else:
            ext = ".cbz"
    zip_path = out / f"{out.name}{ext}"
    partial = out / f"{out.name}{ext}.partial"
    total = _total_size(files)
    try:
        if total > shutil.disk_usage(out).free:
            print("  ⚠ Not enough free space to make the zip. The output folder was kept as it is.")
            return
    except OSError:
        pass

    print(f"  Packing {len(files)} file(s) into {zip_path.name}...")
    cancelled = False
    try:
        with zipfile.ZipFile(partial, "w", allowZip64=True, strict_timestamps=False) as zf:
            for f in files:
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    break
                throttle_if_needed(config, cancel)
                method = zipfile.ZIP_STORED if f.suffix.lower() in _ALREADY_COMPRESSED else zipfile.ZIP_DEFLATED
                zf.write(f, _rel(f, out).replace(os.sep, "/"), compress_type=method)
        if cancelled:
            partial.unlink(missing_ok=True)
            print("  Packing cancelled. The output folder was kept as it is.")
            return
        with zipfile.ZipFile(partial) as zf:                     # never delete originals before the zip is proven good
            bad = zf.testzip()
        if bad:
            raise RuntimeError(f"zip check failed on '{bad}'")
        os.replace(partial, zip_path)
    except Exception as e:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        if _is_no_space(e):
            print("  ✖ Disk full — could not make the zip. The output folder was kept as it is.")
        else:
            print(f"  ✖ Could not make the zip: {e}. The output folder was kept as it is.")
        return

    print(f"  Saved: {zip_path.name}  ({len(files)} file(s), {_fmt_size(zip_path.stat().st_size)})")

    left = 0
    for f in files:
        try:
            f.unlink()
        except OSError:
            left += 1
    for d in sorted((p for p in out.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    if left:
        print(f"  ⚠ {left} loose file(s) could not be removed after zipping.")


def _finalize_output(config, out, cancel=None):
    """The last step of every successful tool: zip the result (if wanted), then clear Input (if wanted)."""
    zip_output(config, out, cancel)
    do_auto_clear(config)


def folders_to_pdf(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("Folders to PDF")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "folders to pdf", run_name)
    print(f"  Output: {out}")
    if not _check_disk_space(out, config):
        return

    mode = config.get("default_folders_to_pdf_mode", "ask")
    if mode == "ask":
        raw = input("Mode? 1=Combine all into one PDF (default)  2=One PDF per folder: ").strip()
        if raw == SENTINEL:
            return _cancel()
        mode = "individual" if raw == "2" else "combine"
    elif mode not in ("combine", "individual"):
        mode = "combine"                                  # unusable saved value: fall back to the default
    print(f"  Mode: {mode}")
    print(f"  Note: cannot be cancelled once PDF conversion starts.")

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    folders = sorted([f for f in src.iterdir() if f.is_dir()],
                     key=lambda x: natural_sort_key(x.name)) if use_sort else \
        [f for f in src.iterdir() if f.is_dir()]

    if not folders:
        print("  No folders found in Input!")
        print("")
        return

    print(f"  Found {len(folders)} folder(s). Scanning...")

    if mode == "combine":
        all_paths = []
        all_skipped = []
        for folder in folders:
            if cancel and cancel.is_set():
                print("  Cancelled.")
                print("")
                return
            throttle_if_needed(config, cancel)
            paths, skipped = collect_image_paths(folder, IMAGE_EXTENSIONS, use_sort=use_sort)
            _log_group(config, folder.name, paths, folder,
                       extra=f"  ·  {len(skipped)} skipped" if skipped else "")
            all_paths.extend(paths)
            all_skipped.extend(skipped)

        print(f"  Total: {len(all_paths)} images across {len(folders)} folders{_size_suffix(config, all_paths)}")

        if all_paths:
            try:
                save_pdf(all_paths, out / "output.pdf")
            except OSError as e:
                if _is_no_space(e):
                    print(f"  ✖ Job stopped — disk full. PDF may be incomplete.")
                    print("")
                    return
                raise
            _print_summary(config, copied=len(all_paths), skipped=all_skipped or None, label="converted")
            _finalize_output(config, out, cancel)
            print(f"  Done! → {out}")
            print("")
        else:
            print("  No images found in any folder.")
            if all_skipped:
                _print_summary(config, skipped=all_skipped)
            print("")

    else:
        total_converted = 0
        total_skipped = []
        for folder in folders:
            if cancel and cancel.is_set():
                print(f"  Cancelled. ({total_converted} PDFs saved so far)")
                print("")
                return
            throttle_if_needed(config, cancel)
            subfolders = sorted(
                [f for f in folder.iterdir() if f.is_dir()],
                key=lambda x: natural_sort_key(x.name)
            ) if use_sort else [f for f in folder.iterdir() if f.is_dir()]
            units = subfolders if subfolders else [folder]
            unit_data = []
            for unit in units:
                paths, skipped = collect_image_paths(unit, IMAGE_EXTENSIONS, use_sort=use_sort)
                total_skipped.extend(skipped)
                safe_name = re.sub(r'[^\w\s\-.]', '', unit.name).strip() or unit.name
                label = unit.name if unit is folder else f"{folder.name} / {unit.name}"
                extra = (f"  ·  {len(skipped)} skipped" if skipped else "") + \
                        (f"  →  {safe_name}.pdf" if paths else "")
                _log_group(config, label, paths, unit, extra=extra)
                unit_data.append((safe_name, paths))
            for safe_name, paths in unit_data:
                if not paths:
                    continue
                pdf_path = out / f"{safe_name}.pdf"
                try:
                    save_pdf(paths, pdf_path)
                    total_converted += 1
                except OSError as e:
                    if _is_no_space(e):
                        print(f"  ✖ Disk full after {total_converted} PDF(s). Stopping.")
                        print("")
                        return
                    print(f"  Failed to save {safe_name}.pdf: {e}")
        _print_summary(config, copied=total_converted, skipped=total_skipped or None, label="PDFs saved")
        _finalize_output(config, out, cancel)
        print(f"  Done! → {out}")
        print("")


def images_to_pdf(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("Images to PDF")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "images to pdf", run_name)
    print(f"  Output: {out}")
    if not _check_disk_space(out, config):
        return
    print(f"  Note: cannot be cancelled once PDF conversion starts.")

    all_files = list(src.rglob("*"))
    image_files = []
    skipped = []
    for f in all_files:
        if not f.is_file():
            continue
        if f.suffix.lower() in IMAGE_EXTENSIONS:
            image_files.append(f)
        else:
            skipped.append((f, "unsupported type"))

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    if use_sort:
        image_files = sorted(image_files,
                             key=lambda x: natural_sort_key(str(x.relative_to(src))))

    if not image_files:
        print("  No images found in Input!")
        if skipped:
            _print_summary(config, skipped=skipped)
        print("")
        return

    from collections import Counter
    ext_counts = Counter(f.suffix.lower() for f in image_files)
    summary = "  ".join(f"{v}× {k}" for k, v in sorted(ext_counts.items()))
    print(f"  Found {len(image_files)} images:  {summary}{_size_suffix(config, image_files)}")

    mode = config.get("default_images_to_pdf_mode", "ask")
    if mode == "ask":
        raw = input("Mode? 1=Combine all (default)  2=Reorder & Combine: ").strip()
        if raw == SENTINEL:
            return _cancel()
        mode = "reorder & combine" if raw == "2" else "combine"
    elif mode not in ("combine", "reorder & combine"):
        mode = "combine"                                  # unusable saved value: fall back to the default
    print(f"  Mode: {mode}")

    if mode == "reorder & combine":
        # One expander PER FOLDER. Lists follow the preview size; each header shows its number range
        # (e.g. #26–#29) so you can still tell which number is which file when names are hidden.
        numbering = {f: n for n, f in enumerate(image_files, 1)}
        print("  Expand a folder to see which number is which file. Numbers run in order; each folder's range is in its header.")
        _log_scan(config, image_files, src, numbering=numbering)
        print(f"  Enter file numbers (1–{len(image_files)}) separated by commas.")
        print(f"  Leave blank to use all files in default order.")
        raw = input("Order: ").strip()
        if raw == SENTINEL:
            return _cancel()

        if not raw:
            print("  No order given — using all files in default order.")
            final_files = image_files
            out_of_range = []
        else:
            indices = [s.strip() for s in raw.split(",")]
            final_files = []
            out_of_range = []
            for s in indices:
                try:
                    idx = int(s)
                    if 1 <= idx <= len(image_files):
                        final_files.append(image_files[idx - 1])
                    else:
                        out_of_range.append(s)
                except ValueError:
                    out_of_range.append(s)

            if not final_files:
                print("  No valid indices entered. Aborting.")
                print("")
                return

            print(f"  Using {len(final_files)} file(s) in specified order.")
    else:
        _log_scan(config, image_files, src)
        final_files = image_files
        out_of_range = []

    if cancel and cancel.is_set():
        print("  Cancelled.")
        print("")
        return

    try:
        save_pdf(final_files, out / "output.pdf")
    except OSError as e:
        if _is_no_space(e):
            print(f"  ✖ Job stopped — disk full. PDF may be incomplete.")
            print("")
            return
        raise

    if out_of_range:
        print(f"  ⚠ {len(out_of_range)} invalid or out-of-range index/indices skipped: {', '.join(out_of_range)}")

    _print_summary(config, copied=len(final_files), skipped=skipped or None, label="converted")
    _finalize_output(config, out, cancel)
    print(f"  Done! → {out}")
    print("")


def folder_renamer(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("Folder Renamer")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "folder renamer", run_name)
    print(f"  Output: {out}")
    if not _check_disk_space(out, config):
        return

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    folders = _get_working_folders(src, use_sort)

    if not folders:
        print("  No folders found in Input!")
        print("")
        return

    print(f"  Found {len(folders)} folder(s).")
    for folder in folders:
        folder_files = [f for f in folder.rglob("*") if f.is_file()]
        if use_sort:
            folder_files.sort(key=lambda x: natural_sort_key(_rel(x, folder)))
        _log_group(config, folder.name, folder_files, folder, noun="file")
    mode = config.get("default_folder_renamer_mode", "ask")
    if mode == "ask":
        print("  Modes: 1=Prefix  2=Suffix  3=Replace  4=Extract Number")
        mode = input("Choose mode (1-4, default 4): ").strip() or "4"
        if mode == SENTINEL:
            return _cancel()
        mode = {"1": "prefix", "2": "suffix", "3": "replace", "4": "extract number"}.get(mode, mode)
    if mode not in ("prefix", "suffix", "replace", "extract number"):
        print(f"  ⚠ '{mode}' is not a valid mode. Using the default (extract number).")
        mode = "extract number"
    print(f"  Mode: {mode}")

    preview = []
    skipped = []

    if mode == "prefix":
        param1 = input("Prefix to add: ")
        if param1 == SENTINEL:
            return _cancel()
        for f in folders:
            preview.append((f, out / (param1 + f.name)))
    elif mode == "suffix":
        param1 = input("Suffix to add: ")
        if param1 == SENTINEL:
            return _cancel()
        for f in folders:
            preview.append((f, out / (f.name + param1)))
    elif mode == "replace":
        param1 = input("Find: ")
        if param1 == SENTINEL:
            return _cancel()
        if not param1:
            print("  'Find' can't be empty.")
            print("")
            return
        param2 = input("Replace with (Enter for blank): ")
        if param2 == SENTINEL:
            return _cancel()
        for f in folders:
            preview.append((f, out / f.name.replace(param1, param2)))
    elif mode == "extract number":
        for folder in folders:
            match = re.search(r'\d+(?:\.\d+)?', folder.name)
            if match:
                raw = match.group()
                if '.' in raw:
                    integer, decimal = raw.split('.', 1)
                    new_name = f"{int(integer)}.{decimal}"
                else:
                    new_name = str(int(raw))
                preview.append((folder, out / new_name))
            else:
                skipped.append((folder, "no number found"))
    else:
        print("  Invalid mode.")
        print("")
        return

    if not preview and not skipped:
        print("  Nothing to rename.")
        print("")
        return

    print(f"  {len(preview)} folder(s) to rename{f', {len(skipped)} skipped' if skipped else ''}.")
    _warn_name_collisions(config, preview)
    with _Section(config, f"  Preview ({len(preview)})") as s:
        for old, new in preview:
            s.add(f"    {old.name}  →  {new.name}")

    failed = []
    copied = 0
    for i, (old, new) in enumerate(preview):
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({copied} renamed so far)")
            print("")
            return
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(old), str(new), dirs_exist_ok=True)
            copied += 1
        except OSError as e:
            if _is_no_space(e):
                print(f"  ✖ Disk full after {copied} folder(s). Stopping.")
                _print_summary(config, copied=copied, failed=failed or None,
                               skipped=skipped or None, label="renamed")
                print("")
                return
            failed.append((old, str(e)))
        except Exception as e:
            failed.append((old, str(e)))

    _print_summary(config, copied=copied, failed=failed or None,
                   skipped=skipped or None, label="renamed")
    _finalize_output(config, out, cancel)
    print(f"  Done! → {out}")
    print("")


def file_renamer(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("File Renamer")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "renamed", run_name)
    print(f"  Output: {out}")
    if not _check_disk_space(out, config):
        return

    items = [f for f in src.rglob("*") if f.is_file()]
    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    if use_sort:
        items = sorted(items, key=lambda x: natural_sort_key(x.name))

    if not items:
        print("  No files found in Input!")
        print("")
        return

    print(f"  Found {len(items)} file(s){_size_suffix(config, items)}")
    _log_scan(config, items, src, noun="file")
    mode = config.get("default_file_renamer_mode", "ask")
    if mode == "ask":
        print("  Modes: 1=Prefix  2=Suffix  3=Replace  4=Sequence")
        mode = input("Choose mode (1-4): ").strip()
        if mode == SENTINEL:
            return _cancel()
        mode = {"1": "prefix", "2": "suffix", "3": "replace", "4": "sequence"}.get(mode, mode)
    print(f"  Mode: {mode}")

    if mode == "prefix":
        param1 = input("Prefix to add: ")
        if param1 == SENTINEL:
            return _cancel()
        preview = [(f, out / f.relative_to(src).parent / (param1 + f.name)) for f in items]
    elif mode == "suffix":
        param1 = input("Suffix to add (before extension): ")
        if param1 == SENTINEL:
            return _cancel()
        preview = [(f, out / f.relative_to(src).parent / (f.stem + param1 + f.suffix)) for f in items]
    elif mode == "replace":
        param1 = input("Find: ")
        if param1 == SENTINEL:
            return _cancel()
        if not param1:
            print("  'Find' can't be empty.")
            print("")
            return
        param2 = input("Replace with (Enter for blank): ")
        if param2 == SENTINEL:
            return _cancel()
        preview = [(f, out / f.relative_to(src).parent / f.name.replace(param1, param2)) for f in items]
    elif mode == "sequence":
        param1 = input("Base name (leave blank for numbers only): ")
        if param1 == SENTINEL:
            return _cancel()
        start = input("Start number (default 1): ").strip()
        if start == SENTINEL:
            return _cancel()
        pad = input("Pad digits (default 3): ").strip()
        if pad == SENTINEL:
            return _cancel()
        def _whole_number(text, default, what):
            if not text:
                return default
            try:
                n = int(text)
                if n < 0:
                    raise ValueError
                return n
            except ValueError:
                print(f"  ⚠ '{text}' is not a valid {what}. Using the default ({default}).")
                return default
        start = _whole_number(start, 1, "start number")
        pad   = _whole_number(pad,   3, "number of digits")
        def _seq_name(base, i, ext):
            num = str(start + i).zfill(pad)
            return f"{base}{num}{ext}" if base else f"{num}{ext}"
        preview = [(f, out / f.relative_to(src).parent / _seq_name(param1, i, f.suffix))
                   for i, f in enumerate(items)]
    else:
        print("  Invalid mode.")
        print("")
        return

    _warn_name_collisions(config, preview)
    with _Section(config, f"  Preview ({len(preview)})") as s:
        for old, new in preview:
            s.add(f"    {old.name}  →  {new.name}")

    copied = 0
    failed = []
    for i, (old, new) in enumerate(preview):
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({copied} renamed so far)")
            print("")
            return
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(old), str(new))
            copied += 1
        except OSError as e:
            if _is_no_space(e):
                print(f"  ✖ Disk full after {copied} file(s). Stopping.")
                _print_summary(config, copied=copied, failed=failed or None, label="renamed")
                print("")
                return
            failed.append((old, str(e)))
        except Exception as e:
            failed.append((old, str(e)))

    _print_summary(config, copied=copied, failed=failed or None, label="renamed")
    _finalize_output(config, out, cancel)
    print(f"  Done! → {out}")
    print("")


def combine_image_sets(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("Combine Image Sets")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "combined image set", run_name)
    print(f"  Output: {out}")
    if not _check_disk_space(out, config):
        return

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")

    def collect_images(folder):
        all_items = sorted(folder.iterdir(), key=lambda x: natural_sort_key(x.name)) if use_sort else list(folder.iterdir())
        images = []
        skipped = []
        for item in all_items:
            if item.is_file():
                if item.suffix.lower() in IMAGE_EXTENSIONS:
                    images.append(item)
                else:
                    skipped.append((item, "unsupported type"))
            elif item.is_dir():
                sub_imgs, sub_skip = collect_images(item)
                images.extend(sub_imgs)
                skipped.extend(sub_skip)
        return images, skipped

    folders = sorted([f for f in src.iterdir() if f.is_dir()],
                     key=lambda x: natural_sort_key(x.name)) if use_sort else [f for f in src.iterdir() if f.is_dir()]

    if not folders:
        print("  No folders found in Input!")
        print("")
        return

    print(f"  Found {len(folders)} top-level folder(s). Scanning...")

    counter = 1
    total_skipped = []
    total_failed = []

    for folder in folders:
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({counter - 1} images combined so far)")
            print("")
            return
        throttle_if_needed(config, cancel)
        subfolders = sorted([f for f in folder.iterdir() if f.is_dir()],
                            key=lambda x: natural_sort_key(x.name)) if use_sort else [f for f in folder.iterdir() if f.is_dir()]
        targets = [(sub.name, sub) for sub in subfolders] if subfolders else [(folder.name, folder)]

        for label, target in targets:
            images, skipped = collect_images(target)
            total_skipped.extend(skipped)

            # Show this set (count, size, and each image with the number it will get) BEFORE copying it.
            first = counter
            names = [f"{_rel(img, target)}  →  {str(first + i).zfill(4)}{img.suffix}" for i, img in enumerate(images)]
            extra = (f"  ·  {len(skipped)} skipped" if skipped else "") + \
                    (f"  →  {str(first).zfill(4)}–{str(first + len(images) - 1).zfill(4)}" if images else "")
            _log_group(config, label if target is folder else f"{folder.name} / {label}",
                       images, target, names=names, extra=extra)

            for img in images:
                dest = out / f"{str(counter).zfill(4)}{img.suffix}"
                try:
                    shutil.copy2(img, dest)
                    counter += 1
                except OSError as e:
                    if _is_no_space(e):
                        print(f"  ✖ Disk full after {counter - 1} image(s). Stopping.")
                        _print_summary(config, copied=counter - 1, failed=total_failed or None,
                                       skipped=total_skipped or None, label="combined")
                        print("")
                        return
                    total_failed.append((img, str(e)))
                except Exception as e:
                    total_failed.append((img, str(e)))

    _print_summary(config, copied=counter - 1, failed=total_failed or None,
                   skipped=total_skipped or None, label="combined")
    _finalize_output(config, out, cancel)
    print(f"  Total: {counter - 1} images combined.")
    print(f"  Done! → {out}")
    print("")


def image_converter(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("Image Converter")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()

    fmt = config.get("default_img_fmt", "ask")
    if fmt == "ask":
        raw = input("Format (jpg/png/webp/bmp/tiff, default jpg): ").strip()
        if raw == SENTINEL:                      # must be checked BEFORE .lower(), which changes the text
            return _cancel()
        fmt = raw.lower() or "jpg"
    fmt = {"jpeg": "jpg", "tif": "tiff"}.get(fmt, fmt)
    if fmt not in ("jpg", "png", "webp", "bmp", "tiff"):
        print(f"  ⚠ Unknown format '{fmt}'. Using the default (jpg).")
        fmt = "jpg"
    print(f"  Format: {fmt}")

    pillow_fmt = "JPEG" if fmt == "jpg" else fmt.upper()
    ext        = ".jpg" if fmt == "jpg" else f".{fmt}"

    out = get_output(config, "converted", run_name)
    print(f"  Output: {out}")
    if not _check_disk_space(out, config):
        return

    converter_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.webp'}
    """MORE FILE FORMATS ARE ALLOWED. IMAGE CONVERTER IS MEANT TO TURN UN-IDEAL FILES INTER BETTER ONES"""
    all_files = list(src.rglob("*"))
    images = []
    skipped = []
    for f in all_files:
        if not f.is_file():
            continue
        if f.suffix.lower() in converter_extensions:
            images.append(f)
        else:
            skipped.append((f, "unsupported type"))

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    if use_sort:
        images = sorted(images, key=lambda x: natural_sort_key(x.name))

    if not images:
        print("  No images found in Input!")
        if skipped:
            _print_summary(config, skipped=skipped)
        print("")
        return

    from collections import Counter
    ext_counts = Counter(f.suffix.lower() for f in images)
    summary = "  ".join(f"{v}× {k}" for k, v in sorted(ext_counts.items()))
    print(f"  Found {len(images)} images:  {summary}{_size_suffix(config, images)}")
    _log_scan(config, images, src)
    print(f"  Converting all → {ext} ...")

    converted = copied = 0
    failed = []
    for i, img_path in enumerate(images):
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({converted + copied} images processed so far)")
            print("")
            return
        throttle_if_needed(config, cancel)
        dest_dir = out / img_path.relative_to(src).parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (img_path.stem + ext)
        src_ext = img_path.suffix.lower()
        already_correct = src_ext == ext or (ext == ".jpg" and src_ext == ".jpeg")
        if already_correct:
            try:
                shutil.copy2(img_path, dest)
                copied += 1
            except OSError as e:
                if _is_no_space(e):
                    print(f"  ✖ Disk full after {converted + copied} image(s). Stopping.")
                    _print_summary(config, copied=converted + copied, failed=failed or None,
                                   skipped=skipped or None, label="converted")
                    print("")
                    return
                failed.append((img_path, str(e)))
            except Exception as e:
                failed.append((img_path, str(e)))
        else:
            try:
                img = Image.open(img_path)
                if pillow_fmt == "JPEG" and img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")
                img.save(dest, pillow_fmt)
                converted += 1
            except OSError as e:
                if _is_no_space(e):
                    print(f"  ✖ Disk full after {converted + copied} image(s). Stopping.")
                    _print_summary(config, copied=converted + copied, failed=failed or None,
                                   skipped=skipped or None, label="converted")
                    print("")
                    return
                failed.append((img_path, str(e)))
            except Exception as e:
                failed.append((img_path, str(e)))

    _print_summary(config, copied=converted + copied, failed=failed or None,
                   skipped=skipped or None, label="converted")
    _finalize_output(config, out, cancel)
    print(f"  Done! → {out}")
    print("")


def find_duplicates(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("Find Duplicates")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "find duplicates", run_name)
    print(f"  Output: {out}")
    if not _check_disk_space(out, config):
        return

    # ── hash method (set in Preferences) ─────────────────────────────────────
    hash_method = config.get("default_dedupe_hash", "exact")
    print(f"  Hash: {hash_method}")

    all_files_raw = list(src.rglob("*"))
    all_files = []
    skipped = []
    for f in all_files_raw:
        if not f.is_file():
            continue
        if f.suffix.lower() in IMAGE_EXTENSIONS:
            all_files.append(f)
        else:
            skipped.append((f, "unsupported type"))

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    if use_sort:
        all_files = sorted(all_files, key=lambda x: natural_sort_key(x.name))

    if not all_files:
        print("  No images found in Input!")
        if skipped:
            _print_summary(config, skipped=skipped)
        print("")
        return
    print(f"  Found {len(all_files)} image(s){_size_suffix(config, all_files)}")
    _log_scan(config, all_files, src)

    hash_failed = []
    duplicates  = set()
    dup_groups  = []

    if hash_method == "exact":
        print(f"  Scanning {len(all_files)} image(s)...")
        seen = {}
        file_digests = {}
        for f in all_files:
            if cancel and cancel.is_set():
                print("  Cancelled.")
                print("")
                return
            try:
                digest = hashlib.sha256(f.read_bytes()).hexdigest()
                file_digests[f] = digest
                if digest in seen:
                    duplicates.add(f)
                else:
                    seen[digest] = f
            except Exception as e:
                hash_failed.append((f, str(e)))

        # Build dup_groups for consistent reporting
        groups_by_digest = {}
        for f, d in file_digests.items():
            groups_by_digest.setdefault(d, []).append(f)
        dup_groups = [g for g in groups_by_digest.values() if len(g) > 1]

    else:
        import imagehash
        PHASH_THRESHOLD = _int_cfg(config, "phash_threshold", 4)
        print(f"  Scanning {len(all_files)} image(s) (threshold={PHASH_THRESHOLD})...")

        file_phashes = {}
        for f in all_files:
            if cancel and cancel.is_set():
                print("  Cancelled.")
                print("")
                return
            try:
                with Image.open(f) as img:
                    file_phashes[f] = imagehash.phash(img)
            except Exception as e:
                hash_failed.append((f, str(e)))

        representatives = []
        groups = []
        for f in [f for f in all_files if f in file_phashes]:
            ph = file_phashes[f]
            matched = False
            for i, (_, rep_ph) in enumerate(representatives):
                if (ph - rep_ph) <= PHASH_THRESHOLD:
                    groups[i].append(f)
                    matched = True
                    break
            if not matched:
                representatives.append((f, ph))
                groups.append([f])

        dup_groups = [g for g in groups if len(g) > 1]
        duplicates = {f for g in dup_groups for f in g[1:]}

    if not duplicates:
        print(f"  No duplicates found across {len(all_files)} images.")
        _print_summary(config, copied=len(all_files), failed=hash_failed or None,
                       skipped=skipped or None, label="scanned")
        print("")
        return

    print(f"  Found {len(dup_groups)} duplicate group(s), {len(duplicates)} redundant image(s).")

    mode = config.get("default_dedupe_mode", "ask")
    if mode == "ask":
        mode = input("Mode? 1=Keep one of each (default)  2=Remove all instances: ").strip() or "1"
        if mode == SENTINEL:
            return _cancel()
        mode = {"1": "keep one copy", "2": "delete all"}.get(mode, mode)
    if mode not in ("keep one copy", "delete all"):
        print(f"  ⚠ '{mode}' is not a valid mode. Using the default (keep one copy).")
        mode = "keep one copy"
    print(f"  Mode: {mode}")

    if mode == "delete all":
        exclude = {f for g in dup_groups for f in g}
    elif mode == "keep one copy":
        exclude = duplicates  # exact: all but first seen; perceptual: all but group[0]
    else:
        print("  Invalid mode.")
        print("")
        return

    with _Section(config, f"  Excluding ({len(exclude)}) — expand and hover over filenames to preview") as s:
        for f in sorted(exclude, key=lambda x: natural_sort_key(x.name)):
            s.add(f"    {_rel(f, src)}", preview_path=f)
    print(f"  {len(exclude)} image(s) will be excluded.")

    keep_list = [f for f in all_files if f not in exclude]
    copied = 0
    failed = []
    for f in keep_list:
        dest = out / f.relative_to(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(str(f), dest)
            copied += 1
        except OSError as e:
            if _is_no_space(e):
                print(f"  ✖ Disk full after {copied} image(s). Stopping.")
                _print_summary(config, copied=copied, failed=failed or None,
                               skipped=skipped or None, label="copied")
                print("")
                return
            failed.append((f, str(e)))
        except Exception as e:
            failed.append((f, str(e)))

    _print_summary(config, copied=copied, failed=failed or None,
                   skipped=skipped or None, label="copied")
    _finalize_output(config, out, cancel)
    print(f"  Done! → {out}")
    print("")


def pdf_combiner(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("PDF Combiner")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "pdf combined", run_name)
    print(f"  Output: {out}")
    if not _check_disk_space(out, config):
        return

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")

    all_files = list(src.rglob("*"))
    pdfs = []
    skipped = []
    for f in all_files:
        if not f.is_file():
            continue
        if f.suffix.lower() == ".pdf":
            pdfs.append(f)
        else:
            skipped.append((f, "not a PDF"))

    if use_sort:
        pdfs = sorted(pdfs, key=lambda x: natural_sort_key(x.name))

    if not pdfs:
        print("  No PDFs found in Input!")
        if skipped:
            _print_summary(config, skipped=skipped)
        print("")
        return

    print(f"  Found {len(pdfs)} PDF(s){_size_suffix(config, pdfs)}")
    _log_scan(config, pdfs, src, noun="PDF")
    print("  Combining...")
    writer = PdfWriter()
    total_pages = 0
    failed = []
    page_rows = []
    for i, pdf_path in enumerate(pdfs):
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({total_pages} pages combined so far)")
            print("")
            return
        throttle_if_needed(config, cancel)
        try:
            reader = PdfReader(str(pdf_path))
            for page in reader.pages:
                writer.add_page(page)
            total_pages += len(reader.pages)
            page_rows.append(f"    {_rel(pdf_path, src)}  —  {len(reader.pages)} page(s)  (running total: {total_pages})")
        except Exception as e:
            failed.append((pdf_path, str(e)))
    with _Section(config, f"  Pages per PDF ({len(page_rows)})") as s:
        for row in page_rows:
            s.add(row)

    try:
        out_path = out / "combined.pdf"
        with open(out_path, "wb") as f:
            writer.write(f)
        size_mb = out_path.stat().st_size / (1024 * 1024)
        print(f"  Saved: combined.pdf  ({total_pages} pages, {size_mb:.1f} MB)")
    except OSError as e:
        if _is_no_space(e):
            print(f"  ✖ Disk full — could not write combined PDF.")
            print("")
            return
        raise

    _print_summary(config, copied=len(pdfs) - len(failed), failed=failed or None,
                   skipped=skipped or None, label="combined")
    _finalize_output(config, out, cancel)
    print(f"  Done! → {out}")
    print("")


def pdf_to_images(config, cancel=None):
    try:
        from pdf2image import convert_from_path
    except ImportError:
        print("  pdf2image not installed — run: pip install pdf2image")
        print("  Also need poppler: brew install poppler")
        print("")
        return

    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("PDF to Images")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()

    fmt = config.get("default_pdf_to_images_fmt", "ask")
    if fmt == "ask":
        raw = input("Format (jpg/png, default jpg): ").strip()
        if raw == SENTINEL:                      # must be checked BEFORE .lower(), which changes the text
            return _cancel()
        fmt = raw.lower() or "jpg"
    fmt = {"jpeg": "jpg"}.get(fmt, fmt)
    if fmt not in ("jpg", "png"):
        print(f"  ⚠ Invalid format '{fmt}'. Using the default (jpg).")
        fmt = "jpg"
    print(f"  Format: {fmt}")

    dpi = config.get("default_dpi", "ask")
    if dpi == "ask":
        raw = input("DPI (default 72): ").strip()
        if raw == SENTINEL:
            return _cancel()
        dpi = raw or "72"
    try:
        dpi = int(str(dpi).strip())
        if not 10 <= dpi <= 1200:
            raise ValueError
    except (TypeError, ValueError):
        print(f"  ⚠ Invalid DPI '{dpi}' (use a whole number from 10 to 1200). Using the default (72).")
        dpi = 72
    print(f"  DPI: {dpi}")

    out = get_output(config, "pdf to images", run_name)
    print(f"  Output: {out}")
    if not _check_disk_space(out, config):
        return

    all_files = list(src.rglob("*"))
    pdfs = []
    skipped = []
    for f in all_files:
        if not f.is_file():
            continue
        if f.suffix.lower() == ".pdf":
            pdfs.append(f)
        else:
            skipped.append((f, "not a PDF"))

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    if use_sort:
        pdfs = sorted(pdfs, key=lambda x: natural_sort_key(x.name))

    if not pdfs:
        print("  No PDFs found in Input!")
        if skipped:
            _print_summary(config, skipped=skipped)
        print("")
        return

    total_page_count = 0
    page_counts = {}
    for pdf_path in pdfs:
        try:
            page_counts[pdf_path] = len(PdfReader(str(pdf_path)).pages)
            total_page_count += page_counts[pdf_path]
        except Exception:
            pass

    print(f"  Found {len(pdfs)} PDF(s), {total_page_count} pages total{_size_suffix(config, pdfs)}.")
    _log_scan(config, pdfs, src, noun="PDF",
              details={p: (f"{n} page(s)" if (n := page_counts.get(p)) is not None else "unreadable") for p in pdfs})
    print(f"  Note: cancelling will stop after the current page finishes.")
    total_pages = 0
    failed = []
    exported = []
    for pdf_path in pdfs:
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({total_pages} pages exported so far)")
            print("")
            return
        pdf_out = out / pdf_path.stem
        pdf_out.mkdir(exist_ok=True)
        print(f"  Converting: {pdf_path.name}  (@ {dpi} DPI)...")
        try:
            page_count = len(PdfReader(str(pdf_path)).pages)
        except Exception as e:
            failed.append((pdf_path, str(e)))
            continue
        for i in range(page_count):
            if cancel and cancel.is_set():
                print(f"  Cancelled. ({total_pages + i} pages exported so far)")
                print("")
                return
            throttle_if_needed(config, cancel)
            try:
                pages = convert_from_path(str(pdf_path), dpi=dpi, first_page=i + 1, last_page=i + 1, poppler_path=_POPPLER_PATH)
                dest = pdf_out / f"{pdf_path.stem}_{str(i + 1).zfill(4)}.{fmt}"
                pages[0].save(str(dest), "JPEG" if fmt == "jpg" else "PNG")
            except OSError as e:
                if _is_no_space(e):
                    print(f"  ✖ Disk full after {total_pages + i} page(s). Stopping.")
                    _print_summary(config, copied=total_pages + i, failed=failed or None,
                                   skipped=skipped or None, label="pages exported")
                    print("")
                    return
                failed.append((pdf_path, f"page {i+1}: {e}"))
            except Exception as e:
                failed.append((pdf_path, f"page {i+1}: {e}"))
        total_pages += page_count
        exported.append(f"    {_rel(pdf_path, src)}  →  {pdf_out.name}/  ({page_count} page(s))")

    with _Section(config, f"  Exported ({len(exported)})") as s:
        for row in exported:
            s.add(row)

    _print_summary(config, copied=total_pages, failed=failed or None,
                   skipped=skipped or None, label="pages exported")
    _finalize_output(config, out, cancel)
    print(f"  Total: {len(pdfs)} PDF(s), {total_pages} page(s) exported.")
    print(f"  Done! → {out}")
    print("")


def pdf_splitter(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("PDF Splitter")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "pdf split", run_name)
    print(f"  Output: {out}")
    if not _check_disk_space(out, config):
        return

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    pdfs = sorted(
        [f for f in src.rglob("*") if f.is_file() and f.suffix.lower() == ".pdf"],
        key=lambda x: natural_sort_key(x.name)
    ) if use_sort else [f for f in src.rglob("*") if f.is_file() and f.suffix.lower() == ".pdf"]

    if not pdfs:
        print("  No PDF found in Input!")
        print("")
        return
    if len(pdfs) > 1:
        print(f"  Multiple PDFs found, only the first pdf: {pdfs[0].name} will be used")
    pdf_path = pdfs[0]

    try:
        reader = PdfReader(str(pdf_path))
    except Exception as e:
        print(f"  Failed to read PDF: {e}")
        print("")
        return

    total = len(reader.pages)
    print(f"  Loaded: {pdf_path.name}  ({total} pages)")

    print("  Enter page ranges separated by commas, e.g. 1-7, 12-13, 27-20")
    print("  Each range becomes its own PDF. 1-7 keeps page order; 7-1 reverses it.")
    raw = input("Ranges: ").strip()
    if raw == SENTINEL:
        return _cancel()
    if not raw:
        print("  No ranges entered. Aborting.")
        print("")
        return

    ranges = []
    invalid = []
    reversed_ranges = []
    for s in [x.strip() for x in raw.split(",")]:
        parts = s.split("-")
        if len(parts) != 2:
            invalid.append(s)
            continue
        try:
            a, b = int(parts[0].strip()), int(parts[1].strip())
        except ValueError:
            invalid.append(s)
            continue
        if a < 1 or a > total or b < 1 or b > total:
            invalid.append(s)
            continue
        if a > b:
            reversed_ranges.append(s)
            ranges.append((a, b, True))
        else:
            ranges.append((a, b, False))

    if invalid:
        print(f"  ⚠ Skipped invalid or out-of-range: {', '.join(invalid)}")
    if reversed_ranges:
        print(f"  ⚠ Reversed range(s) will be processed in descending order: {', '.join(reversed_ranges)}")
    if not ranges:
        print("  No valid ranges entered. Aborting.")
        print("")
        return

    stem = pdf_path.stem
    part_count = len(ranges)
    print(f"  Writing {part_count} part(s)...")
    failed = []
    written = 0
    part_rows = []
    for i, (a, b, descending) in enumerate(ranges):
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({written} part(s) saved so far)")
            print("")
            return
        page_indices = list(range(a - 1, b - 2, -1) if descending else range(a - 1, b))
        try:
            writer = PdfWriter()
            for p in page_indices:
                writer.add_page(reader.pages[p])
            out_path = out / f"{stem}_part{i + 1}.pdf"
            with open(out_path, "wb") as f:
                writer.write(f)
            direction = f" (reversed)" if descending else ""
            part_rows.append(f"    Part {i + 1}/{part_count}: pages {a}–{b}{direction}  →  {out_path.name}")
            written += 1
        except OSError as e:
            if _is_no_space(e):
                print(f"  ✖ Disk full after {written} part(s). Stopping.")
                _print_summary(config, copied=written, failed=failed or None, label="parts saved")
                print("")
                return
            failed.append((pdf_path, f"part {i + 1}: {e}"))
        except Exception as e:
            failed.append((pdf_path, f"part {i + 1}: {e}"))

    with _Section(config, f"  Parts ({len(part_rows)})") as s:
        for row in part_rows:
            s.add(row)
    _print_summary(config, copied=written, failed=failed or None, label="parts saved")
    _finalize_output(config, out, cancel)
    print(f"  Done! {written} part(s) saved.")
    print(f"  → {out}")
    print("")


def status(config):
    src = get_input(config)
    out_base = Path(config.get("output", "")) / "output"

    start_section, end_section = _get_log_section_fns()

    print("")
    if src.exists():
        items = list(src.iterdir())
        files = [i for i in items if i.is_file()]
        dirs  = [i for i in items if i.is_dir()]
        parts = []
        if dirs:  parts.append(f"{len(dirs)} folder(s)")
        if files: parts.append(f"{len(files)} file(s)")

        print(f"Input  —  {', '.join(parts) if parts else 'empty'}")

        for d in sorted(dirs, key=lambda x: natural_sort_key(x.name)):
            sub_dirs = sorted([i for i in d.iterdir() if i.is_dir()], key=lambda x: natural_sort_key(x.name))
            file_count = sum(1 for _ in d.rglob("*") if _.is_file())
            if sub_dirs:
                start_section(f"[folder] {d.name}/  ({file_count} file(s))")
                for sd in sub_dirs:
                    sc = sum(1 for _ in sd.rglob("*") if _.is_file())
                    print(f"    {sd.name}/  ({sc} file(s))")
                end_section()
            else:
                print(f"  [folder] {d.name}/  ({file_count} file(s))")

        for f in sorted(files, key=lambda x: natural_sort_key(x.name)):
            tag = {".zip": "[zip]   ", ".cbz": "[cbz]   "}.get(f.suffix.lower(), "[file]  ")
            print(f"  {tag} {f.name}")
    else:
        print("Input  —  (doesn't exist)")

    print("")

    if out_base.exists():
        items = [i for i in out_base.iterdir() if i.is_dir()]
        print(f"Output  —  {len(items)} folder(s)" if items else "Output  —  empty")
        for op in sorted(items, key=lambda x: natural_sort_key(x.name)):
            sub_dirs = sorted([i for i in op.iterdir() if i.is_dir()], key=lambda x: natural_sort_key(x.name))
            file_count = sum(1 for f in op.rglob("*") if f.is_file())
            if sub_dirs:
                start_section(f"  {op.name}/  ({file_count} file(s))")
                for sd in sub_dirs:
                    sc = sum(1 for f in sd.rglob("*") if f.is_file())
                    print(f"    {sd.name}/  ({sc} file(s))")
                end_section()
            else:
                print(f"    {op.name}/  ({file_count} file(s))")
    else:
        print("Output  —  empty")

    print("")


def info():
    print("""
-- File & Folder Management --
'folders to pdf'     'images to pdf'     'folder renamer'
'file renamer'       'combine image sets' 'image converter'
'find duplicates'    'pdf splitter'       'pdf combiner'
'pdf to images'

-- Other --
'status'  'exit'

Zip files (.zip / .cbz) in Input are unpacked automatically.
Results are saved as a zip by default; choose cbz or folder in Preferences > Tools > Zip Compression.

All output goes to output/
""")

# Unused code. Manager.py primarily functions as a backend for Interface.py
# The area below allos the manager.py to run on its own
def command_line():
    config = load_config()
    print("\nFile & Folder Manager — type 'info' for commands")
    while True:
        command = input(">>> ").strip().lower()
        try:
            if command == "info":                 info()
            elif command == "folders to pdf":     folders_to_pdf(config)
            elif command == "images to pdf":      images_to_pdf(config)
            elif command == "folder renamer":     folder_renamer(config)
            elif command == "file renamer":       file_renamer(config)
            elif command == "combine image sets": combine_image_sets(config)
            elif command == "image converter":    image_converter(config)
            elif command == "pdf splitter":       pdf_splitter(config)
            elif command == "pdf combiner":       pdf_combiner(config)
            elif command == "pdf to images":      pdf_to_images(config)
            elif command == "find duplicates":    find_duplicates(config)
            elif command == "status":             status(config)
            elif command == "exit":
                print("exiting...")
                break
            else:
                print("Invalid command. Type 'info' for list.")
        except KeyboardInterrupt:
            print("\nInterrupted.")


if __name__ == "__main__":
    command_line()
