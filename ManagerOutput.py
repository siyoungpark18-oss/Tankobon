"""Output naming, staging, archive production, and run recovery."""

import json
import os
import shutil
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path

import psutil

from ManagerConfig import get_input
from Util import (natural_sort_key, rel, fmt_size, total_size, is_no_space, throttle_if_needed,
                  DOCUMENT_EXTENSIONS, CBZ_IMAGE_EXTENSIONS, ALREADY_COMPRESSED)
from LogFormat import Section, print_summary
from ToolNames import folder_format

STAGING_PREFIX = ".tankobon_run_"
_RUNS = {}
_RUNS_LOCK = threading.Lock()
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
    """Choose a distinct path for this run, optionally replacing its previous output.

    Names reserved by earlier items in the current run receive numbered suffixes. In
    replace mode, an existing file at the selected name is replaced too, so a rerun
    with duplicate input names reuses the same base and numbered output names.
    """
    path = Path(path)
    reserved = reserved if reserved is not None else set()

    def key(candidate):
        return os.path.normcase(os.path.abspath(str(candidate))).casefold()

    suffix = "" if directory else path.suffix
    stem = path.name if directory else path.name[:-len(suffix)] if suffix else path.name
    candidate = path
    number = 2
    while (key(candidate) in reserved
           or (not replace and (candidate.exists() or candidate.is_symlink()))):
        candidate = path.with_name(f"{stem} ({number}){suffix}")
        number += 1
    if replace:
        _remove_path(candidate)
    reserved.add(key(candidate))
    return candidate


def unique_output_stem(parent, name, extension, reserved=None):
    target = unique_path(Path(parent) / f"{name}{extension}", reserved=reserved)
    return target.with_name(target.name[:-len(extension)]) if extension else target


def _write_run_marker(stage, named, target=None, preserve=False, cancelled=False):
    process = psutil.Process(os.getpid())
    marker = {"named": named, "preserve": preserve, "cancelled": cancelled, "pid": process.pid,
              "pid_created": process.create_time()}
    if target is not None:
        marker["target"] = str(target)
    (Path(stage) / ".tankobon_run.json").write_text(json.dumps(marker), encoding="utf-8")


def _read_run_marker(stage):
    try:
        return json.loads((Path(stage) / ".tankobon_run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _process_is_running(pid, created=None):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        process = psutil.Process(pid)
        if created is not None and abs(process.create_time() - float(created)) > 0.01:
            return False
        return process.is_running()
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True


def get_output(config, operation, run_name):
    """Create a private stage for a run; named output is published only on finalize."""
    root = Path(config["output"]) / "output"
    if run_name:
        run_name = validate_run_name(run_name)
    root.mkdir(parents=True, exist_ok=True)
    named = bool(config.get("sort_output", False) or run_name)
    if not named:
        staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=root))
        _write_run_marker(staging, named=False)
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
    _write_run_marker(staging, named=True, target=target)
    with _RUNS_LOCK:
        _RUNS[str(staging)] = {
            "root": root, "operation": operation, "thread": threading.get_ident(),
            "done": False, "named": True, "target": target, "replace": replace,
        }
    return staging


def _archive_mode(config):
    mode = folder_format(config)
    return mode if mode in ("zip", "cbz") else None


def _write_archive(config, files, base, dest_dir, stem, mode, cancel, replace, notes, partial_dir):
    files = sorted(files, key=lambda item: natural_sort_key(rel(item, base)))
    ext = ".zip"
    if mode == "cbz":
        others = sorted({f.suffix.lower() or "(no extension)" for f in files
                         if f.suffix.lower() not in CBZ_IMAGE_EXTENSIONS})
        if others:
            notes.append(f"  ⚠ {stem}: contains {', '.join(others)}, which a CBZ can't hold. Saved as .zip instead.")
        else:
            ext = ".cbz"
    try:
        if total_size(files) > shutil.disk_usage(dest_dir).free:
            return ("failed", "not enough free disk space")
    except OSError:
        pass

    fd, partial_name = tempfile.mkstemp(prefix=".tankobon_archive_", suffix=".partial",
                                        dir=partial_dir)
    os.close(fd)
    partial = Path(partial_name)
    cancelled = False
    try:
        with zipfile.ZipFile(partial, "w", allowZip64=True, strict_timestamps=False) as archive:
            for item in files:
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    break
                throttle_if_needed(config, cancel)
                method = zipfile.ZIP_STORED if item.suffix.lower() in ALREADY_COMPRESSED else zipfile.ZIP_DEFLATED
                archive.write(item, rel(item, base).replace(os.sep, "/"), compress_type=method)
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
        return ("failed", "disk full" if is_no_space(error) else str(error))


