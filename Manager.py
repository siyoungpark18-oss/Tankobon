#IMPORTS——————————————————————————————————————————————————————————————————————————————————————————————————
from PIL import Image
from pathlib import Path
import re
import shutil
import os
import hashlib
import zipfile
import tempfile
import io
from pypdf import PdfReader, PdfWriter
from Util import (natural_sort_key, rel, int_cfg, is_no_space, throttle_if_needed,
                  IMAGE_EXTENSIONS, DOCUMENT_EXTENSIONS, resolve_sort, get_poppler_path)
from LogFormat import Section, log_group, log_scan, size_suffix, print_summary, section_callbacks
from ToolNames import document_name, document_format, folder_tool_name
from ManagerConfig import get_input
from ManagerOutput import (
    get_output, finalize_output, shown, unique_path, unique_output_stem, validate_run_name,
)
from Archives import extract_archives
from Books import (
    PathImage, render_pdf_pages_as_images, epub_pages, build_epub,
    save_book, image_to_pdf_bytes, export_epub_pages, write_pdf_atomically,
)


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



def collect_image_paths(folder, image_extensions, use_sort=True):
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
            sub_paths, sub_skipped = collect_image_paths(item, image_extensions, use_sort)
            paths.extend(sub_paths)
            skipped.extend(sub_skipped)
    return paths, skipped


def collect_direct_image_paths(folder, image_extensions, use_sort=True):
    """Collect supported files directly inside a folder without descending into child folders."""
    items = sorted(folder.iterdir(), key=lambda x: natural_sort_key(x.name)) if use_sort else list(folder.iterdir())
    paths, skipped = [], []
    for item in items:
        if item.is_file():
            if item.suffix.lower() in image_extensions:
                paths.append(item)
            else:
                skipped.append((item, "unsupported type"))
    return paths, skipped




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


