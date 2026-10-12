"""Safe extraction of zip and comic-book archives found in Input."""

import errno
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from LogFormat import Section, print_summary
from ManagerOutput import unique_path
from Util import (ARCHIVE_EXTENSIONS, int_cfg, fmt_size, rel, natural_sort_key, is_no_space,
                  throttle_if_needed)

_MAX_ARCHIVE_NESTING = 3
_JUNK_FILES = {".ds_store", "thumbs.db", "desktop.ini"}

def _is_junk_member(parts):
    last = parts[-1]
    return "__MACOSX" in parts or last.startswith("._") or last.lower() in _JUNK_FILES


def _member_name(info):
    """The real file name inside a zip. Old zip tools save names in a local encoding (very common
    for Japanese files) without saying so, and Python would show them as gibberish."""
    name = info.filename
    if info.flag_bits & 0x800 or name.isascii():              # 0x800 = the zip says "this is UTF-8"
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for encoding in ("utf-8", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return name



def _plan_members(zf, dest):
    """Works out where every entry goes. Entries that would land OUTSIDE `dest` ("../../x" tricks)
    are dropped, as is Mac/Windows junk. Returns ([(info, target_path), ...], unsafe_count)."""
    root = dest.resolve()
    plan, unsafe = [], 0
    for info in zf.infolist():
        parts = [p for p in _member_name(info).replace("\\", "/").split("/") if p not in ("", ".")]
        if not parts:
            continue
        if ".." in parts or re.match(r"^[A-Za-z]:", parts[0]):
            unsafe += 1
            continue
        if _is_junk_member(parts):
            continue
        target = dest.joinpath(*parts)
        try:
            target.resolve().relative_to(root)
        except ValueError:
            unsafe += 1
            continue
        plan.append((info, target))
    return plan, unsafe


def _extract_archive(config, archive, cancel=None):
    """Unpacks one zip next to itself as a folder named after the zip.
    Everything is unpacked into a hidden temporary folder first, so a failure or a Cancel never
    leaves a half-unpacked folder behind. A zip that is just one wrapper folder is flattened.
    Returns (folder, file_count, bytes, unsafe_count), or None if cancelled. Raises on failure."""
    parent = archive.parent
    staging = Path(tempfile.mkdtemp(prefix=".tankobon_unzip_", dir=parent))
    try:
        with zipfile.ZipFile(archive) as zf:
            plan, unsafe = _plan_members(zf, staging)
            files = [(i, t) for i, t in plan if not i.is_dir()]
            if not files:
                raise RuntimeError("the zip file contains no files")
            if any(i.flag_bits & 0x1 for i, _ in files):
                raise RuntimeError("the zip file is password-protected")
            need = sum(i.file_size for i, _ in files)
            reserve = int_cfg(config, "min_free_gb", 10) * (1024 ** 3)
            free = shutil.disk_usage(parent).free
            if need + reserve > free:
                raise OSError(errno.ENOSPC,
                              f"not enough disk space to unpack ({fmt_size(need)} needed, "
                              f"{fmt_size(free)} free, minimum free space is set in Preferences)")
            for info, target in plan:
                if cancel is not None and cancel.is_set():
                    return None
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as s, open(target, "wb") as d:
                    shutil.copyfileobj(s, d)
        kids = list(staging.iterdir())
        source = kids[0] if len(kids) == 1 and kids[0].is_dir() else staging
        final = unique_path(parent / archive.stem, directory=True)
        shutil.move(str(source), str(final))
        return final, len(files), need, unsafe
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def extract_archives(config, src, cancel=None):
    """Unpacks every .zip / .cbz in Input (and zips inside those). Returns False if cancelled."""
    if not config.get("zip_extract_input", True):
        return True
    rows, failed, attempted = [], [], set()
    unsafe_total = 0

    def find():
        return sorted((f for f in src.rglob("*")
                       if f.is_file() and f.suffix.lower() in ARCHIVE_EXTENSIONS and f not in attempted),
                      key=lambda x: natural_sort_key(rel(x, src)))

    for _ in range(_MAX_ARCHIVE_NESTING):
        archives = find()
        if not archives:
            break
        if not attempted:
            print(f"  Zip file(s) found in Input. Unpacking...")
        for archive in archives:
            attempted.add(archive)
            if cancel is not None and cancel.is_set():
                return False
            throttle_if_needed(config, cancel)
            try:
                result = _extract_archive(config, archive, cancel)
            except zipfile.BadZipFile:
                failed.append((archive, "not a valid zip file"))
                continue
            except OSError as e:
                failed.append((archive, "disk full" if is_no_space(e) and not e.strerror else (e.strerror or str(e))))
                continue
            except Exception as e:
                failed.append((archive, str(e)))
                continue
            if result is None:
                return False
            folder, count, size, unsafe = result
            unsafe_total += unsafe
            try:
                archive.unlink()
            except OSError as e:
                failed.append((archive, f"unpacked, but the zip could not be removed: {e}"))
            rows.append(f"    {rel(archive, src)}  →  {rel(folder, src)}/  ({count} file(s), {fmt_size(size)})")

    for archive in find():                                      # still there after the nesting limit
        failed.append((archive, f"nested more than {_MAX_ARCHIVE_NESTING} levels deep"))

    if rows:
        with Section(config, f"  Unpacked zip files ({len(rows)})") as s:
            for row in rows:
                s.add(row)
    if unsafe_total:
        print(f"  ⚠ {unsafe_total} unsafe path(s) inside zip files were skipped (they pointed outside the folder).")
    if rows or failed:
        print_summary(config, copied=len(rows), failed=failed or None, label="extracted")
    return True


