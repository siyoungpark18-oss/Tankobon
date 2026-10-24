"""Export PDF and EPUB pages as image files."""

import zipfile

from pypdf import PdfReader

from Archives import extract_archives
from Books import epub_pages, export_epub_pages
from LogFormat import Section, log_scan, print_summary, size_suffix
from ManagerConfig import get_input
from ManagerOutput import finalize_output, get_output, shown, unique_path
from ManagerSupport import SENTINEL, _cancel, _check_disk_space, _get_run_name
from ToolNames import document_format, document_name
from Util import (
    DOCUMENT_EXTENSIONS,
    get_poppler_path,
    is_no_space,
    natural_sort_key,
    rel,
    resolve_sort,
    throttle_if_needed,
)


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