def folders_to_file(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print(f"Folders to {document_name(config)}")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, f"folders to {document_format(config)}", run_name)
    print(f"  Output: {shown(out)}")
    if not _check_disk_space(out, config):
        return

    mode = config.get("default_folders_to_pdf_mode", "ask")
    if mode == "ask":
        raw = input(f"Mode? 1=Combine all into one {document_name(config)} (default)  2=One {document_name(config)} per folder: ").strip()
        if raw == SENTINEL:
            return _cancel()
        mode = "individual" if raw == "2" else "combine"
    elif mode not in ("combine", "individual"):
        mode = "combine"                                  # unusable saved value: fall back to the default
    print(f"  Mode: {mode}")
    if document_format(config) == "pdf":
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
            log_group(config, folder.name, paths, folder,
                       extra=f"  ·  {len(skipped)} skipped" if skipped else "")
            all_paths.extend(paths)
            all_skipped.extend(skipped)

        print(f"  Total: {len(all_paths)} images across {len(folders)} folders{size_suffix(config, all_paths)}")

        if all_paths:
            try:
                if save_book(config, all_paths, out / "output", "Output", cancel) is False:
                    return _cancel()
            except OSError as e:
                if is_no_space(e):
                    print(f"  ✖ Job stopped — disk full. {document_name(config)} may be incomplete.")
                    print("")
                    return
                raise
            print_summary(config, copied=len(all_paths), skipped=all_skipped or None, label="converted")
            if finalize_output(config, out, cancel):
                print(f"  Done! → {shown(out)}")
            print("")
        else:
            print("  No images found in any folder.")
            if all_skipped:
                print_summary(config, skipped=all_skipped)
            print("")

    else:
        total_converted = 0
        total_skipped = []
        used_book_paths = set()
        for folder in folders:
            if cancel and cancel.is_set():
                print(f"  Cancelled. ({total_converted} {document_name(config)}s staged so far)")
                print("")
                return
            throttle_if_needed(config, cancel)
            subfolders = sorted(
                [f for f in folder.iterdir() if f.is_dir()],
                key=lambda x: natural_sort_key(x.name)
            ) if use_sort else [f for f in folder.iterdir() if f.is_dir()]
            units = list(subfolders) if subfolders else [folder]
            if subfolders:
                loose_images, loose_skipped = collect_direct_image_paths(
                    folder, IMAGE_EXTENSIONS, use_sort=use_sort)
                if loose_images or loose_skipped:
                    units.append(folder)
            unit_data = []
            for unit in units:
                if unit is folder and subfolders:
                    paths, skipped = collect_direct_image_paths(
                        unit, IMAGE_EXTENSIONS, use_sort=use_sort)
                else:
                    paths, skipped = collect_image_paths(unit, IMAGE_EXTENSIONS, use_sort=use_sort)
                total_skipped.extend(skipped)
                raw_name = f"{folder.name} (loose images)" if unit is folder and subfolders else unit.name
                safe_name = re.sub(r'[^\w\s\-.]', '', raw_name).strip() or raw_name
                if paths:
                    safe_name = unique_output_stem(
                        out, safe_name, f".{document_format(config)}", used_book_paths).name
                label = "Loose images" if unit is folder and subfolders else (
                    unit.name if unit is folder else f"{folder.name} / {unit.name}")
                extra = (f"  ·  {len(skipped)} skipped" if skipped else "") + \
                        (f"  →  {safe_name}.{document_format(config)}" if paths else "")
                log_group(config, label, paths, unit, extra=extra)
                unit_data.append((safe_name, paths))
            for safe_name, paths in unit_data:
                if not paths:
                    continue
                try:
                    if save_book(config, paths, out / safe_name, safe_name, cancel) is False:
                        print(f"  Cancelled. ({total_converted} {document_name(config)}s staged so far)")
                        print("")
                        return
                    total_converted += 1
                except OSError as e:
                    if is_no_space(e):
                        print(f"  ✖ Disk full after {total_converted} {document_name(config)}(s). Stopping.")
                        print("")
                        return
                    print(f"  Failed to save {safe_name}.{document_format(config)}: {e}")
        print_summary(config, copied=total_converted, skipped=total_skipped or None, label=f"{document_name(config)}s saved")
        if finalize_output(config, out, cancel):
            print(f"  Done! → {shown(out)}")
        print("")


def images_to_file(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print(f"Images to {document_name(config)}")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, f"images to {document_format(config)}", run_name)
    print(f"  Output: {shown(out)}")
    if not _check_disk_space(out, config):
        return
    if document_format(config) == "pdf":
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
            print_summary(config, skipped=skipped)
        print("")
        return

    from collections import Counter
    ext_counts = Counter(f.suffix.lower() for f in image_files)
    summary = "  ".join(f"{v}× {k}" for k, v in sorted(ext_counts.items()))
    print(f"  Found {len(image_files)} images:  {summary}{size_suffix(config, image_files)}")

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
        log_scan(config, image_files, src, numbering=numbering, unlimited=True)
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
        log_scan(config, image_files, src)
        final_files = image_files
        out_of_range = []

    if cancel and cancel.is_set():
        print("  Cancelled.")
        print("")
        return

    try:
        if save_book(config, final_files, out / "output", "Output", cancel) is False:
            return _cancel()
    except OSError as e:
        if is_no_space(e):
            print(f"  ✖ Job stopped — disk full. {document_name(config)} may be incomplete.")
            print("")
            return
        raise

    if out_of_range:
        print(f"  ⚠ {len(out_of_range)} invalid or out-of-range index/indices skipped: {', '.join(out_of_range)}")

    print_summary(config, copied=len(final_files), skipped=skipped or None, label="converted")
    if finalize_output(config, out, cancel):
        print(f"  Done! → {shown(out)}")
    print("")



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



