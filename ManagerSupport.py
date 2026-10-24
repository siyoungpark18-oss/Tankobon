"""Shared run-control and disk-space helpers for manager operations."""

import shutil

from ManagerOutput import validate_run_name
from Util import int_cfg


SENTINEL = "\x00CANCELLED\x00"


def _check_disk_space(path, config=None):
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        min_gb = int_cfg(config, "min_free_gb", 10) if config else 10
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


def _get_run_name(config, prompt="Run name (Enter to skip): "):
    """Returns the run name, "" when the person didn't name the run, or None if they cancelled."""
    if not config.get("ask_run_name", False):
        return ""
    while True:
        val = input(prompt).strip()
        if val == SENTINEL:
            return None
        if not val:
            return ""
        try:
            return validate_run_name(val)
        except ValueError:
            print("  Run names must be a single folder name. Avoid '/', '\\', and ':'; don't use '.' or '..' alone.")


def _cancel():
    print("  Cancelled.")
    print("")
