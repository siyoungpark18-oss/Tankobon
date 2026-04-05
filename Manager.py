#IMPORTS——————————————————————————————————————————————————————————————————————————————————————————————————
from PIL import Image
from pathlib import Path
import re
import shutil
import img2pdf
import json
import hashlib
import time
import psutil
import errno
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

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff',} #DEFAULT IMAGE EXTENSIONS FOR MOST TOOLS

SENTINEL = "\x00CANCELLED\x00"

#DEFAULTS——————————————————————————————————————————————————————————————————————————————————————————————————
DEFAULTS = {
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
    "ui_mode": "dropdown",

}

#FUNCTIONS——————————————————————————————————————————————————————————————————————————————————————————————————
def load_config():
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r') as f:
            data = json.load(f)
        for k, v in DEFAULTS.items():
            data.setdefault(k, v)
        return data
    return DEFAULTS.copy()


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)


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


def throttle_if_needed(config):
    cpu_limit = config.get("throttle_cpu", 80)
    mem_limit = config.get("throttle_mem", 80)
    if cpu_limit == 0 and mem_limit == 0:
        return
    warned = False
    while True:
        cpu = psutil.cpu_percent(interval=0.2)
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
        min_gb = config.get("min_free_gb", 2) if config else 2
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
    _print = sub_print if sub_print is not None else print
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
            if sub_paths:
                _print(f"    {item.name}/  →  {len(sub_paths)} image(s)")
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


def _print_summary(copied=0, failed=None, skipped=None, label="processed"):
    start_section, end_section = _get_log_section_fns()
    parts = [f"  {label}: {copied}"]
    if failed:
        parts.append(f"Failed: {len(failed)}")
    if skipped:
        parts.append(f"Skipped: {len(skipped)}")
    print("  " + "   ".join(parts))
    if failed:
        start_section(f"  Failed files ({len(failed)})")
        for p, reason in failed:
            print(f"    {p.name}: {reason}")
        end_section()
    if skipped:
        non_type_skips = [(p, r) for p, r in skipped if r != "unsupported type"]
        if non_type_skips:
            start_section(f"  Skipped files ({len(non_type_skips)})")
            for p, reason in non_type_skips:
                print(f"    {p.name}: {reason}")
            end_section()
        type_skips = len(skipped) - len(non_type_skips)
        if type_skips:
            print(f"  {type_skips} file(s) skipped (unsupported type)")


def _get_log_section_fns():
    import sys as _sys
    log = _sys.stdout
    if hasattr(log, 'start_section') and hasattr(log, 'end_section'):
        return log.start_section, log.end_section
    return lambda header: None, lambda: None


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


