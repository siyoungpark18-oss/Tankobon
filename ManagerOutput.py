"""Output naming, staging, archive production, and run recovery."""

import json
import os
import shutil
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path

from ManagerConfig import do_auto_clear

STAGING_PREFIX = ".tankobon_run_"
_RUNS = {}
_RUNS_LOCK = threading.Lock()
_SUPPORT = {}


def configure_output_support(**support):
    """Inject the Manager and Log helpers needed to finalize tool output."""
    _SUPPORT.update(support)


def _helper(name):
    return _SUPPORT[name]


def _remove_path(path):
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def validate_run_name(run_name):
    """Reject names that could escape the output directory on supported platforms."""
    name = str(run_name).strip()
    if (name in (".", "..") or Path(name).is_absolute()
            or any(sep in name for sep in ("/", "\\", ":"))
            or any(ord(char) < 32 for char in name)):
        raise ValueError("Run names must be a single folder name without path separators.")
    return name


def unique_path(path, reserved=None, directory=False, replace=False):
    """Return one shared collision-safe output path, optionally replacing an existing item."""
    path = Path(path)
    reserved = reserved if reserved is not None else set()

    def key(candidate):
        return os.path.normcase(os.path.abspath(str(candidate))).casefold()

    if replace and not reserved:
        _remove_path(path)
        return path
    suffix = "" if directory else path.suffix
    stem = path.name if directory else path.name[:-len(suffix)] if suffix else path.name
    candidate = path
    number = 2
    while candidate.exists() or candidate.is_symlink() or key(candidate) in reserved:
        candidate = path.with_name(f"{stem} ({number}){suffix}")
        number += 1
    reserved.add(key(candidate))
    return candidate


def unique_output_stem(parent, name, extension, reserved=None):
    target = unique_path(Path(parent) / f"{name}{extension}", reserved=reserved)
    return target.with_name(target.name[:-len(extension)]) if extension else target


def get_output(config, operation, run_name):
    """Create a private stage for a run; named output is published only on finalize."""
    root = Path(config["output"]) / "output"
    if run_name:
        run_name = validate_run_name(run_name)
    root.mkdir(parents=True, exist_ok=True)
    named = bool(config.get("sort_output", False) or run_name)
    if not named:
        staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=root))
        with _RUNS_LOCK:
            _RUNS[str(staging)] = {
                "root": root, "operation": operation, "thread": threading.get_ident(),
                "done": False, "named": False,
            }
        return staging

    run_name = run_name or "Output"
    base = root / operation / run_name if config.get("sort_output", False) else root / run_name
    base.parent.mkdir(parents=True, exist_ok=True)
    replace = bool(config.get("replace_output", True))
    target = base if replace else unique_path(base, directory=True)
    staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=target.parent))
    (staging / ".tankobon_run.json").write_text(json.dumps({"named": True}), encoding="utf-8")
    with _RUNS_LOCK:
        _RUNS[str(staging)] = {
            "root": root, "operation": operation, "thread": threading.get_ident(),
            "done": False, "named": True, "target": target, "replace": replace,
        }
    return staging


def _archive_mode(config):
    mode = config.get("zip_compression", "cbz")
    return mode if mode in ("zip", "cbz") else None


def _write_archive(config, files, base, dest_dir, stem, mode, cancel, replace, notes):
    natural_sort_key = _helper("natural_sort_key")
    _rel = _helper("_rel")
    _total_size = _helper("_total_size")
    _is_no_space = _helper("_is_no_space")
    throttle_if_needed = _helper("throttle_if_needed")
    CBZ_IMAGE_EXTENSIONS = _helper("CBZ_IMAGE_EXTENSIONS")
    _ALREADY_COMPRESSED = _helper("_ALREADY_COMPRESSED")
    files = sorted(files, key=lambda item: natural_sort_key(_rel(item, base)))
    ext = ".zip"
    if mode == "cbz":
        others = sorted({f.suffix.lower() or "(no extension)" for f in files
                         if f.suffix.lower() not in CBZ_IMAGE_EXTENSIONS})
        if others:
            notes.append(f"  ⚠ {stem}: contains {', '.join(others)}, which a CBZ can't hold. Saved as .zip instead.")
        else:
            ext = ".cbz"
    try:
        if _total_size(files) > shutil.disk_usage(dest_dir).free:
            return ("failed", "not enough free disk space")
    except OSError:
        pass

    partial = Path(dest_dir) / f".tankobon_{threading.get_ident()}.partial"
    cancelled = False
    try:
        with zipfile.ZipFile(partial, "w", allowZip64=True, strict_timestamps=False) as archive:
            for item in files:
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    break
                throttle_if_needed(config, cancel)
                method = zipfile.ZIP_STORED if item.suffix.lower() in _ALREADY_COMPRESSED else zipfile.ZIP_DEFLATED
                archive.write(item, _rel(item, base).replace(os.sep, "/"), compress_type=method)
        if cancelled:
            partial.unlink(missing_ok=True)
            return ("cancelled",)
        with zipfile.ZipFile(partial) as archive:
            bad = archive.testzip()
        if bad:
            raise RuntimeError(f"zip check failed on '{bad}'")
        final = unique_path(Path(dest_dir) / f"{stem}{ext}", replace=replace)
        os.replace(partial, final)
        return ("ok", final, final.stat().st_size)
    except Exception as error:
        partial.unlink(missing_ok=True)
        return ("failed", "disk full" if _is_no_space(error) else str(error))