def combine_image_sets(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print(folder_tool_name(config, "Combine Image Sets"))
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "combined image set", run_name)
    print(f"  Output: {shown(out)}")
    if not _check_disk_space(out, config):
        return

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")

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
            print(f"  Cancelled. ({counter - 1} images combined and staged so far)")
            print("")
            return
        throttle_if_needed(config, cancel)
        subfolders = sorted([f for f in folder.iterdir() if f.is_dir()],
                            key=lambda x: natural_sort_key(x.name)) if use_sort else [f for f in folder.iterdir() if f.is_dir()]
        targets = [(sub.name, sub, False) for sub in subfolders] if subfolders else [
            (folder.name, folder, False)]
        if subfolders:
            loose_images, loose_skipped = collect_direct_image_paths(
                folder, IMAGE_EXTENSIONS, use_sort=use_sort)
            if loose_images or loose_skipped:
                targets.append(("Loose images", folder, True))

        for label, target, direct_only in targets:
            if direct_only:
                images, skipped = collect_direct_image_paths(
                    target, IMAGE_EXTENSIONS, use_sort=use_sort)
            else:
                images, skipped = collect_image_paths(target, IMAGE_EXTENSIONS, use_sort=use_sort)
            total_skipped.extend(skipped)

            # Show this set (count, size, and each image with the number it will get) BEFORE copying it.
            first = counter
            names = [f"{rel(img, target)}  →  {str(first + i).zfill(4)}{img.suffix}" for i, img in enumerate(images)]
            extra = (f"  ·  {len(skipped)} skipped" if skipped else "") + \
                    (f"  →  {str(first).zfill(4)}–{str(first + len(images) - 1).zfill(4)}" if images else "")
            log_group(config, label if target is folder else f"{folder.name} / {label}",
                       images, target, names=names, extra=extra)

            for img in images:
                dest = out / f"{str(counter).zfill(4)}{img.suffix}"
                try:
                    shutil.copy2(img, dest)
                    counter += 1
                except OSError as e:
                    if is_no_space(e):
                        print(f"  ✖ Disk full after {counter - 1} image(s). Stopping.")
                        print_summary(config, copied=counter - 1, failed=total_failed or None,
                                       skipped=total_skipped or None, label="combined")
                        print("")
                        return
                    total_failed.append((img, str(e)))
                except Exception as e:
                    total_failed.append((img, str(e)))

    print_summary(config, copied=counter - 1, failed=total_failed or None,
                   skipped=total_skipped or None, label="combined")
    if finalize_output(config, out, cancel):
        print(f"  Total: {counter - 1} images combined.")
        print(f"  Done! → {shown(out)}")
    print("")


