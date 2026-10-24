"""Manager workflow for finding and removing duplicate images."""

import shutil

from Archives import extract_archives
from DuplicateDetection import (
    files_to_exclude,
    find_exact_duplicates,
    find_perceptual_duplicates,
)
from LogFormat import Section, log_scan, print_summary, size_suffix
from ManagerConfig import get_input
from ManagerOutput import finalize_output, get_output, shown
from ManagerSupport import SENTINEL, _cancel, _check_disk_space, _get_run_name
from ToolNames import folder_tool_name
from Util import (
    IMAGE_EXTENSIONS,
    int_cfg,
    is_no_space,
    natural_sort_key,
    rel,
    resolve_sort,
)


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

    if hash_method == "exact":
        print(f"  Scanning {len(all_files)} image(s)...")
        detection = find_exact_duplicates(all_files, cancel)

    else:
        PHASH_THRESHOLD = int_cfg(config, "phash_threshold", 4)
        print(f"  Scanning {len(all_files)} image(s) (threshold={PHASH_THRESHOLD})...")
        detection = find_perceptual_duplicates(all_files, PHASH_THRESHOLD, cancel)

    if detection.cancelled:
        print("  Cancelled.")
        print("")
        return
    hash_failed = detection.failures
    duplicates = detection.duplicates
    dup_groups = detection.groups

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

    exclude = files_to_exclude(dup_groups, duplicates, mode)
    if exclude is None:
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