def _place_results(config, src, dest, loose_name, cancel=None):
    """Pack folders and move documents or loose results without archiving the outer output folder."""
    _Section = _helper("_Section")
    _fmt_size = _helper("_fmt_size")
    _print_summary = _helper("_print_summary")
    natural_sort_key = _helper("natural_sort_key")
    _rel = _helper("_rel")
    DOCUMENT_EXTENSIONS = _helper("DOCUMENT_EXTENSIONS")
    CBZ_IMAGE_EXTENSIONS = _helper("CBZ_IMAGE_EXTENSIONS")
    mode = _archive_mode(config)
    replace = config.get("replace_output", True)
    inplace = Path(src).resolve() == Path(dest).resolve()
    rows, notes, failed = [], [], []
    loose_images, packed, cancelled = [], 0, False

    def handle(result, label, source_files_dir=None, source_files=()):
        nonlocal packed, cancelled
        if result[0] == "ok":
            packed += 1
            rows.append(f"    {label}  →  {result[1].name}  ({_fmt_size(result[2])})")
            if source_files_dir is not None:
                shutil.rmtree(source_files_dir, ignore_errors=True)
                if source_files_dir.exists():
                    notes.append(f"  ⚠ {label}: archive saved, but the original folder could not be removed.")
            for item in source_files:
                try:
                    item.unlink()
                except OSError:
                    notes.append(f"  ⚠ {item.name}: archive saved, but the original file could not be removed.")
        elif result[0] == "cancelled":
            cancelled = True
        else:
            failed.append((Path(label), result[1]))

    entries = sorted((item for item in Path(src).iterdir() if item.name != ".tankobon_run.json"),
                     key=lambda item: natural_sort_key(item.name))
    todo = sum(1 for e in entries if e.is_dir() and not e.is_symlink()) + (
        1 if any(e.is_file() and e.suffix.lower() in CBZ_IMAGE_EXTENSIONS for e in entries) else 0)
    if mode and todo:
        print(f"  Packing {todo} item(s) as {mode}...")

    for entry in entries:
        if cancel is not None and cancel.is_set():
            cancelled = True
            break
        try:
            if entry.is_dir() and not entry.is_symlink():
                if config.get("keep_documents_unarchived", True):
                    documents = sorted((f for f in entry.rglob("*") if f.is_file()
                                        and f.suffix.lower() in DOCUMENT_EXTENSIONS),
                                       key=lambda f: natural_sort_key(_rel(f, entry)))
                    for doc in documents:
                        shutil.move(str(doc), str(unique_path(Path(dest) / doc.name, replace=replace)))
                    for folder in sorted((f for f in entry.rglob("*") if f.is_dir()),
                                         key=lambda f: len(f.parts), reverse=True):
                        try:
                            folder.rmdir()
                        except OSError:
                            pass
                if mode:
                    files = [f for f in entry.rglob("*") if f.is_file()]
                    if not files:
                        shutil.rmtree(entry, ignore_errors=True)
                        continue
                    handle(_write_archive(config, files, entry, dest, entry.name, mode, cancel, replace, notes),
                           entry.name, source_files_dir=entry)
                elif not inplace:
                    if any(entry.iterdir()):
                        shutil.move(str(entry), str(unique_path(Path(dest) / entry.name,
                                                                  directory=True, replace=replace)))
                    else:
                        shutil.rmtree(entry, ignore_errors=True)
            elif entry.is_file():
                if mode and entry.suffix.lower() in CBZ_IMAGE_EXTENSIONS:
                    loose_images.append(entry)
                elif not inplace:
                    shutil.move(str(entry), str(unique_path(Path(dest) / entry.name, replace=replace)))
        except Exception as error:
            failed.append((entry, str(error)))
        if cancelled:
            break

    if loose_images and mode and not cancelled:
        handle(_write_archive(config, loose_images, src, dest, loose_name, mode, cancel, replace, notes),
               loose_name, source_files=loose_images)
    if rows:
        with _Section(config, f"  Archives ({len(rows)})") as section:
            for row in rows:
                section.add(row)
    for note in notes:
        print(note)
    if cancelled:
        print("  Packing cancelled. The remaining results were saved as they are.")
    if failed:
        _print_summary(config, copied=packed, failed=failed, label="archived")
    elif packed:
        print(f"  Saved: {packed} archive(s) → {shown(dest)}")
    if not inplace:
        _sweep(src, dest, replace)