def folders_to_pdf(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("Folders to PDF")
    print(f"  Input:  {src}")
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

    start_section, end_section = _get_log_section_fns()

    if mode == "combine":
        all_paths = []
        all_skipped = []
        for i, folder in enumerate(folders):
            if cancel and cancel.is_set():
                print("  Cancelled.")
                print("")
                return
            throttle_if_needed(config)
            start_section(f"[{folder.name}]")
            paths, skipped = collect_image_paths(folder, IMAGE_EXTENSIONS, sub_print=print, use_sort=use_sort)
            end_section()
            all_paths.extend(paths)
            all_skipped.extend(skipped)
            print(f"  [{folder.name}]  {len(paths)} image(s)"
                  + (f"  {len(skipped)} skipped" if skipped else ""))

        print(f"  Total: {len(all_paths)} images across {len(folders)} folders")

        if all_paths:
            try:
                save_pdf(all_paths, out / "output.pdf")
            except OSError as e:
                if _is_no_space(e):
                    print(f"  ✖ Job stopped — disk full. PDF may be incomplete.")
                    print("")
                    return
                raise
            _print_summary(copied=len(all_paths), skipped=all_skipped or None, label="converted")
            do_auto_clear(config)
            print(f"  Done! → {out}")
            print("")
        else:
            print("  No images found in any folder.")
            if all_skipped:
                _print_summary(skipped=all_skipped)
            print("")

    else:
        total_converted = 0
        total_skipped = []
        for folder in folders:
            if cancel and cancel.is_set():
                print(f"  Cancelled. ({total_converted} PDFs saved so far)")
                print("")
                return
            throttle_if_needed(config)
            subfolders = sorted(
                [f for f in folder.iterdir() if f.is_dir()],
                key=lambda x: natural_sort_key(x.name)
            ) if use_sort else [f for f in folder.iterdir() if f.is_dir()]
            units = subfolders if subfolders else [folder]
            start_section(f"[{folder.name}]")
            folder_skipped = []
            unit_data = []
            for unit in units:
                paths, skipped = collect_image_paths(unit, IMAGE_EXTENSIONS, sub_print=print, use_sort=use_sort)
                folder_skipped.extend(skipped)
                skip_str = f"  {len(skipped)} skipped" if skipped else ""
                print(f"    {unit.name}/  →  {len(paths)} image(s){skip_str}")
                unit_data.append((unit, paths, skipped))
            total_img = sum(len(p) for _, p, _ in unit_data)
            skip_total = f"  {len(folder_skipped)} skipped" if folder_skipped else ""
            print(f"  [{folder.name}]  {total_img} image(s){skip_total}")
            end_section()
            for unit, paths, skipped in unit_data:
                if not paths:
                    continue
                safe_name = re.sub(r'[^\w\s\-.]', '', unit.name).strip() or unit.name
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
            total_skipped.extend(folder_skipped)
        _print_summary(copied=total_converted, skipped=total_skipped or None, label="PDFs saved")
        do_auto_clear(config)
        print(f"  Done! → {out}")
        print("")


def images_to_pdf(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("Images to PDF")
    print(f"  Input:  {src}")
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
            _print_summary(skipped=skipped)
        print("")
        return

    from collections import Counter
    ext_counts = Counter(f.suffix.lower() for f in image_files)
    summary = "  ".join(f"{v}× {k}" for k, v in sorted(ext_counts.items()))
    print(f"  Found {len(image_files)} images:  {summary}")
    if cancel and cancel.is_set():
        print("  Cancelled.")
        print("")
        return
    try:
        save_pdf(image_files, out / "output.pdf")
    except OSError as e:
        if _is_no_space(e):
            print(f"  ✖ Job stopped — disk full. PDF may be incomplete.")
            print("")
            return
        raise
    _print_summary(copied=len(image_files), skipped=skipped or None, label="converted")
    do_auto_clear(config)
    print(f"  Done! → {out}")
    print("")


def folder_renamer(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("Folder Renamer")
    print(f"  Input:  {src}")
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
    print("  Modes: 1=Prefix  2=Suffix  3=Replace  4=Extract Number")
    mode = config.get("default_folder_renamer_mode", "ask")
    if mode == "ask":
        mode = input("Choose mode (1-4, default 4): ").strip() or "4"
        if mode == SENTINEL:
            return _cancel()
        mode = {"1": "prefix", "2": "suffix", "3": "replace", "4": "extract number"}.get(mode, mode)
    else:
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
    start_section, end_section = _get_log_section_fns()
    start_section(f"  Preview ({len(preview)})")
    for old, new in preview:
        print(f"    {old.name}  →  {new.name}")
    end_section()

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
                _print_summary(copied=copied, failed=failed or None,
                               skipped=skipped or None, label="renamed")
                print("")
                return
            failed.append((old, str(e)))
        except Exception as e:
            failed.append((old, str(e)))

    _print_summary(copied=copied, failed=failed or None,
                   skipped=skipped or None, label="renamed")
    do_auto_clear(config)
    print(f"  Done! → {out}")
    print("")


def file_renamer(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("File Renamer")
    print(f"  Input:  {src}")
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

    print(f"  Found {len(items)} file(s).")
    print("  Modes: 1=Prefix  2=Suffix  3=Replace  4=Sequence")
    mode = config.get("default_file_renamer_mode", "ask")
    if mode == "ask":
        mode = input("Choose mode (1-4): ").strip()
        if mode == SENTINEL:
            return _cancel()
        mode = {"1": "prefix", "2": "suffix", "3": "replace", "4": "sequence"}.get(mode, mode)
    else:
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
        start = int(start) if start else 1
        pad   = int(pad)   if pad   else 3
        def _seq_name(base, i, ext):
            num = str(start + i).zfill(pad)
            return f"{base}{num}{ext}" if base else f"{num}{ext}"
        preview = [(f, out / f.relative_to(src).parent / _seq_name(param1, i, f.suffix))
                   for i, f in enumerate(items)]
    else:
        print("  Invalid mode.")
        print("")
        return

    start_section, end_section = _get_log_section_fns()
    start_section(f"  Preview ({len(preview)})")
    for old, new in preview:
        print(f"    {old.name}  →  {new.name}")
    end_section()

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
                _print_summary(copied=copied, failed=failed or None, label="renamed")
                print("")
                return
            failed.append((old, str(e)))
        except Exception as e:
            failed.append((old, str(e)))

    _print_summary(copied=copied, failed=failed or None, label="renamed")
    do_auto_clear(config)
    print(f"  Done! → {out}")
    print("")


def combine_image_sets(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("Combine Image Sets")
    print(f"  Input:  {src}")
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

    start_section, end_section = _get_log_section_fns()

    for folder in folders:
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({counter - 1} images combined so far)")
            print("")
            return
        throttle_if_needed(config)
        subfolders = sorted([f for f in folder.iterdir() if f.is_dir()],
                            key=lambda x: natural_sort_key(x.name)) if use_sort else [f for f in folder.iterdir() if f.is_dir()]
        targets = [(f"{sub.name}", sub) for sub in subfolders] if subfolders else [(folder.name, folder)]

        start_section(f"[{folder.name}]")
        for label, target in targets:
            images, skipped = collect_images(target)
            total_skipped.extend(skipped)
            start_idx = counter
            for img in images:
                dest = out / f"{str(counter).zfill(4)}{img.suffix}"
                try:
                    shutil.copy2(img, dest)
                    counter += 1
                except OSError as e:
                    if _is_no_space(e):
                        end_section()
                        print(f"  ✖ Disk full after {counter - 1} image(s). Stopping.")
                        _print_summary(copied=counter - 1, failed=total_failed or None,
                                       skipped=total_skipped or None, label="combined")
                        print("")
                        return
                    total_failed.append((img, str(e)))
                except Exception as e:
                    total_failed.append((img, str(e)))
            print(f"    [{label}]  {len(images)} image(s)  →  {str(start_idx).zfill(4)}–{str(counter - 1).zfill(4)}")
        end_section()

    _print_summary(copied=counter - 1, failed=total_failed or None,
                   skipped=total_skipped or None, label="combined")
    do_auto_clear(config)
    print(f"  Total: {counter - 1} images combined.")
    print(f"  Done! → {out}")
    print("")


def image_converter(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("Image Converter")
    print(f"  Input:  {src}")
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()

    fmt = config.get("default_img_fmt", "ask")
    if fmt == "ask":
        fmt = input("Format (jpg/png/bmp/tiff, default jpg): ").strip().lower()
        if fmt == SENTINEL:
            return _cancel()
        fmt = fmt or "jpg"
    else:
        print(f"  Format: {fmt}")

    pillow_fmt = "JPEG" if fmt == "jpg" else fmt.upper()
    ext        = ".jpg" if fmt == "jpg" else f".{fmt}"

    out = get_output(config, "converted", run_name)
    print(f"  Output: {out}")
    if not _check_disk_space(out, config):
        return

    converter_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.webp'}
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
            _print_summary(skipped=skipped)
        print("")
        return

    from collections import Counter
    ext_counts = Counter(f.suffix.lower() for f in images)
    summary = "  ".join(f"{v}× {k}" for k, v in sorted(ext_counts.items()))
    print(f"  Found {len(images)} images:  {summary}")
    print(f"  Converting all → {ext} ...")

    converted = copied = 0
    failed = []
    for i, img_path in enumerate(images):
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({converted + copied} images processed so far)")
            print("")
            return
        throttle_if_needed(config)
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
                    _print_summary(copied=converted + copied, failed=failed or None,
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
                    _print_summary(copied=converted + copied, failed=failed or None,
                                   skipped=skipped or None, label="converted")
                    print("")
                    return
                failed.append((img_path, str(e)))
            except Exception as e:
                failed.append((img_path, str(e)))

    _print_summary(copied=converted + copied, failed=failed or None,
                   skipped=skipped or None, label="converted")
    do_auto_clear(config)
    print(f"  Done! → {out}")
    print("")


def find_duplicates(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("Find Duplicates")
    print(f"  Input:  {src}")
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "find duplicates", run_name)
    print(f"  Output: {out}")
    if not _check_disk_space(out, config):
        return

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

    print(f"  Hashing {len(all_files)} image(s)...")

    file_digests = {}
    hashes = {}
    duplicates = set()
    hash_failed = []
    for i, f in enumerate(all_files):
        if cancel and cancel.is_set():
            print("  Cancelled.")
            print("")
            return
        try:
            digest = hashlib.lib.sha256(f.read_bytes()).hexdigest()
            file_digests[f] = digest
            if digest in hashes:
                duplicates.add(f)
            else:
                hashes[digest] = f
        except Exception as e:
            hash_failed.append((f, str(e)))

    if not duplicates:
        print(f"  No duplicates found across {len(all_files)} images.")
        _print_summary(copied=len(all_files), failed=hash_failed or None,
                       skipped=skipped or None, label="scanned")
        print("")
        return

    print(f"  Found {len(duplicates)} duplicate(s) out of {len(all_files)} images.")

    mode = config.get("default_dedupe_mode", "ask")
    if mode == "ask":
        mode = input("Mode? 1=Keep one of each (default)  2=Remove all instances: ").strip()
        if mode == SENTINEL:
            return _cancel()
        mode = {"1": "keep one copy", "2": "delete all"}.get(mode, mode)
    else:
        print(f"  Mode: {mode}")

    if mode == "delete all":
        duped_digests = {file_digests[f] for f in duplicates}
        exclude = {f for f, d in file_digests.items() if d in duped_digests}
    elif mode == "keep one copy":
        exclude = duplicates
    else:
        print("  Invalid mode.")
        print("")
        return

    import sys as _sys
    log = _sys.stdout
    start_section, end_section = _get_log_section_fns()
    start_section(f"  Excluding ({len(exclude)}) — expand and hover over filenames to preview")
    for f in sorted(exclude, key=lambda x: natural_sort_key(x.name)):
        if hasattr(log, 'write_with_preview'):
            log.write_with_preview(f"      {f.name}", f)
        else:
            print(f"    {f.name}")
    end_section()
    print(f"  {len(exclude)} image(s) will be excluded.")

    keep_list = [f for f in all_files if f not in exclude]
    copied = 0
    failed = []
    for i, f in enumerate(keep_list):
        dest = out / f.relative_to(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(str(f), dest)
            copied += 1
        except OSError as e:
            if _is_no_space(e):
                print(f"  ✖ Disk full after {copied} image(s). Stopping.")
                _print_summary(copied=copied, failed=failed or None,
                               skipped=skipped or None, label="copied")
                print("")
                return
            failed.append((f, str(e)))
        except Exception as e:
            failed.append((f, str(e)))

    _print_summary(copied=copied, failed=failed or None,
                   skipped=skipped or None, label="copied")
    do_auto_clear(config)
    print(f"  Done! → {out}")
    print("")


def pdf_combiner(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("PDF Combiner")
    print(f"  Input:  {src}")
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
            _print_summary(skipped=skipped)
        print("")
        return

    print(f"  Found {len(pdfs)} PDF(s). Combining...")
    writer = PdfWriter()
    total_pages = 0
    failed = []
    for i, pdf_path in enumerate(pdfs):
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({total_pages} pages combined so far)")
            print("")
            return
        throttle_if_needed(config)
        try:
            reader = PdfReader(str(pdf_path))
            for page in reader.pages:
                writer.add_page(page)
            total_pages += len(reader.pages)
            print(f"  [{pdf_path.name}]  {len(reader.pages)} page(s)  (running total: {total_pages})")
        except Exception as e:
            failed.append((pdf_path, str(e)))

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

    _print_summary(copied=len(pdfs) - len(failed), failed=failed or None,
                   skipped=skipped or None, label="combined")
    do_auto_clear(config)
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
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()

    fmt = config.get("default_pdf_to_images_fmt", "ask")
    if fmt == "ask":
        raw = input("Format (jpg/png, default jpg): ").strip().lower()
        if raw == SENTINEL:
            return _cancel()
        fmt = raw or "jpg"
    else:
        print(f"  Format: {fmt}")
    if fmt not in ("jpg", "png"):
        print("  Invalid format.")
        print("")
        return

    dpi = config.get("default_dpi", "ask")
    if dpi == "ask":
        raw = input("DPI (default 72): ").strip()
        if raw == SENTINEL:
            return _cancel()
        dpi = int(raw) if raw else 72
    else:
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
            _print_summary(skipped=skipped)
        print("")
        return

    total_page_count = 0
    for pdf_path in pdfs:
        try:
            total_page_count += len(PdfReader(str(pdf_path)).pages)
        except Exception:
            pass

    print(f"  Found {len(pdfs)} PDF(s), {total_page_count} pages total.")
    total_pages = 0
    failed = []
    for pdf_path in pdfs:
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({total_pages} pages exported so far)")
            print("")
            return
        pdf_out = out / pdf_path.stem
        pdf_out.mkdir(exist_ok=True)
        print(f"  Converting: {pdf_path.name}  (@ {dpi} DPI)...")
        print(f"  Note: cancelling will stop after the current page finishes.")
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
            throttle_if_needed(config)
            try:
                pages = convert_from_path(str(pdf_path), dpi=dpi, first_page=i + 1, last_page=i + 1, poppler_path=_POPPLER_PATH)
                dest = pdf_out / f"{pdf_path.stem}_{str(i + 1).zfill(4)}.{fmt}"
                pages[0].save(str(dest), "JPEG" if fmt == "jpg" else "PNG")
            except OSError as e:
                if _is_no_space(e):
                    print(f"  ✖ Disk full after {total_pages + i} page(s). Stopping.")
                    _print_summary(copied=total_pages + i, failed=failed or None,
                                   skipped=skipped or None, label="pages exported")
                    print("")
                    return
                failed.append((pdf_path, f"page {i+1}: {e}"))
            except Exception as e:
                failed.append((pdf_path, f"page {i+1}: {e}"))
        total_pages += page_count
        print(f"  [{pdf_path.name}]  {page_count} page(s)  →  {pdf_out.name}/")

    _print_summary(copied=total_pages, failed=failed or None,
                   skipped=skipped or None, label="pages exported")
    do_auto_clear(config)
    print(f"  Total: {len(pdfs)} PDF(s), {total_pages} page(s) exported.")
    print(f"  Done! → {out}")
    print("")


def pdf_splitter(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("PDF Splitter")
    print(f"  Input:  {src}")
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "pdf split", run_name)
    print(f"  Output: {out}")
    if not _check_disk_space(out, config):
        return
    print(f"  Note: cannot be cancelled once PDF conversion starts.")

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
        print(f"  Multiple PDFs found, using first: {pdfs[0].name}")
    pdf_path = pdfs[0]

    try:
        reader = PdfReader(str(pdf_path))
    except Exception as e:
        print(f"  Failed to read PDF: {e}")
        print("")
        return

    total = len(reader.pages)
    print(f"  Loaded: {pdf_path.name}  ({total} pages)")
    print(f"  Enter page numbers to split after. Empty input = done.")
    print(f"  Valid range: 1–{total - 1}")

    splits = [0]
    while True:
        cmd = input(f"Split after page (1-{total - 1}), or Enter to finish: ").strip().lower()
        if cmd == SENTINEL:
            return _cancel()
        if cmd in ("exit", ""):
            break
        try:
            page_num = int(cmd)
            if page_num < 1 or page_num >= total:
                print(f"  Must be between 1 and {total - 1}.")
                continue
            splits.append(page_num)
            print(f"  ✓ Split marked after page {page_num}. ({len(splits) - 1} split(s) so far)")
        except ValueError:
            print("  Invalid input.")

    splits = sorted(set(splits))
    splits.append(total)

    if len(splits) < 2:
        print("  No splits made.")
        print("")
        return

    stem = pdf_path.stem
    part_count = len(splits) - 1
    print(f"  Writing {part_count} part(s)...")
    failed = []
    written = 0
    for i in range(part_count):
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({written} part(s) saved so far)")
            print("")
            return
        start, end = splits[i], splits[i + 1]
        try:
            writer = PdfWriter()
            for p in range(start, end):
                writer.add_page(reader.pages[p])
            out_path = out / f"{stem}_part{i + 1}.pdf"
            with open(out_path, "wb") as f:
                writer.write(f)
            print(f"  Part {i + 1}/{part_count}: pages {start + 1}–{end}  →  {out_path.name}")
            written += 1
        except OSError as e:
            if _is_no_space(e):
                print(f"  ✖ Disk full after {written} part(s). Stopping.")
                _print_summary(copied=written, failed=failed or None, label="parts saved")
                print("")
                return
            failed.append((pdf_path, f"part {i+1}: {e}"))
        except Exception as e:
            failed.append((pdf_path, f"part {i+1}: {e}"))

    _print_summary(copied=written, failed=failed or None, label="parts saved")
    do_auto_clear(config)
    print(f"  Done! {written} part(s) saved.")
    print(f"  → {out}")
    print("")


def status(config):
    src = get_input(config)
    out_base = Path(config["output"]) / "output"

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
            print(f"  [file]   {f.name}")
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

All output goes to output/
""")


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
