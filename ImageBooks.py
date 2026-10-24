"""Build book documents from image files."""

from collections import Counter
import re

from Archives import extract_archives
from Books import save_book
from ImageSets import collect_direct_image_paths, collect_image_paths
from LogFormat import log_group, log_scan, print_summary, size_suffix
from ManagerConfig import get_input
from ManagerOutput import finalize_output, get_output, shown, unique_output_stem
from ManagerSupport import SENTINEL, _cancel, _check_disk_space, _get_run_name
from ToolNames import document_format, document_name
from Util import (
    IMAGE_EXTENSIONS,
    is_no_space,
    natural_sort_key,
    resolve_sort,
    throttle_if_needed,
)


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
                if unit is folder and subfolders:
                    raw_name = f"{folder.name} loose images"
                elif unit is folder:
                    raw_name = folder.name
                else:
                    raw_name = f"{folder.name} - {unit.name}"
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
