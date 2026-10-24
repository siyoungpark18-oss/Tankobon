"""Validation rules shared by file and folder rename operations."""

import os
from pathlib import Path

from LogFormat import Section


def _validate_rename_preview(out, preview, src, folders=False):
    """Reject rename values that introduce path components or unsafe file-name characters."""
    root = Path(out).resolve()
    for old, new in preview:
        try:
            relative = Path(new).resolve(strict=False).relative_to(root)
        except ValueError:
            print("  ✖ Rename stopped because a name points outside the output folder.")
            print("    Remove path separators and try again.")
            return False
        parts = relative.parts
        expected_parent = () if folders else Path(old).relative_to(src).parent.parts
        if (len(parts) != len(expected_parent) + 1 or parts[:-1] != expected_parent
                or any(part in ("", ".", "..") or any(ch in part for ch in ("/", "\\", ":"))
                       or any(ord(ch) < 32 for ch in part) for part in parts)):
            print("  ✖ Rename stopped because names must be single safe file or folder names.")
            print("    Remove '/', '\\', ':', or control characters, then try again.")
            return False
    return True


def _validate_rename_text(value):
    if any(ch in value for ch in ("/", "\\", ":")) or any(ord(ch) < 32 for ch in value):
        print("  ✖ Rename stopped because names cannot contain '/', '\\', ':', or control characters.")
        return False
    return True


def _has_name_conflicts(config, preview):
    """Return True when destinations collide with each other or already exist."""
    users = {}
    existing = []
    for old, new in preview:
        key = os.path.normcase(os.path.abspath(str(new))).casefold()
        users.setdefault(key, (new, []))[1].append(old)
        if new.exists():
            existing.append((new, old))
    clashes = [entry for entry in users.values() if len(entry[1]) > 1]
    if not clashes and not existing:
        return False
    print("  ✖ Rename stopped because one or more output names already exist or repeat.")
    print("    Choose a different naming mode or input set, then try again.")
    with Section(config, f"  Conflicting destinations ({len(clashes) + len(existing)})") as s:
        for new, olds in clashes:
            s.add(f"    {new.name}  ←  " + ",  ".join(o.name for o in olds))
        for new, old in existing:
            s.add(f"    {new.name} already exists (from {old.name})")
    return True
