"""Configuration, input/output paths, and per-run staging for Manager tools."""

import copy
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path


CONFIG_PATH = Path.home() / ".tankobon" / "config.json"

# Keep defaults in one place so Preferences and Manager share the same baseline.
DEFAULTS = {
    "input": "",
    "output": "",
    "default_sort": "natural",
    "default_dpi": "72",
    "default_img_fmt": "ask",
    "default_pdf_to_images_fmt": "ask",
    "auto_clear_input": False,
    "confirm_clear_input": False,
    "confirm_clear_output": True,
    "ask_run_name": True,
    "replace_output": True,
    "sort_output": False,
    "hotkey_continue": "Return",
    "hotkey_cancel": "Escape",
    "throttle_cpu": 80,
    "throttle_mem": 80,
    "dark_mode": False,
    "min_free_gb": 10,
    "log_default_expanded": False,
    "show_timestamps": True,
    "open_output_recent": False,
    "first_launch": True,
    "log_blank_lines": False,
    "log_font_size": 11,
    "ui_font_size": 10,
    "default_images_to_pdf_mode": "ask",
    "default_folders_to_pdf_mode": "ask",
    "default_folder_renamer_mode": "ask",
    "default_file_renamer_mode": "ask",
    "default_dedupe_mode": "ask",
    "default_dedupe_hash": "exact",
    "phash_threshold": 4,
    "guide_empty_input": True,
    "show_tooltips": True,
    "allow_concurrent_jobs": False,
    "last_tool_options": {},
    "themes": {},
    "preview_limit": 10,
    "show_folder_stats": True,
    "zip_extract_input": True,
    "default_doc_format": "epub",
    "epub_direction": "rtl",
    "zip_compression": "cbz",
    "keep_documents_unarchived": True,
}

_config_warning = None


def take_config_warning():
    """Return (once) a message if the config file had to be reset while loading."""
    global _config_warning
    msg, _config_warning = _config_warning, None
    return msg


def load_config():
    global _config_warning
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = None
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("the file does not contain a settings object")
        except (ValueError, OSError) as e:
            backup = CONFIG_PATH.with_name(CONFIG_PATH.name + ".bak")
            try:
                shutil.copy2(CONFIG_PATH, backup)
            except OSError:
                pass
            _config_warning = (
                f"  ⚠ Your settings file could not be read ({e}). Default settings are being used. "
                f"The old file was kept as {backup.name}."
            )
            data = None
    if data is None:
        return copy.deepcopy(DEFAULTS)
    legacy_confirm = data.get("confirm_clear")
    if legacy_confirm is not None:
        data.setdefault("confirm_clear_input", bool(legacy_confirm))
        data.setdefault("confirm_clear_output", bool(legacy_confirm))
    data.pop("confirm_clear", None)
    for key, value in DEFAULTS.items():
        data.setdefault(key, copy.deepcopy(value))
    return data


def save_config(config):
    """Persist settings atomically, so an interrupted write cannot destroy the file."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def get_input(config):
    return Path(config["input"]) / "Input"


STAGING_PREFIX = ".tankobon_run_"
_RUNS = {}  # staging folder -> {root, operation, thread, done}
_RUNS_LOCK = threading.Lock()


def _validate_run_name(run_name):
    """Reject names that could escape the output directory on any supported platform."""
    name = str(run_name).strip()
    if (name in (".", "..") or Path(name).is_absolute()
            or any(sep in name for sep in ("/", "\\", ":"))
            or any(ord(char) < 32 for char in name)):
        raise ValueError("Run names must be a single folder name without path separators.")
    return name


def _unique_output_path(path, reserved=None, directory=False):
    """Return a non-colliding output path, adding ' (2)', ' (3)', etc. as needed."""
    reserved = reserved if reserved is not None else set()
    path = Path(path)

    def key(candidate):
        return os.path.normcase(os.path.abspath(str(candidate))).casefold()

    suffix = "" if directory else path.suffix
    stem = path.name if directory else path.name[:-len(suffix)] if suffix else path.name
    candidate = path
    number = 2
    while candidate.exists() or key(candidate) in reserved:
        candidate = path.with_name(f"{stem} ({number}){suffix}")
        number += 1
    reserved.add(key(candidate))
    return candidate


def _unique_output_stem(parent, name, extension, reserved=None):
    """Choose a unique stem while accounting for save_book's appended extension."""
    target = _unique_output_path(Path(parent) / f"{name}{extension}", reserved)
    return target.with_name(target.name[:-len(extension)]) if extension else target


def get_output(config, operation, run_name):
    """Choose an output directory, isolating each run before results are finalized."""
    root = Path(config["output"]) / "output"
    if run_name:
        run_name = _validate_run_name(run_name)
    if not (config.get("sort_output", False) or run_name):
        root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=root))
        with _RUNS_LOCK:
            _RUNS[str(staging)] = {
                "root": root,
                "operation": operation,
                "thread": threading.get_ident(),
                "done": False,
            }
        return staging
    run_name = run_name or "Output"
    base = root / operation / run_name if config.get("sort_output", False) else root / run_name
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
