"""Combining PDF and EPUB documents into a single output."""

import io
import tempfile
import zipfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from Archives import extract_archives
from Books import (
    PathImage,
    build_epub,
    epub_pages,
    image_to_pdf_bytes,
    render_pdf_pages_as_images,
    write_pdf_atomically,
)
from LogFormat import Section, log_scan, print_summary, size_suffix
from ManagerConfig import get_input
from ManagerOutput import finalize_output, get_output, shown
from ManagerSupport import _cancel, _check_disk_space, _get_run_name
from ToolNames import document_format
from Util import (
    DOCUMENT_EXTENSIONS,
    is_no_space,
    natural_sort_key,
    rel,
    resolve_sort,
    throttle_if_needed,
)


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