def _sweep(src, dest, replace):
    src, dest = Path(src), Path(dest)
    if not src.exists():
        return
    for item in list(src.iterdir()):
        try:
            unique = unique_path(dest / item.name, directory=item.is_dir() and not item.is_symlink(), replace=replace)
            shutil.move(str(item), str(unique))
        except Exception as error:
            print(f"  ⚠ Could not move {item.name} into the output folder: {error}")
    try:
        src.rmdir()
    except OSError:
        pass
    if src.exists():
        print(f"  ⚠ Some results are still in {src}")


def _publish_named(stage, record):
    target = Path(record["target"])
    backup = target.with_name(f".{target.name}.tankobon-backup-{uuid.uuid4().hex}")
    had_old = target.exists() or target.is_symlink()
    try:
        if had_old:
            os.replace(target, backup)
        os.replace(stage, target)
        try:
            (target / ".tankobon_run.json").unlink(missing_ok=True)
        except OSError:
            pass
    except Exception:
        if had_old and backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    else:
        if had_old:
            _remove_path(backup)


def settle_runs(config):
    """Finalize abandoned anonymous runs; discard incomplete named stages and retain prior output."""
    me = threading.get_ident()
    with _RUNS_LOCK:
        open_runs = [(path, record) for path, record in _RUNS.items()
                     if record["thread"] == me and not record["done"]]
    for path, record in open_runs:
        stage = Path(path)
        if record.get("named"):
            shutil.rmtree(stage, ignore_errors=True)
        else:
            _sweep(stage, record["root"], config.get("replace_output", True))
        record["done"] = True


def recover_stale_runs(config):
    """Clean leftover hidden stages after a crash without touching completed named output."""
    output = config.get("output")
    root = Path(output) / "output" if output else None
    if root is None or not root.is_dir():
        return
    stages = sorted((item for item in root.rglob(f"{STAGING_PREFIX}*") if item.is_dir()),
                    key=lambda item: len(item.parts), reverse=True)
    for stage in stages:
        record = _RUNS.get(str(stage))
        marker = stage / ".tankobon_run.json"
        named_stage = bool(record and record.get("named")) or marker.exists()
        if named_stage:
            shutil.rmtree(stage, ignore_errors=True)
        else:
            _sweep(stage, root, config.get("replace_output", True))
    # Restore an old named output if the process stopped between its backup and stage rename.
    for marker in root.rglob(".tankobon_run.json"):
        marker.unlink(missing_ok=True)
    for backup in root.rglob(".*.tankobon-backup-*"):
        if not backup.exists() and not backup.is_symlink():
            continue
        name = backup.name[1:].split(".tankobon-backup-", 1)[0]
        target = backup.with_name(name)
        if not target.exists() and not target.is_symlink():
            os.replace(backup, target)
        else:
            _remove_path(backup)


def finalize_output(config, out, cancel=None):
    """Pack a successful run, then publish its named stage and apply input cleanup."""
    out = Path(out)
    with _RUNS_LOCK:
        record = _RUNS.get(str(out))
    if record is not None and record.get("named"):
        _place_results(config, out, out, record["operation"], cancel)
        _publish_named(out, record)
        record["done"] = True
    elif record is not None:
        _place_results(config, out, record["root"], record["operation"], cancel)
        record["done"] = True
    else:
        _place_results(config, out, out, out.name, cancel)
    do_auto_clear(config)


def shown(out):
    out = Path(out)
    with _RUNS_LOCK:
        record = _RUNS.get(str(out))
    if record is None:
        return out
    return record.get("target", record["root"])
