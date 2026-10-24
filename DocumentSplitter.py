"""Split PDF and image-based EPUB documents into page-range parts."""

import io
import tempfile
import zipfile

from pypdf import PdfReader, PdfWriter

from Archives import extract_archives
from Books import (
    ZipImage,
    build_epub,
    epub_pages,
    image_to_pdf_bytes,
    render_pdf_pages_as_images,
    write_pdf_atomically,
)
from LogFormat import Section, print_summary
from ManagerConfig import get_input
from ManagerOutput import finalize_output, get_output, shown
from ManagerSupport import SENTINEL, _cancel, _check_disk_space, _get_run_name
from ToolNames import document_format
from Util import DOCUMENT_EXTENSIONS, is_no_space, natural_sort_key, resolve_sort, throttle_if_needed


def document_splitter(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    output_format = document_format(config)
    output_name = output_format.upper()
    print(f"{output_name} Splitter")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, f"{output_format} split", run_name)
    print(f"  Output: {shown(out)}")
    if not _check_disk_space(out, config):
        return

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    pdfs = sorted(
        [f for f in src.rglob("*") if f.is_file() and f.suffix.lower() in DOCUMENT_EXTENSIONS],
        key=lambda x: natural_sort_key(str(x.relative_to(src)))
    ) if use_sort else [f for f in src.rglob("*") if f.is_file() and f.suffix.lower() in DOCUMENT_EXTENSIONS]

    if not pdfs:
        print("  No PDF or EPUB found in Input!")
        print("")
        return
    if len(pdfs) > 1:
        print(f"  Multiple files found, only the first one: {pdfs[0].name} will be used")
    pdf_path = pdfs[0]
    is_epub = pdf_path.suffix.lower() == ".epub"
    kind = "EPUB" if is_epub else "PDF"
    to_epub = output_format == "epub"

    reader, epub_zf, epub_members, epub_direction = None, None, [], None
    try:
        if is_epub:
            epub_zf = zipfile.ZipFile(pdf_path)
            epub_members, epub_direction, _title = epub_pages(epub_zf)
            if not epub_members:
                raise ValueError("no page images found (only image-based EPUBs are supported)")
            total = len(epub_members)
        else:
            reader = PdfReader(str(pdf_path))
            total = len(reader.pages)
    except Exception as e:
        if epub_zf is not None:
            epub_zf.close()
        print(f"  Failed to read {kind}: {e}")
        print("")
        return

    print(f"  Loaded: {pdf_path.name}  ({total} pages)")

    print("  Enter page ranges separated by commas, e.g. 1-7, 12-13, 27-20")
    print(f"  Each range becomes its own {output_name}. 1-7 keeps page order; 7-1 reverses it.")
    if to_epub and not is_epub:
        print("  PDF pages will be rendered as images inside each EPUB book.")
    raw = input("Ranges: ").strip()
    if raw == SENTINEL:
        if epub_zf is not None:
            epub_zf.close()
        return _cancel()
    if not raw:
        if epub_zf is not None:
            epub_zf.close()
        print("  No ranges entered. Aborting.")
        print("")
        return

    ranges = []
    invalid = []
    reversed_ranges = []
    for s in [x.strip() for x in raw.split(",")]:
        parts = s.split("-")
        if len(parts) != 2:
            invalid.append(s)
            continue
        try:
            a, b = int(parts[0].strip()), int(parts[1].strip())
        except ValueError:
            invalid.append(s)
            continue
        if a < 1 or a > total or b < 1 or b > total:
            invalid.append(s)
            continue
        if a > b:
            reversed_ranges.append(s)
            ranges.append((a, b, True))
        else:
            ranges.append((a, b, False))

    if invalid:
        print(f"  ⚠ Skipped invalid or out-of-range: {', '.join(invalid)}")
    if reversed_ranges:
        print(f"  ⚠ Reversed range(s) will be processed in descending order: {', '.join(reversed_ranges)}")
    if not ranges:
        if epub_zf is not None:
            epub_zf.close()
        print("  No valid ranges entered. Aborting.")
        print("")
        return

    stem = pdf_path.stem
    part_count = len(ranges)
    print(f"  Writing {part_count} part(s)...")
    failed = []
    written = 0
    part_rows = []
    try:
        for i, (a, b, descending) in enumerate(ranges):
            if cancel and cancel.is_set():
                print(f"  Cancelled. ({written} part(s) staged so far)")
                print("")
                return
            page_indices = list(range(a - 1, b - 2, -1) if descending else range(a - 1, b))
            try:
                if to_epub:
                    out_path = out / f"{stem}_part{i + 1}.epub"
                    if is_epub:
                        part_pages = [ZipImage(epub_zf, epub_members[p]) for p in page_indices]
                        if not build_epub(config, part_pages, out_path,
                                          f"{stem} - part {i + 1}", cancel, epub_direction):
                            print(f"  Cancelled. ({written} part(s) staged so far)")
                            print("")
                            return
                    else:
                        with tempfile.TemporaryDirectory(prefix="tankobon_split_pages_") as page_dir:
                            part_pages = render_pdf_pages_as_images(
                                pdf_path, [p + 1 for p in page_indices], page_dir,
                                f"part{i + 1:04d}", cancel)
                            if not build_epub(config, part_pages, out_path,
                                              f"{stem} - part {i + 1}", cancel):
                                print(f"  Cancelled. ({written} part(s) staged so far)")
                                print("")
                                return
                elif is_epub:
                    writer = PdfWriter()
                    for p in page_indices:
                        for page in PdfReader(io.BytesIO(image_to_pdf_bytes(epub_zf.read(epub_members[p])))).pages:
                            writer.add_page(page)
                    out_path = out / f"{stem}_part{i + 1}.pdf"
                    write_pdf_atomically(writer, out_path)
                else:
                    writer = PdfWriter()
                    for p in page_indices:
                        writer.add_page(reader.pages[p])
                    out_path = out / f"{stem}_part{i + 1}.pdf"
                    write_pdf_atomically(writer, out_path)
                direction = f" (reversed)" if descending else ""
                part_rows.append(f"    Part {i + 1}/{part_count}: pages {a}–{b}{direction}  →  {out_path.name}")
                written += 1
            except OSError as e:
                if is_no_space(e):
                    print(f"  ✖ Disk full after {written} part(s). Stopping.")
                    print_summary(config, copied=written, failed=failed or None, label="parts saved")
                    print("")
                    return
                failed.append((pdf_path, f"part {i + 1}: {e}"))
            except Exception as e:
                failed.append((pdf_path, f"part {i + 1}: {e}"))
    finally:
        if epub_zf is not None:
            epub_zf.close()

    with Section(config, f"  Parts ({len(part_rows)})") as s:
        for row in part_rows:
            s.add(row)
    print_summary(config, copied=written, failed=failed or None, label="parts saved")
    if finalize_output(config, out, cancel):
        print(f"  Done! {written} part(s) saved.")
    print(f"  → {shown(out)}")
    print("")
