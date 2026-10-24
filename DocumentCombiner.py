"""Combining PDF and EPUB documents into a single output."""

import io
import tempfile
import zipfile
from pathlib import Path

from PIL import Image
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


def _partial_failure_message(kept_pages, total_pages, page_errors):
    details = "; ".join(page_errors[:3])
    if len(page_errors) > 3:
        details += f"; and {len(page_errors) - 3} more page error(s)"
    return (f"partially read: kept {kept_pages} of {total_pages} page(s); "
            f"{len(page_errors)} page error(s): {details}")


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
    salvage_pages = bool(config.get("salvage_failed_document_pages", False))

    if as_epub:
        pages, direction = [], None
        with tempfile.TemporaryDirectory(prefix="tankobon_epub_pages_", dir=out) as page_dir:
            for doc_index, doc in enumerate(pdfs):
                if cancel and cancel.is_set():
                    print(f"  Cancelled. ({total_pages} pages combined and staged so far)")
                    print("")
                    return
                throttle_if_needed(config, cancel)
                document_pages = []
                page_errors = []
                expected_pages = 0
                document_direction = None
                try:
                    if doc.suffix.lower() == ".epub":
                        with zipfile.ZipFile(doc) as zf:
                            members, document_direction, _title = epub_pages(zf)
                            expected_pages = len(members)
                            if not members:
                                raise ValueError("no page images found (only image-based EPUBs are supported)")
                            for member_index, member in enumerate(members):
                                try:
                                    data = zf.read(member)
                                    with Image.open(io.BytesIO(data)) as image:
                                        image.load()
                                    suffix = Path(member).suffix.lower() or ".img"
                                    image_path = Path(page_dir) / f"doc{doc_index:04d}_{member_index:06d}{suffix}"
                                    image_path.write_bytes(data)
                                    document_pages.append(PathImage(image_path))
                                except Exception as error:
                                    if not salvage_pages or is_no_space(error):
                                        raise
                                    page_errors.append(f"page {member_index + 1}: {error}")
                    else:
                        reader = PdfReader(str(doc))
                        expected_pages = len(reader.pages)
                        if salvage_pages:
                            for page_number in range(1, expected_pages + 1):
                                if cancel and cancel.is_set():
                                    break
                                try:
                                    rendered = render_pdf_pages_as_images(
                                        doc, [page_number], page_dir,
                                        f"doc{doc_index:04d}_page{page_number:06d}", cancel)
                                    if not rendered:
                                        if cancel and cancel.is_set():
                                            break
                                        raise RuntimeError(f"could not render PDF page {page_number}")
                                    document_pages.extend(rendered)
                                except Exception as error:
                                    if is_no_space(error):
                                        raise
                                    page_errors.append(f"page {page_number}: {error}")
                        else:
                            document_pages = render_pdf_pages_as_images(
                                doc, range(1, expected_pages + 1), page_dir, f"doc{doc_index:04d}", cancel)

                    if cancel and cancel.is_set():
                        print(f"  Cancelled. ({total_pages} pages combined and staged so far)")
                        print("")
                        return
                    if not document_pages:
                        if page_errors:
                            raise ValueError(_partial_failure_message(0, expected_pages, page_errors))
                        raise ValueError("no readable pages found")
                    if not salvage_pages and len(document_pages) != expected_pages:
                        raise ValueError(
                            f"read {len(document_pages)} of {expected_pages} page(s)")
                    if page_errors:
                        failed.append((doc, _partial_failure_message(
                            len(document_pages), expected_pages, page_errors)))
                    pages.extend(document_pages)
                    total_pages += len(document_pages)
                    direction = direction or document_direction
                    page_count = len(document_pages)
                    detail = (f"partial; kept {page_count} of {expected_pages} page(s)"
                              if page_errors else f"{page_count} page(s)")
                    page_rows.append(
                        f"    {rel(doc, src)}  —  {detail}  (running total: {total_pages})")
                except Exception as e:
                    failed.append((doc, f"no pages included: {e}"))
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
            document_writer = PdfWriter()
            page_errors = []
            expected_pages = 0
            try:
                if pdf_path.suffix.lower() == ".epub":
                    with zipfile.ZipFile(pdf_path) as zf:
                        members, _direction, _title = epub_pages(zf)
                        if not members:
                            raise ValueError("no page images found (only image-based EPUBs are supported)")
                        expected_pages = len(members)
                        for page_index, member in enumerate(members):
                            try:
                                converted = PdfReader(io.BytesIO(image_to_pdf_bytes(zf.read(member))))
                                if not converted.pages:
                                    raise ValueError("image did not produce a PDF page")
                                for page in converted.pages:
                                    document_writer.add_page(page)
                            except Exception as error:
                                if not salvage_pages or is_no_space(error):
                                    raise
                                page_errors.append(f"page {page_index + 1}: {error}")
                else:
                    reader = PdfReader(str(pdf_path))
                    expected_pages = len(reader.pages)
                    for page_index in range(expected_pages):
                        try:
                            document_writer.add_page(reader.pages[page_index])
                        except Exception as error:
                            if not salvage_pages or is_no_space(error):
                                raise
                            page_errors.append(f"page {page_index + 1}: {error}")

                if cancel and cancel.is_set():
                    print(f"  Cancelled. ({total_pages} pages combined and staged so far)")
                    print("")
                    return
                if not document_writer.pages:
                    if page_errors:
                        raise ValueError(_partial_failure_message(0, expected_pages, page_errors))
                    raise ValueError("no readable pages found")

                start_page_count = len(writer.pages)
                for page_index, page in enumerate(document_writer.pages, 1):
                    try:
                        writer.add_page(page)
                    except Exception as error:
                        if salvage_pages and not is_no_space(error):
                            page_errors.append(f"page {page_index}: {error}")
                            continue
                        while len(writer.pages) > start_page_count:
                            writer.remove_page(-1)
                        raise

                added_pages = len(writer.pages) - start_page_count
                if not added_pages:
                    raise ValueError(_partial_failure_message(
                        0, expected_pages, page_errors or ["no pages could be added to the output"]))
                if page_errors:
                    failed.append((pdf_path, _partial_failure_message(
                        added_pages, expected_pages, page_errors)))
                total_pages += added_pages
                detail = (f"partial; kept {added_pages} of {expected_pages} page(s)"
                          if page_errors else f"{added_pages} page(s)")
                page_rows.append(
                    f"    {rel(pdf_path, src)}  —  {detail}  (running total: {total_pages})")
            except Exception as e:
                failed.append((pdf_path, f"no pages included: {e}"))
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