def image_converter(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print(folder_tool_name(config, "Image Converter"))
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
    print(f"  Output: {shown(out)}")
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
        images = sorted(images, key=lambda x: natural_sort_key(str(x.relative_to(src))))

    if not images:
        print("  No images found in Input!")
        if skipped:
            print_summary(config, skipped=skipped)
        print("")
        return

    from collections import Counter
    ext_counts = Counter(f.suffix.lower() for f in images)
    summary = "  ".join(f"{v}× {k}" for k, v in sorted(ext_counts.items()))
    print(f"  Found {len(images)} images:  {summary}{size_suffix(config, images)}")
    log_scan(config, images, src)
    print(f"  Converting all → {ext} ...")

    converted = copied = 0
    failed = []
    for i, img_path in enumerate(images):
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({converted + copied} images processed and staged so far)")
            print("")
            return
        throttle_if_needed(config, cancel)
        dest_dir = out / img_path.relative_to(src).parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = unique_path(dest_dir / (img_path.stem + ext))
        src_ext = img_path.suffix.lower()
        already_correct = src_ext == ext or (ext == ".jpg" and src_ext == ".jpeg")
        if already_correct:
            try:
                shutil.copy2(img_path, dest)
                copied += 1
            except OSError as e:
                if is_no_space(e):
                    print(f"  ✖ Disk full after {converted + copied} image(s). Stopping.")
                    print_summary(config, copied=converted + copied, failed=failed or None,
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
                if is_no_space(e):
                    print(f"  ✖ Disk full after {converted + copied} image(s). Stopping.")
                    print_summary(config, copied=converted + copied, failed=failed or None,
                                   skipped=skipped or None, label="converted")
                    print("")
                    return
                failed.append((img_path, str(e)))
            except Exception as e:
                failed.append((img_path, str(e)))

    print_summary(config, copied=converted + copied, failed=failed or None,
                   skipped=skipped or None, label="converted")
    if finalize_output(config, out, cancel):
        print(f"  Done! → {shown(out)}")
    print("")


def find_duplicates(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print(folder_tool_name(config, "Find Duplicates"))
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "find duplicates", run_name)
    print(f"  Output: {shown(out)}")
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
        all_files = sorted(all_files, key=lambda x: natural_sort_key(str(x.relative_to(src))))

    if not all_files:
        print("  No images found in Input!")
        if skipped:
            print_summary(config, skipped=skipped)
        print("")
        return
    print(f"  Found {len(all_files)} image(s){size_suffix(config, all_files)}")
    log_scan(config, all_files, src)

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
        PHASH_THRESHOLD = int_cfg(config, "phash_threshold", 4)
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
        print_summary(config, copied=len(all_files), failed=hash_failed or None,
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

    with Section(config, f"  Excluding ({len(exclude)}) — expand and hover over filenames to preview") as s:
        for f in sorted(exclude, key=lambda x: natural_sort_key(x.name)):
            s.add(f"    {rel(f, src)}", preview_path=f)
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
            if is_no_space(e):
                print(f"  ✖ Disk full after {copied} image(s). Stopping.")
                print_summary(config, copied=copied, failed=failed or None,
                               skipped=skipped or None, label="copied")
                print("")
                return
            failed.append((f, str(e)))
        except Exception as e:
            failed.append((f, str(e)))

    print_summary(config, copied=copied, failed=failed or None,
                   skipped=skipped or None, label="copied")
    if finalize_output(config, out, cancel):
        print(f"  Done! → {shown(out)}")
    print("")


def document_combiner(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    output_format = document_format(config)
    output_name = output_format.upper()
    print(f"{output_name} Combiner")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, f"{output_format} combined", run_name)
    print(f"  Output: {shown(out)}")
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
        if f.suffix.lower() in DOCUMENT_EXTENSIONS:
            pdfs.append(f)
        else:
            skipped.append((f, "not a PDF or EPUB"))

    if use_sort:
        pdfs = sorted(pdfs, key=lambda x: natural_sort_key(str(x.relative_to(src))))

    if not pdfs:
        print("  No PDFs or EPUBs found in Input!")
        if skipped:
            print_summary(config, skipped=skipped)
        print("")
        return

    n_epub = sum(1 for f in pdfs if f.suffix.lower() == ".epub")
    n_pdf = len(pdfs) - n_epub
    noun = "document" if n_epub else "PDF"
    mix = f"  ({n_pdf} PDF, {n_epub} EPUB)" if n_epub and n_pdf else ""
    print(f"  Found {len(pdfs)} {noun}(s){mix}{size_suffix(config, pdfs)}")
    log_scan(config, pdfs, src, noun=noun)
    as_epub = output_format == "epub"
    if n_epub and n_pdf and not as_epub:
        print("  PDFs and EPUBs are mixed, so the result is a PDF (EPUB pages are added as PDF pages).")
    if as_epub and n_pdf:
        print("  PDF pages will be rendered as images inside the EPUB book.")
    print("  Combining...")
    total_pages = 0
    failed = []
    page_rows = []

    if as_epub:
        pages, direction = [], None
        with tempfile.TemporaryDirectory(prefix="tankobon_epub_pages_", dir=out) as page_dir:
            for doc_index, doc in enumerate(pdfs):
                if cancel and cancel.is_set():
                    print(f"  Cancelled. ({total_pages} pages combined and staged so far)")
                    print("")
                    return
                throttle_if_needed(config, cancel)
                try:
                    if doc.suffix.lower() == ".epub":
                        with zipfile.ZipFile(doc) as zf:
                            members, doc_direction, _title = epub_pages(zf)
                            for member_index, member in enumerate(members):
                                suffix = Path(member).suffix.lower() or ".img"
                                image_path = Path(page_dir) / f"doc{doc_index:04d}_{member_index:06d}{suffix}"
                                image_path.write_bytes(zf.read(member))
                                pages.append(PathImage(image_path))
                        if not members:
                            raise ValueError("no page images found (only image-based EPUBs are supported)")
                        direction = direction or doc_direction
                        n_pages = len(members)
                    else:
                        reader = PdfReader(str(doc))
                        n_pages = len(reader.pages)
                        pages.extend(render_pdf_pages_as_images(
                            doc, range(1, n_pages + 1), page_dir, f"doc{doc_index:04d}", cancel))
                    total_pages += n_pages
                    page_rows.append(f"    {rel(doc, src)}  —  {n_pages} page(s)  (running total: {total_pages})")
                except Exception as e:
                    failed.append((doc, str(e)))
            with Section(config, f"  Pages per document ({len(page_rows)})") as section:
                for row in page_rows:
                    section.add(row)
            if not pages:
                print("  Nothing to combine.")
                print_summary(config, copied=0, failed=failed or None, skipped=skipped or None, label="combined")
                print("")
                return
            try:
                if not build_epub(config, pages, out / "combined.epub", "Combined", cancel, direction):
                    return _cancel()
            except OSError as e:
                if is_no_space(e):
                    print("  ✖ Disk full — could not write combined EPUB.")
                    print("")
                    return
                raise
    else:
        writer = PdfWriter()
        for i, pdf_path in enumerate(pdfs):
            if cancel and cancel.is_set():
                print(f"  Cancelled. ({total_pages} pages combined and staged so far)")
                print("")
                return
            throttle_if_needed(config, cancel)
            try:
                if pdf_path.suffix.lower() == ".epub":
                    with zipfile.ZipFile(pdf_path) as zf:
                        members, _direction, _title = epub_pages(zf)
                        if not members:
                            raise ValueError("no page images found (only image-based EPUBs are supported)")
                        for m in members:
                            for page in PdfReader(io.BytesIO(image_to_pdf_bytes(zf.read(m)))).pages:
                                writer.add_page(page)
                    n_pages = len(members)
                else:
                    reader = PdfReader(str(pdf_path))
                    for page in reader.pages:
                        writer.add_page(page)
                    n_pages = len(reader.pages)
                total_pages += n_pages
                page_rows.append(f"    {rel(pdf_path, src)}  —  {n_pages} page(s)  (running total: {total_pages})")
            except Exception as e:
                failed.append((pdf_path, str(e)))
        if not writer.pages:
            print("  Nothing to combine.")
            print_summary(config, copied=0, failed=failed or None, skipped=skipped or None, label="combined")
            print("")
            return
        with Section(config, f"  Pages per {noun} ({len(page_rows)})") as section:
            for row in page_rows:
                section.add(row)
        try:
            out_path = out / "combined.pdf"
            write_pdf_atomically(writer, out_path)
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"  Saved: combined.pdf  ({total_pages} pages, {size_mb:.1f} MB)")
        except OSError as e:
            if is_no_space(e):
                print("  ✖ Disk full — could not write combined PDF.")
                print("")
                return
            raise


    print_summary(config, copied=len(pdfs) - len(failed), failed=failed or None,
                   skipped=skipped or None, label="combined")
    if finalize_output(config, out, cancel):
        print(f"  Done! → {shown(out)}")
    print("")


def document_to_images(config, cancel=None):
    try:
        from pdf2image import convert_from_path
    except ImportError:
        convert_from_path = None          # only a problem if there is a PDF to convert (EPUBs don't need it)

    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print(f"{document_name(config)} to Images")
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

    out = get_output(config, f"{document_format(config)} to images", run_name)
    print(f"  Output: {shown(out)}")
    if not _check_disk_space(out, config):
        return

    all_files = list(src.rglob("*"))
    pdfs = []
    skipped = []
    for f in all_files:
        if not f.is_file():
            continue
        if f.suffix.lower() in DOCUMENT_EXTENSIONS:
            pdfs.append(f)
        else:
            skipped.append((f, "not a PDF or EPUB"))

    if convert_from_path is None and any(f.suffix.lower() == ".pdf" for f in pdfs):
        print("  pdf2image not installed, so PDFs are skipped — run: pip install pdf2image")
        print("  Also need poppler: brew install poppler")
        skipped.extend((f, "pdf2image not installed") for f in pdfs if f.suffix.lower() == ".pdf")
        pdfs = [f for f in pdfs if f.suffix.lower() != ".pdf"]

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    if use_sort:
        pdfs = sorted(pdfs, key=lambda x: natural_sort_key(str(x.relative_to(src))))

    if not pdfs:
        print("  No PDFs or EPUBs found in Input!")
        if skipped:
            print_summary(config, skipped=skipped)
        print("")
        return

    total_page_count = 0
    page_counts = {}
    used_pdf_dirs = set()
    for pdf_path in pdfs:
        try:
            if pdf_path.suffix.lower() == ".epub":
                with zipfile.ZipFile(pdf_path) as zf:
                    page_counts[pdf_path] = len(epub_pages(zf)[0])
            else:
                page_counts[pdf_path] = len(PdfReader(str(pdf_path)).pages)
            total_page_count += page_counts[pdf_path]
        except Exception:
            pass

    has_epub = any(f.suffix.lower() == ".epub" for f in pdfs)
    noun = "document" if has_epub else "PDF"
    print(f"  Found {len(pdfs)} {noun}(s), {total_page_count} pages total{size_suffix(config, pdfs)}.")
    if has_epub:
        print("  EPUB pages are taken out as they are; DPI only applies to PDFs.")
    log_scan(config, pdfs, src, noun=noun,
              details={p: (f"{n} page(s)" if (n := page_counts.get(p)) is not None else "unreadable") for p in pdfs})
    print(f"  Note: cancelling will stop after the current page finishes.")
    total_pages = 0
    failed = []
    exported = []
    for pdf_path in pdfs:
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({total_pages} pages exported and staged so far)")
            print("")
            return
        pdf_out = unique_path(out / pdf_path.stem, used_pdf_dirs, directory=True)
        pdf_out.mkdir(exist_ok=True)
        if pdf_path.suffix.lower() == ".epub":
            print(f"  Extracting: {pdf_path.name}...")
            try:
                n_done, was_cancelled = export_epub_pages(config, pdf_path, pdf_out, fmt, cancel)
            except OSError as e:
                if is_no_space(e):
                    print(f"  ✖ Disk full after {total_pages} page(s). Stopping.")
                    print_summary(config, copied=total_pages, failed=failed or None,
                                   skipped=skipped or None, label="pages exported")
                    print("")
                    return
                failed.append((pdf_path, str(e)))
                continue
            except Exception as e:
                failed.append((pdf_path, str(e)))
                continue
            if was_cancelled:
                print(f"  Cancelled. ({total_pages + n_done} pages exported and staged so far)")
                print("")
                return
            total_pages += n_done
            exported.append(f"    {rel(pdf_path, src)}  →  {pdf_out.name}/  ({n_done} page(s))")
            continue
        print(f"  Converting: {pdf_path.name}  (@ {dpi} DPI)...")
        try:
            page_count = len(PdfReader(str(pdf_path)).pages)
        except Exception as e:
            failed.append((pdf_path, str(e)))
            continue
        for i in range(page_count):
            if cancel and cancel.is_set():
                print(f"  Cancelled. ({total_pages + i} pages exported and staged so far)")
                print("")
                return
            throttle_if_needed(config, cancel)
            try:
                pages = convert_from_path(str(pdf_path), dpi=dpi, first_page=i + 1, last_page=i + 1,
                                           poppler_path=get_poppler_path())
                dest = pdf_out / f"{pdf_path.stem}_{str(i + 1).zfill(4)}.{fmt}"
                pages[0].save(str(dest), "JPEG" if fmt == "jpg" else "PNG")
            except OSError as e:
                if is_no_space(e):
                    print(f"  ✖ Disk full after {total_pages + i} page(s). Stopping.")
                    print_summary(config, copied=total_pages + i, failed=failed or None,
                                   skipped=skipped or None, label="pages exported")
                    print("")
                    return
                failed.append((pdf_path, f"page {i+1}: {e}"))
            except Exception as e:
                failed.append((pdf_path, f"page {i+1}: {e}"))
        total_pages += page_count
        exported.append(f"    {rel(pdf_path, src)}  →  {pdf_out.name}/  ({page_count} page(s))")

    with Section(config, f"  Exported ({len(exported)})") as s:
        for row in exported:
            s.add(row)

    print_summary(config, copied=total_pages, failed=failed or None,
                   skipped=skipped or None, label="pages exported")
    if finalize_output(config, out, cancel):
        print(f"  Total: {len(pdfs)} {noun}(s), {total_pages} page(s) exported.")
        print(f"  Done! → {shown(out)}")
    print("")


def document_splitter(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    output_format = document_format(config)
    output_name = output_format.upper()
    print(f"{output_name} Splitter")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, f"{output_format} split", run_name)
    print(f"  Output: {shown(out)}")
    if not _check_disk_space(out, config):
        return

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    pdfs = sorted(
        [f for f in src.rglob("*") if f.is_file() and f.suffix.lower() in DOCUMENT_EXTENSIONS],
        key=lambda x: natural_sort_key(str(x.relative_to(src)))
    ) if use_sort else [f for f in src.rglob("*") if f.is_file() and f.suffix.lower() in DOCUMENT_EXTENSIONS]

    if not pdfs:
        print("  No PDF or EPUB found in Input!")
        print("")
        return
    if len(pdfs) > 1:
        print(f"  Multiple files found, only the first one: {pdfs[0].name} will be used")
    pdf_path = pdfs[0]
    is_epub = pdf_path.suffix.lower() == ".epub"
    kind = "EPUB" if is_epub else "PDF"
    to_epub = output_format == "epub"

    reader, epub_zf, epub_members, epub_direction = None, None, [], None
    try:
        if is_epub:
            epub_zf = zipfile.ZipFile(pdf_path)
            epub_members, epub_direction, _title = epub_pages(epub_zf)
            if not epub_members:
                raise ValueError("no page images found (only image-based EPUBs are supported)")
            total = len(epub_members)
        else:
            reader = PdfReader(str(pdf_path))
            total = len(reader.pages)
    except Exception as e:
        if epub_zf is not None:
            epub_zf.close()
        print(f"  Failed to read {kind}: {e}")
        print("")
        return

    print(f"  Loaded: {pdf_path.name}  ({total} pages)")

    print("  Enter page ranges separated by commas, e.g. 1-7, 12-13, 27-20")
    print(f"  Each range becomes its own {output_name}. 1-7 keeps page order; 7-1 reverses it.")
    if to_epub and not is_epub:
        print("  PDF pages will be rendered as images inside each EPUB book.")
    raw = input("Ranges: ").strip()
    if raw == SENTINEL:
        if epub_zf is not None:
            epub_zf.close()
        return _cancel()
    if not raw:
        if epub_zf is not None:
            epub_zf.close()
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
        if epub_zf is not None:
            epub_zf.close()
        print("  No valid ranges entered. Aborting.")
        print("")
        return

    stem = pdf_path.stem
    part_count = len(ranges)
    print(f"  Writing {part_count} part(s)...")
    failed = []
    written = 0
    part_rows = []
    try:
        for i, (a, b, descending) in enumerate(ranges):
            if cancel and cancel.is_set():
                print(f"  Cancelled. ({written} part(s) staged so far)")
                print("")
                return
            page_indices = list(range(a - 1, b - 2, -1) if descending else range(a - 1, b))
            try:
                if to_epub:
                    out_path = out / f"{stem}_part{i + 1}.epub"
                    if is_epub:
                        part_pages = [ZipImage(epub_zf, epub_members[p]) for p in page_indices]
                        if not build_epub(config, part_pages, out_path,
                                          f"{stem} - part {i + 1}", cancel, epub_direction):
                            print(f"  Cancelled. ({written} part(s) staged so far)")
                            print("")
                            return
                    else:
                        with tempfile.TemporaryDirectory(prefix="tankobon_split_pages_") as page_dir:
                            part_pages = render_pdf_pages_as_images(
                                pdf_path, [p + 1 for p in page_indices], page_dir,
                                f"part{i + 1:04d}", cancel)
                            if not build_epub(config, part_pages, out_path,
                                              f"{stem} - part {i + 1}", cancel):
                                print(f"  Cancelled. ({written} part(s) staged so far)")
                                print("")
                                return
                elif is_epub:
                    writer = PdfWriter()
                    for p in page_indices:
                        for page in PdfReader(io.BytesIO(image_to_pdf_bytes(epub_zf.read(epub_members[p])))).pages:
                            writer.add_page(page)
                    out_path = out / f"{stem}_part{i + 1}.pdf"
                    write_pdf_atomically(writer, out_path)
                else:
                    writer = PdfWriter()
                    for p in page_indices:
                        writer.add_page(reader.pages[p])
                    out_path = out / f"{stem}_part{i + 1}.pdf"
                    write_pdf_atomically(writer, out_path)
                direction = f" (reversed)" if descending else ""
                part_rows.append(f"    Part {i + 1}/{part_count}: pages {a}–{b}{direction}  →  {out_path.name}")
                written += 1
            except OSError as e:
                if is_no_space(e):
                    print(f"  ✖ Disk full after {written} part(s). Stopping.")
                    print_summary(config, copied=written, failed=failed or None, label="parts saved")
                    print("")
                    return
                failed.append((pdf_path, f"part {i + 1}: {e}"))
            except Exception as e:
                failed.append((pdf_path, f"part {i + 1}: {e}"))
    finally:
        if epub_zf is not None:
            epub_zf.close()

    with Section(config, f"  Parts ({len(part_rows)})") as s:
        for row in part_rows:
            s.add(row)
    print_summary(config, copied=written, failed=failed or None, label="parts saved")
    if finalize_output(config, out, cancel):
        print(f"  Done! {written} part(s) saved.")
    print(f"  → {shown(out)}")
    print("")


def status(config):
    src = get_input(config)
    out_base = Path(config.get("output", "")) / "output"

    start_section, end_section = section_callbacks()

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
        visible = [i for i in out_base.iterdir() if not i.name.startswith(".")]     # hide staging folders
        items = [i for i in visible if i.is_dir()]
        out_files = [i for i in visible if i.is_file()]
        parts = []
        if items:     parts.append(f"{len(items)} folder(s)")
        if out_files: parts.append(f"{len(out_files)} file(s)")
        print(f"Output  —  {', '.join(parts)}" if parts else "Output  —  empty")
        for f in sorted(out_files, key=lambda x: natural_sort_key(x.name)):
            tag = {".zip": "[zip]   ", ".cbz": "[cbz]   "}.get(f.suffix.lower(), "[file]  ")
            print(f"  {tag} {f.name}")
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

