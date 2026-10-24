"""Image collection and combine-image-sets operations."""

import shutil

from Archives import extract_archives
from LogFormat import log_group, print_summary
from ManagerConfig import get_input
from ManagerOutput import finalize_output, get_output, shown
from ManagerSupport import _cancel, _check_disk_space, _get_run_name
from ToolNames import folder_tool_name
from Util import (
    IMAGE_EXTENSIONS,
    is_no_space,
    natural_sort_key,
    rel,
    resolve_sort,
    throttle_if_needed,
)


def collect_image_paths(folder, image_extensions, use_sort=True):
    """Collect supported image files recursively, optionally using natural order."""
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
    """Collect supported files directly inside a folder without descending."""
    items = sorted(folder.iterdir(), key=lambda x: natural_sort_key(x.name)) if use_sort else list(folder.iterdir())
    paths, skipped = [], []
    for item in items:
        if item.is_file():
            if item.suffix.lower() in image_extensions:
                paths.append(item)
            else:
                skipped.append((item, "unsupported type"))
    return paths, skipped


def combine_image_sets(config, cancel=None):
    """Flatten image sets from top-level folders into one numbered output set."""
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