def _place_results(config, src, dest, loose_name, cancel=None):
    """Pack folders and move documents or loose results without archiving the outer output folder."""
    mode = _archive_mode(config)
    replace = config.get("replace_output", True)
    inplace = Path(src).resolve() == Path(dest).resolve()
    rows, notes, failed = [], [], []
    reserved_documents = set()
    loose_images, packed, cancelled = [], 0, False

    def handle(result, label, source_files_dir=None, source_files=()):
        nonlocal packed, cancelled
        if result[0] == "ok":
            packed += 1
            rows.append(f"    {label}  →  {result[1].name}  ({fmt_size(result[2])})")
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
                if mode and config.get("keep_documents_unarchived", True):
                    documents = sorted((f for f in entry.rglob("*") if f.is_file()
                                        and f.suffix.lower() in DOCUMENT_EXTENSIONS),
                                       key=lambda f: natural_sort_key(rel(f, entry)))
                    for doc in documents:
                        destination = unique_path(Path(dest) / doc.name, reserved=reserved_documents,
                                                  replace=replace)
                        shutil.move(str(doc), str(destination))
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
                    handle(_write_archive(config, files, entry, dest, entry.name, mode, cancel,
                                          replace, notes, src),
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
        handle(_write_archive(config, loose_images, src, dest, loose_name, mode, cancel,
                              replace, notes, src),
               loose_name, source_files=loose_images)
    if rows:
        with Section(config, f"  Archives ({len(rows)})") as section:
            for row in rows:
                section.add(row)
    for note in notes:
        print(note)
    cancelled = cancelled or (cancel is not None and cancel.is_set())
    if cancelled:
        if inplace:
            print("  Packing cancelled. The named output was not published.")
        else:
            print("  Packing cancelled. Completed results were kept in Output; remaining files were swept there.")
    if failed:
        print_summary(config, copied=packed, failed=failed, label="archived")
    elif packed:
        print(f"  Saved: {packed} archive(s) → {shown(dest)}")
    if not inplace:
        _sweep(src, dest, replace)
    return cancelled, bool(failed)


def _sweep(src, dest, replace):
    src, dest = Path(src), Path(dest)
    if not src.exists():
        return
    for partial in src.glob(".tankobon_archive_*.partial"):
        partial.unlink(missing_ok=True)
    for item in list(src.iterdir()):
        if item.name == ".tankobon_run.json":
            item.unlink(missing_ok=True)
            continue
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


def settle_runs(config, cancelled=False):
    """Keep failed named stages recoverable; discard explicitly cancelled named runs."""
    me = threading.get_ident()
    with _RUNS_LOCK:
        open_runs = [(path, record) for path, record in _RUNS.items()
                     if record["thread"] == me and not record["done"]]
    for path, record in open_runs:
        stage = Path(path)
        if record.get("preserve"):
            record["done"] = True
            continue
        if record.get("named"):
            has_results = any(item.name != ".tankobon_run.json" for item in stage.iterdir()) if stage.exists() else False
            if has_results and (cancelled or record.get("cancelled")):
                shutil.rmtree(stage, ignore_errors=True)
                print("  Partial named-run results were discarded; the named output was not replaced.")
            elif has_results:
                record["preserve"] = True
                _write_run_marker(stage, named=True, target=record.get("target"), preserve=True)
                print(f"  Incomplete named-run results were kept at {stage}; the named output was not replaced.")
            else:
                shutil.rmtree(stage, ignore_errors=True)
        else:
            _sweep(stage, record["root"], config.get("replace_output", True))
        record["done"] = True


def _output_parents(root):
    """Only stage folders and backups at Output or one operation folder below it."""
    parents = [Path(root)]
    try:
        parents.extend(item for item in Path(root).iterdir() if item.is_dir() and not item.is_symlink())
    except OSError:
        pass
    return parents


def recover_stale_runs(config):
    """Recover abandoned stages without walking large output trees or touching live runs."""
    output = config.get("output")
    root = Path(output) / "output" if output else None
    if root is None or not root.is_dir():
        return
    parents = _output_parents(root)
    stages = []
    for parent in parents:
        try:
            stages.extend(item for item in parent.iterdir()
                          if item.is_dir() and item.name.startswith(STAGING_PREFIX))
        except OSError:
            continue

    busy_targets = set()
    for stage in stages:
        record = _RUNS.get(str(stage), {})
        marker = _read_run_marker(stage)
        pid = record.get("pid", marker.get("pid"))
        created = record.get("pid_created", marker.get("pid_created"))
        if _process_is_running(pid, created):
            if marker.get("target"):
                busy_targets.add(str(Path(marker["target"])))
            continue
        if record.get("preserve") or marker.get("preserve"):
            print(f"  ⚠ An output could not be published and remains staged at {stage}")
            continue
        named = record.get("named", marker.get("named", False))
        if named:
            if marker.get("cancelled"):
                shutil.rmtree(stage, ignore_errors=True)
            elif any(item.name != ".tankobon_run.json" for item in stage.iterdir()):
                target = record.get("target", marker.get("target"))
                _write_run_marker(stage, named=True, target=target, preserve=True)
                print(f"  ⚠ An incomplete named run remains staged at {stage}; the named output was not replaced.")
            else:
                shutil.rmtree(stage, ignore_errors=True)
        else:
            _sweep(stage, root, config.get("replace_output", True))

    # Restore interrupted swaps and clean backup/marker files at the known output depths.
    for parent in parents:
        try:
            entries = list(parent.iterdir())
        except OSError:
            continue
        for backup in entries:
            if ".tankobon-backup-" not in backup.name:
                continue
            original_name = backup.name[1:].split(".tankobon-backup-", 1)[0]
            target = backup.with_name(original_name)
            if str(target) in busy_targets:
                continue
            if not target.exists() and not target.is_symlink():
                os.replace(backup, target)
            else:
                _remove_path(backup)
        for item in entries:
            if item.is_dir() and not item.name.startswith(STAGING_PREFIX):
                marker = item / ".tankobon_run.json"
                if marker.exists() and _read_run_marker(item).get("target") == str(item):
                    marker.unlink(missing_ok=True)


def _has_staged_results(stage, config, record):
    """Check whether a run contains an output item, ignoring its marker and empty setup folders."""
    stage = Path(stage)
    for item in stage.rglob("*"):
        if item.name == ".tankobon_run.json":
            continue
        if item.is_file() or item.is_symlink():
            return True

    # In uncompressed mode, the folder renamer can produce a meaningful folder tree
    # with only empty child folders. Other tools create empty directories as setup
    # before writing files, so those directories alone are not results.
    operation = str(record.get("operation", "")).casefold()
    if _archive_mode(config) is None and "renamer" in operation:
        for item in stage.iterdir():
            if item.is_dir() and not item.is_symlink() and next(item.iterdir(), None) is not None:
                return True
    return False


def finalize_output(config, out, cancel=None):
    """Pack and publish a staged run, then apply Input cleanup."""
    out = Path(out)
    with _RUNS_LOCK:
        record = _RUNS.get(str(out))
    if record is None:
        raise RuntimeError(f"No output staging record exists for {out}")
    if not (cancel is not None and cancel.is_set()) and not _has_staged_results(out, config, record):
        try:
            shutil.rmtree(out)
        except OSError as error:
            print(f"  ⚠ No results were produced, but the staging folder remains at {out}: {error}")
        record["done"] = True
        print("  No results were produced; output was left unchanged and Input was not cleared.")
        return False
    loose_name = Path(record["target"]).name if record.get("named") else record["operation"]
    cancelled, failed = _place_results(
        config, out, out if record.get("named") else record["root"], loose_name, cancel)
    cancelled = cancelled or (cancel is not None and cancel.is_set())
    if cancelled:
        record["cancelled"] = True
        if record.get("named"):
            _write_run_marker(out, named=True, target=record["target"], cancelled=True)
        if not record.get("named"):
            record["done"] = True
        return False
    if failed and record.get("named"):
        record["preserve"] = True
        record["done"] = True
        _write_run_marker(out, named=True, target=record["target"], preserve=True)
        print(f"  ⚠ Some results could not be packed. The incomplete run remains staged at {out}")
        return False
    if record.get("named"):
        try:
            _publish_named(out, record)
        except Exception as error:
            record["preserve"] = True
            _write_run_marker(out, named=True, target=record["target"], preserve=True)
            print(f"  ✖ Could not publish the completed output. It remains staged at {out}")
            raise RuntimeError(f"Could not publish output; completed files remain at {out}: {error}") from error
    record["done"] = True
    if failed:
        return False
    do_auto_clear(config)
    return True

def shown(out):
    out = Path(out)
    with _RUNS_LOCK:
        record = _RUNS.get(str(out))
    if record is None:
        return out
    return record.get("target", record["root"])
