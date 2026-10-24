"""Image conversion operation."""

from collections import Counter
import shutil

from PIL import Image

from Archives import extract_archives
from LogFormat import log_scan, print_summary, size_suffix
from ManagerConfig import get_input
from ManagerOutput import finalize_output, get_output, shown, unique_path
from ManagerSupport import SENTINEL, _cancel, _check_disk_space, _get_run_name
from ToolNames import IMAGE_CONVERTER_FORMATS, folder_tool_name
from Util import (
    is_no_space,
    natural_sort_key,
    resolve_sort,
    throttle_if_needed,
)


def image_converter(config, cancel=None):
    """Convert supported images while preserving their relative folders."""
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
        raw = input("Format (jpg/png/webp, default jpg): ").strip()
        if raw == SENTINEL:                      # must be checked BEFORE .lower(), which changes the text
            return _cancel()
        fmt = raw.lower() or "jpg"
    fmt = {"jpeg": "jpg", "tif": "tiff"}.get(fmt, fmt)
    if fmt not in IMAGE_CONVERTER_FORMATS:
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
