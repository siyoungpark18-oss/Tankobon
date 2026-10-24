"""Configuration and input paths for Tankobon."""

import copy
import json
import os
import shutil
from pathlib import Path

from ToolNames import DEFAULT_DOCUMENT_FORMAT, DEFAULT_FOLDER_FORMAT


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
    "default_doc_format": DEFAULT_DOCUMENT_FORMAT,
    "salvage_failed_document_pages": False,
    "epub_direction": "rtl",
    "split_wide_pages": True,
    "zip_compression": DEFAULT_FOLDER_FORMAT,
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


