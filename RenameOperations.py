"""File and folder rename workflows."""

import re
import shutil

from Util import natural_sort_key, rel, is_no_space, resolve_sort
from LogFormat import Section, log_group, log_scan, print_summary, size_suffix
from ToolNames import folder_tool_name
from ManagerConfig import get_input
from ManagerSupport import SENTINEL, _cancel, _check_disk_space, _get_run_name
from ManagerOutput import get_output, finalize_output, shown
from Archives import extract_archives
from RenameRules import (
    _has_name_conflicts, _validate_rename_preview, _validate_rename_text,
)


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


def folder_renamer(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print(folder_tool_name(config, "Folder Renamer"))
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, folder_tool_name(config, "Folder Renamer").lower(), run_name)
    print(f"  Output: {shown(out)}")
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
            folder_files.sort(key=lambda x: natural_sort_key(rel(x, folder)))
        log_group(config, folder.name, folder_files, folder, noun="file")
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
        if not _validate_rename_text(param1):
            return
        for f in folders:
            preview.append((f, out / (param1 + f.name)))
    elif mode == "suffix":
        param1 = input("Suffix to add: ")
        if param1 == SENTINEL:
            return _cancel()
        if not _validate_rename_text(param1):
            return
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
        if not _validate_rename_text(param1) or not _validate_rename_text(param2):
            return
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
    if not _validate_rename_preview(out, preview, src, folders=True):
        print("")
        return
    if _has_name_conflicts(config, preview):
        print("")
        return
    with Section(config, f"  Preview ({len(preview)})") as s:
        for old, new in preview:
            s.add(f"    {old.name}  →  {new.name}")

    failed = []
    copied = 0
    for i, (old, new) in enumerate(preview):
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({copied} renamed and staged so far)")
            print("")
            return
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(old), str(new), dirs_exist_ok=True)
            copied += 1
        except OSError as e:
            if is_no_space(e):
                print(f"  ✖ Disk full after {copied} folder(s). Stopping.")
                print_summary(config, copied=copied, failed=failed or None,
                               skipped=skipped or None, label="renamed")
                print("")
                return
            failed.append((old, str(e)))
        except Exception as e:
            failed.append((old, str(e)))

    print_summary(config, copied=copied, failed=failed or None,
                   skipped=skipped or None, label="renamed")
    if finalize_output(config, out, cancel):
        print(f"  Done! → {shown(out)}")
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
    print(f"  Output: {shown(out)}")
    if not _check_disk_space(out, config):
        return

    items = [f for f in src.rglob("*") if f.is_file()]
    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    if use_sort:
        items = sorted(items, key=lambda x: natural_sort_key(str(x.relative_to(src))))

    if not items:
        print("  No files found in Input!")
        print("")
        return

    print(f"  Found {len(items)} file(s){size_suffix(config, items)}")
    log_scan(config, items, src, noun="file")
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
        if not _validate_rename_text(param1):
            return
        preview = [(f, out / f.relative_to(src).parent / (param1 + f.name)) for f in items]
    elif mode == "suffix":
        param1 = input("Suffix to add (before extension): ")
        if param1 == SENTINEL:
            return _cancel()
        if not _validate_rename_text(param1):
            return
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
        if not _validate_rename_text(param1) or not _validate_rename_text(param2):
            return
        preview = [(f, out / f.relative_to(src).parent / f.name.replace(param1, param2)) for f in items]
    elif mode == "sequence":
        param1 = input("Base name (leave blank for numbers only): ")
        if param1 == SENTINEL:
            return _cancel()
        if not _validate_rename_text(param1):
            return
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

    if not _validate_rename_preview(out, preview, src):
        print("")
        return
    if _has_name_conflicts(config, preview):
        print("")
        return
    with Section(config, f"  Preview ({len(preview)})") as s:
        for old, new in preview:
            s.add(f"    {old.name}  →  {new.name}")

    copied = 0
    failed = []
    for i, (old, new) in enumerate(preview):
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({copied} renamed and staged so far)")
            print("")
            return
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(old), str(new))
            copied += 1
        except OSError as e:
            if is_no_space(e):
                print(f"  ✖ Disk full after {copied} file(s). Stopping.")
                print_summary(config, copied=copied, failed=failed or None, label="renamed")
                print("")
                return
            failed.append((old, str(e)))
        except Exception as e:
            failed.append((old, str(e)))

    print_summary(config, copied=copied, failed=failed or None, label="renamed")
    if finalize_output(config, out, cancel):
        print(f"  Done! → {shown(out)}")
    print("")
