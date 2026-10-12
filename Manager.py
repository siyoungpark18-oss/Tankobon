#IMPORTS——————————————————————————————————————————————————————————————————————————————————————————————————
from PIL import Image
from pathlib import Path
import re
import shutil
import img2pdf
import os
import hashlib
import time
import psutil
import errno
import zipfile
import tempfile
import io
import uuid
import posixpath
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import unquote
from xml.sax.saxutils import escape as _xml_escape
from pypdf import PdfReader, PdfWriter
import sys as _sys
from Log import (natural_sort_key, _fmt_size, _total_size, _size_suffix, _rel, _Section, _log_group, _log_scan, _get_log_section_fns, _int_cfg)
from ToolNames import document_name, folder_tool_name
from ManagerConfig import get_input, resolve_sort
from ManagerOutput import (
    get_output, finalize_output as _finalize_output, shown as _shown, settle_runs,
    recover_stale_runs, unique_path, unique_output_stem, configure_output_support, validate_run_name,
)

def _get_poppler_path():
    base = getattr(_sys, '_MEIPASS', None)
    if base:
        return str(Path(base) / 'poppler' / 'bin')
    return None

_POPPLER_PATH = _get_poppler_path()

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif'} #DEFAULT IMAGE EXTENSIONS FOR MOST TOOLS

SENTINEL = "\x00CANCELLED\x00"

_last_throttle_check = 0.0
psutil.cpu_percent(interval=None)     # first call only primes the counter


def throttle_if_needed(config, cancel=None):
    """Pause while the computer is busy. Cheap enough to call once per file:
    it only really measures twice a second, and it does not block while measuring.
    If `cancel` is set the pause ends immediately so the job can stop."""
    global _last_throttle_check
    cpu_limit = _int_cfg(config, "throttle_cpu", 80)
    mem_limit = _int_cfg(config, "throttle_mem", 80)
    if cpu_limit == 0 and mem_limit == 0:
        return
    now = time.monotonic()
    if now - _last_throttle_check < 0.5:
        return
    _last_throttle_check = now
    warned = False
    while True:
        if cancel is not None and cancel.is_set():
            return
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent
        if cpu <= cpu_limit and mem <= mem_limit:
            break
        if not warned:
            reasons = []
            if cpu > cpu_limit:
                reasons.append(f"CPU at {cpu:.0f}% (limit: {cpu_limit}%)")
            if mem > mem_limit:
                reasons.append(f"RAM at {mem:.0f}% (limit: {mem_limit}%)")
            print(f"  Paused — {', '.join(reasons)}. Free up resources or raise the throttle limit in Preferences.")
            warned = True
        time.sleep(0.5)
    if warned:
        print(f"  Resuming...")



def _check_disk_space(path, config=None):
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        min_gb = _int_cfg(config, "min_free_gb", 10) if config else 10
        if free_gb < min_gb:
            print(f"  ✖ Not enough disk space: {free_gb:.1f} GB free (minimum: {min_gb} GB).")
            print(f"    Free up space or lower the minimum in Preferences (≡).")
            print("")
            return False
        elif free_gb < 10:
            print(f"  ⚠ Low disk space: {free_gb:.1f} GB free on output drive.")
        else:
            print(f"  Disk space: {free_gb:.1f} GB free.")
        return True
    except Exception as e:
        print(f"  Could not check disk space: {e}")
        return True


def _is_no_space(e):
    if isinstance(e, OSError):
        if e.errno == errno.ENOSPC:
            return True
        if hasattr(errno, 'EDQUOT') and e.errno == errno.EDQUOT:
            return True
    return False


def collect_image_paths(folder, image_extensions, use_sort=True):
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
    """Collect supported files directly inside a folder without descending into child folders."""
    items = sorted(folder.iterdir(), key=lambda x: natural_sort_key(x.name)) if use_sort else list(folder.iterdir())
    paths, skipped = [], []
    for item in items:
        if item.is_file():
            if item.suffix.lower() in image_extensions:
                paths.append(item)
            else:
                skipped.append((item, "unsupported type"))
    return paths, skipped


def save_pdf(image_paths, output_path):
    if not image_paths:
        print("  No images to save.")
        return
    print(f"  Converting {len(image_paths)} images → PDF...")
    output_path = Path(output_path)
    partial = output_path.with_name(output_path.name + ".partial")
    try:
        try:
            with open(partial, 'wb') as f:
                f.write(img2pdf.convert([str(p) for p in image_paths]))
        except Exception as e:
            if _is_no_space(e):
                print("  ✖ Disk full — not enough space to write PDF.")
                raise
            partial.unlink(missing_ok=True)
            print(f"  img2pdf failed ({e}), falling back to Pillow...")
            imgs = []
            for p in image_paths:
                with Image.open(p) as source:
                    imgs.append(source.convert("RGB"))
            if imgs:
                imgs[0].save(partial, format="PDF", save_all=True, append_images=imgs[1:])
        os.replace(partial, output_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"  Saved: {output_path.name}  ({size_mb:.1f} MB)")


def _write_pdf_atomically(writer, output_path):
    """Write a PDF beside its destination, then publish it only after completion."""
    output_path = Path(output_path)
    partial = output_path.with_name(output_path.name + ".partial")
    try:
        with open(partial, "wb") as f:
            writer.write(f)
        os.replace(partial, output_path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


# ══ EPUB SUPPORT ══════════════════════════════════════════════════════════════
# OUTPUT: Folder and image production tools write a PDF or a multi-page fixed-layout EPUB book, selected in the
#         main interface or Preferences > Tools. In Individual mode, each folder produces one book; images
#         become pages within that book, never separate one-image EPUB files.
# INPUT : PDF Combiner, PDF Splitter and PDF to Images also accept EPUBs. Only IMAGE-BASED EPUBs work (every
#         page is a picture). A normal text EPUB has no page images to take out, and is reported as such.

DOCUMENT_EXTENSIONS = {'.pdf', '.epub'}
EPUB_IMAGE_TYPES    = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
                       '.gif': 'image/gif', '.webp': 'image/webp'}          # image types an EPUB reader must support
_READABLE_IMAGES    = set(EPUB_IMAGE_TYPES) | {'.bmp', '.tif', '.tiff'}      # image types we can read out of an EPUB
_IMG_REF = re.compile(r'<(?:img|image)\b[^>]*?\b(?:src|(?:xlink:)?href)\s*=\s*["\']([^"\']+)["\']', re.I | re.S)


class _PathImage:
    """A page image on disk, for build_epub()."""
    def __init__(self, path):
        self.path = Path(path)
        self.suffix = self.path.suffix.lower()

    def read(self):
        return self.path.read_bytes()


class _ZipImage:
    """A page image inside an open zip (an EPUB), for build_epub()."""
    def __init__(self, zf, member):
        self.zf, self.member = zf, member
        self.suffix = Path(member).suffix.lower()

    def read(self):
        return self.zf.read(self.member)


def _render_pdf_pages_as_images(pdf_path, page_numbers, temp_dir, prefix, cancel=None, dpi=150):
    """Render selected PDF pages as PNG page images for a multi-page EPUB book."""
    try:
        from pdf2image import convert_from_path
    except ImportError as exc:
        raise RuntimeError("pdf2image is required to write an EPUB from PDF pages") from exc
    rendered_pages = []
    for serial, page_number in enumerate(page_numbers, 1):
        if cancel is not None and cancel.is_set():
            break
        images = convert_from_path(str(pdf_path), dpi=dpi, first_page=page_number,
                                   last_page=page_number, poppler_path=_POPPLER_PATH)
        if not images:
            raise RuntimeError(f"could not render PDF page {page_number}")
        path = Path(temp_dir) / f"{prefix}_{serial:06d}.png"
        try:
            images[0].save(path, format="PNG")
        finally:
            for image in images:
                image.close()
        rendered_pages.append(_PathImage(path))
    return rendered_pages


class _BuildCancelled(Exception):
    pass


def _doc_format(config):
    fmt = config.get("default_doc_format", "epub")
    return fmt if fmt in ("pdf", "epub") else "epub"


def _doc_name(config):
    return document_name(config)


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def epub_pages(zf):
    """Page images of an image-based EPUB, in reading order. zf = an open ZipFile.
    Returns (members, direction, title): the image paths inside the zip, "rtl" or "ltr", and the book title (or None).
    members is empty for a normal text EPUB. Raises ValueError if this isn't an EPUB at all."""
    try:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
    except KeyError:
        raise ValueError("not a valid EPUB (no META-INF/container.xml)")
    opf_path = next((el.get("full-path") for el in container.iter()
                     if _local(el.tag) == "rootfile" and el.get("full-path")), None)
    if not opf_path:
        raise ValueError("not a valid EPUB (no package file)")
    opf = ET.fromstring(zf.read(opf_path))

    manifest, spine, title, direction = {}, [], None, "ltr"
    for el in opf.iter():
        name = _local(el.tag)
        if name == "item" and el.get("id"):
            manifest[el.get("id")] = el.get("href") or ""
        elif name == "itemref" and el.get("idref"):
            spine.append(el.get("idref"))
        elif name == "spine" and el.get("page-progression-direction") == "rtl":
            direction = "rtl"
        elif name == "title" and title is None and el.text and el.text.strip():
            title = el.text.strip()

    names = set(zf.namelist())
    opf_dir = posixpath.dirname(opf_path)
    members = []
    for idref in spine:
        href = manifest.get(idref)
        if not href:
            continue
        page = posixpath.normpath(posixpath.join(opf_dir, unquote(href.split("#")[0])))
        if page not in names:
            continue
        if Path(page).suffix.lower() in _READABLE_IMAGES:           # an image listed directly in the spine
            members.append(page)
            continue
        text = zf.read(page).decode("utf-8", "replace")
        for ref in _IMG_REF.findall(text):
            ref = unquote(ref.split("#")[0].split("?")[0])
            if ref.startswith(("http:", "https:", "data:")):
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(page), ref))
            if target in names and Path(target).suffix.lower() in _READABLE_IMAGES:
                members.append(target)
    return members, direction, title


def build_epub(config, pages, out_path, title, cancel=None, direction=None):
    """Writes a fixed-layout (pre-paginated) EPUB 3 with one image per page.
    pages = objects with .suffix and .read() (see _PathImage / _ZipImage). Returns False if cancelled.
    The file is built under a temporary name and only given its real name once complete."""
    direction = direction if direction in ("rtl", "ltr") else config.get("epub_direction", "rtl")
    if direction not in ("rtl", "ltr"):
        direction = "rtl"
    out_path = Path(out_path)
    partial = out_path.with_name(out_path.name + ".partial")
    xml = lambda s: _xml_escape(str(s), {'"': "&quot;"})
    manifest, spine, count = [], [], 0
    try:
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, allowZip64=True, strict_timestamps=False) as zf:
            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)   # must be first, uncompressed
            zf.writestr("META-INF/container.xml",
                        '<?xml version="1.0"?>\n'
                        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                        '</rootfiles></container>')
            for i, src in enumerate(pages, 1):
                if cancel is not None and cancel.is_set():
                    raise _BuildCancelled()
                throttle_if_needed(config, cancel)
                data, suffix = src.read(), src.suffix.lower()
                with Image.open(io.BytesIO(data)) as im:
                    w, h = im.size
                    if suffix not in EPUB_IMAGE_TYPES:             # bmp / tiff: not allowed in an EPUB, so PNG
                        buf = io.BytesIO()
                        (im.convert("RGB") if im.mode == "CMYK" else im).save(buf, "PNG")
                        data, suffix = buf.getvalue(), ".png"
                if suffix == ".jpeg":
                    suffix = ".jpg"
                img_name, page_name = f"{i:04d}{suffix}", f"page_{i:04d}.xhtml"
                zf.writestr(f"OEBPS/images/{img_name}", data, compress_type=zipfile.ZIP_STORED)
                zf.writestr(f"OEBPS/{page_name}",
                            '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
                            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
                            f'<head><meta charset="utf-8"/><title>{xml(title)} - {i}</title>\n'
                            f'<meta name="viewport" content="width={w}, height={h}"/>\n'
                            '<style>html, body { margin: 0; padding: 0; } '
                            f'img {{ display: block; width: {w}px; height: {h}px; }}</style></head>\n'
                            f'<body><img src="images/{img_name}" alt="Page {i}" width="{w}" height="{h}"/></body>\n</html>')
                cover = ' properties="cover-image"' if i == 1 else ""
                manifest.append(f'<item id="img{i:04d}" href="images/{img_name}" media-type="{EPUB_IMAGE_TYPES[suffix]}"{cover}/>')
                manifest.append(f'<item id="page{i:04d}" href="{page_name}" media-type="application/xhtml+xml"/>')
                spine.append(f'<itemref idref="page{i:04d}"/>')
                count = i
            zf.writestr("OEBPS/nav.xhtml",
                        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
                        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
                        f'<head><meta charset="utf-8"/><title>{xml(title)}</title></head>\n'
                        '<body><nav epub:type="toc" id="toc"><ol>'
                        f'<li><a href="page_0001.xhtml">{xml(title)}</a></li></ol></nav></body>\n</html>')
            zf.writestr("OEBPS/content.opf",
                        '<?xml version="1.0" encoding="utf-8"?>\n'
                        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" '
                        'prefix="rendition: http://www.idpf.org/vocab/rendition/#">\n'
                        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
                        f'<dc:identifier id="bookid">urn:uuid:{uuid.uuid4()}</dc:identifier>\n'
                        f'<dc:title>{xml(title)}</dc:title>\n<dc:language>und</dc:language>\n'
                        f'<meta property="dcterms:modified">{datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}</meta>\n'
                        '<meta property="rendition:layout">pre-paginated</meta>\n'
                        '<meta property="rendition:orientation">auto</meta>\n</metadata>\n<manifest>\n'
                        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>\n'
                        + "\n".join(manifest) +
                        f'\n</manifest>\n<spine page-progression-direction="{direction}">\n'
                        + "\n".join(spine) + '\n</spine>\n</package>')
        os.replace(partial, out_path)
    except _BuildCancelled:
        partial.unlink(missing_ok=True)
        return False
    except Exception:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    print(f"  Saved: {out_path.name}  ({count} pages, {out_path.stat().st_size / (1024 * 1024):.1f} MB)")
    return True


def save_book(config, image_paths, stem_path, title=None, cancel=None):
    """Writes the images as <stem_path>.pdf or .epub, depending on Preferences > Tools > Document Format.
    Returns False only if the person cancelled (an EPUB can be cancelled, a PDF can't once it has started)."""
    if _doc_format(config) == "pdf":
        save_pdf(image_paths, stem_path.with_name(stem_path.name + ".pdf"))
        return True
    if not image_paths:
        print("  No images to save.")
        return True
    print(f"  Converting {len(image_paths)} images → EPUB...")
    return build_epub(config, [_PathImage(p) for p in image_paths],
                      stem_path.with_name(stem_path.name + ".epub"), title or stem_path.name, cancel)


def _image_to_pdf_bytes(data):
    """One page image (bytes) as a one-page PDF."""
    try:
        return img2pdf.convert(data)
    except Exception:                                        # transparency, webp, ...: let Pillow flatten it
        buf = io.BytesIO()
        with Image.open(io.BytesIO(data)) as im:
            im.convert("RGB").save(buf, "PDF")
        return buf.getvalue()


def _export_epub_pages(config, epub_path, dest_dir, fmt, cancel=None):
    """Takes the page images out of an EPUB into dest_dir as <name>_0001.jpg/png. Pages are written as they are
    when they already have the wanted format, otherwise converted. Returns (pages_written, cancelled)."""
    with zipfile.ZipFile(epub_path) as zf:
        members, _direction, _title = epub_pages(zf)
        if not members:
            raise ValueError("no page images found (only image-based EPUBs are supported)")
        for i, member in enumerate(members):
            if cancel is not None and cancel.is_set():
                return i, True
            throttle_if_needed(config, cancel)
            data = zf.read(member)
            dest = dest_dir / f"{epub_path.stem}_{str(i + 1).zfill(4)}.{fmt}"
            if Path(member).suffix.lower() in ((".jpg", ".jpeg") if fmt == "jpg" else (".png",)):
                dest.write_bytes(data)
            else:
                with Image.open(io.BytesIO(data)) as im:
                    if fmt == "jpg":
                        im = im.convert("RGB")
                    im.save(dest, "JPEG" if fmt == "jpg" else "PNG")
        return len(members), False


def _get_run_name(config, prompt="Run name (Enter to skip): "):
    """Returns the run name, "" when the person didn't name the run, or None if they cancelled."""
    if not config.get("ask_run_name", False):
        return ""
    while True:
        val = input(prompt).strip()
        if val == SENTINEL:
            return None
        if not val:
            return ""
        try:
            return validate_run_name(val)
        except ValueError:
            print("  Run names must be a single folder name. Avoid '/', '\\', and ':'; don't use '.' or '..' alone.")


def _cancel():
    print("  Cancelled.")
    print("")


def _validate_rename_preview(out, preview, src, folders=False):
    """Reject rename values that introduce path components or unsafe file-name characters."""
    root = Path(out).resolve()
    source = Path(src).resolve()
    for old, new in preview:
        try:
            relative = Path(new).resolve(strict=False).relative_to(root)
        except ValueError:
            print("  ✖ Rename stopped because a name points outside the output folder.")
            print("    Remove path separators and try again.")
            return False
        parts = relative.parts
        expected_parent = () if folders else Path(old).relative_to(source).parent.parts
        if (len(parts) != len(expected_parent) + 1 or parts[:-1] != expected_parent
                or any(part in ("", ".", "..") or any(ch in part for ch in ("/", "\\", ":"))
                       or any(ord(ch) < 32 for ch in part) for part in parts)):
            print("  ✖ Rename stopped because names must be single safe file or folder names.")
            print("    Remove '/', '\\', ':', or control characters, then try again.")
            return False
    return True


def _validate_rename_text(value):
    if any(ch in value for ch in ("/", "\\", ":")) or any(ord(ch) < 32 for ch in value):
        print("  ✖ Rename stopped because names cannot contain '/', '\\', ':', or control characters.")
        return False
    return True


def _has_name_conflicts(config, preview):
    """Return True when destinations collide with each other or already exist."""
    users = {}
    existing = []
    for old, new in preview:
        key = os.path.normcase(os.path.abspath(str(new))).casefold()
        users.setdefault(key, (new, []))[1].append(old)
        if new.exists():
            existing.append((new, old))
    clashes = [entry for entry in users.values() if len(entry[1]) > 1]
    if not clashes and not existing:
        return False
    print("  ✖ Rename stopped because one or more output names already exist or repeat.")
    print("    Choose a different naming mode or input set, then try again.")
    with _Section(config, f"  Conflicting destinations ({len(clashes) + len(existing)})") as s:
        for new, olds in clashes:
            s.add(f"    {new.name}  ←  " + ",  ".join(o.name for o in olds))
        for new, old in existing:
            s.add(f"    {new.name} already exists (from {old.name})")
    return True


def friendly_error(error):
    """Turn common file-operation errors into a short explanation and a next step."""
    detail = str(error).strip()
    text = detail.lower()
    if "no space left" in text or "disk full" in text or "not enough space" in text:
        return "The disk is full. Free space on the output drive, then try again."
    if "permission denied" in text or "access is denied" in text or "operation not permitted" in text:
        return "Permission denied. Check folder access and close any app using the file, then try again."
    if "no such file or directory" in text or "cannot find the path" in text:
        return "The file or folder could not be found. Check that it still exists and try again."
    if any(term in text for term in ("not a valid zip", "badzipfile", "crc-32", "checksum", "cannot identify image", "unidentifiedimageerror", "truncated", "unexpected eof", "damaged", "corrupt")):
        return "The file appears to be corrupt or incomplete. Try opening it; if it will not open, replace it with a fresh copy."
    if any(term in text for term in ("pdfreaderror", "pdf read", "invalid pdf", "malformed pdf", " eof marker not found")):
        return "The PDF could not be read. Open it in a PDF viewer; if it will not open, replace it with a fresh copy."
    if detail:
        return f"Could not complete this file operation. Check that the file opens and the destination is writable, then try again. Details: {detail}"
    return "Could not complete this file operation. Check that the file opens and the destination is writable, then try again."


def _print_summary(config, copied=0, failed=None, skipped=None, label="processed"):
    parts = [f"  {label}: {copied}"]
    if failed:
        parts.append(f"Failed: {len(failed)}")
    if skipped:
        parts.append(f"Skipped: {len(skipped)}")
    print("  " + "   ".join(parts))
    if failed:                                  # failures are never truncated: you need to see all of them
        with _Section(config, f"  Failed files ({len(failed)})", unlimited=True) as s:
            for p, reason in failed:
                s.add(f"    {p.name}: {friendly_error(reason)}")
    if skipped:
        with _Section(config, f"  Skipped files ({len(skipped)})") as s:
            for p, reason in skipped:
                s.add(f"    {p.name}: {reason}")



def _get_working_folders(src, use_sort=True):
    top = sorted([f for f in src.iterdir() if f.is_dir()],
                 key=lambda x: natural_sort_key(x.name)) if use_sort else [f for f in src.iterdir() if f.is_dir()]
    if len(top) == 1:
        sub = sorted([f for f in top[0].iterdir() if f.is_dir()],
                     key=lambda x: natural_sort_key(x.name)) if use_sort else [f for f in top[0].iterdir() if f.is_dir()]
        if sub:
            print(f"  Found 1 top-level folder '{top[0].name}' — operating on its {len(sub)} subfolder(s).")
            return sub
    return top


# ══ ZIP SUPPORT ═══════════════════════════════════════════════════════════════
# INPUT : every tool unpacks .zip / .cbz files found anywhere in Input (even nested ones) into a
#         normal folder before it starts, so every tool treats them exactly like folders.
#         The zip is removed once it has been unpacked (Input only ever holds copies).
# OUTPUT: with Default Folder Production Format set to zip or cbz, every top-level FOLDER of a result is packed
#         into its own "<folder>.zip" / "<folder>.cbz" (chapter by chapter). Loose images at the top level are
#         packed together into one archive. When Keep PDFs & EPUBs Direct in Output is on, those documents are
#         moved out of result folders before packing. The main output folder
#         itself is never archived. A .cbz is a plain zip, but comic readers expect only images inside, so a
#         folder that holds anything else is saved as a normal .zip instead, with a warning.

ARCHIVE_EXTENSIONS    = {'.zip', '.cbz'}                       # .cbz (comic book archive) is just a zip
CBZ_IMAGE_EXTENSIONS  = IMAGE_EXTENSIONS | {'.webp'}           # what may go inside a .cbz
_MAX_ARCHIVE_NESTING  = 3                                      # zip inside zip inside zip ...
_JUNK_FILES           = {'.ds_store', 'thumbs.db', 'desktop.ini'}
_ALREADY_COMPRESSED   = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.pdf', '.zip', '.cbz'}


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
            reserve = _int_cfg(config, "min_free_gb", 10) * (1024 ** 3)
            free = shutil.disk_usage(parent).free
            if need + reserve > free:
                raise OSError(errno.ENOSPC,
                              f"not enough disk space to unpack ({_fmt_size(need)} needed, "
                              f"{_fmt_size(free)} free, minimum free space is set in Preferences)")
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
                      key=lambda x: natural_sort_key(_rel(x, src)))

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
                failed.append((archive, "disk full" if _is_no_space(e) and not e.strerror else (e.strerror or str(e))))
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
            rows.append(f"    {_rel(archive, src)}  →  {_rel(folder, src)}/  ({count} file(s), {_fmt_size(size)})")

    for archive in find():                                      # still there after the nesting limit
        failed.append((archive, f"nested more than {_MAX_ARCHIVE_NESTING} levels deep"))

    if rows:
        with _Section(config, f"  Unpacked zip files ({len(rows)})") as s:
            for row in rows:
                s.add(row)
    if unsafe_total:
        print(f"  ⚠ {unsafe_total} unsafe path(s) inside zip files were skipped (they pointed outside the folder).")
    if rows or failed:
        _print_summary(config, copied=len(rows), failed=failed or None, label="extracted")
    return True



def folders_to_pdf(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print(f"Folders to {_doc_name(config)}")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, f"folders to {_doc_format(config)}", run_name)
    print(f"  Output: {_shown(out)}")
    if not _check_disk_space(out, config):
        return

    mode = config.get("default_folders_to_pdf_mode", "ask")
    if mode == "ask":
        raw = input(f"Mode? 1=Combine all into one {_doc_name(config)} (default)  2=One {_doc_name(config)} per folder: ").strip()
        if raw == SENTINEL:
            return _cancel()
        mode = "individual" if raw == "2" else "combine"
    elif mode not in ("combine", "individual"):
        mode = "combine"                                  # unusable saved value: fall back to the default
    print(f"  Mode: {mode}")
    if _doc_format(config) == "pdf":
        print(f"  Note: cannot be cancelled once PDF conversion starts.")

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    folders = sorted([f for f in src.iterdir() if f.is_dir()],
                     key=lambda x: natural_sort_key(x.name)) if use_sort else \
        [f for f in src.iterdir() if f.is_dir()]

    if not folders:
        print("  No folders found in Input!")
        print("")
        return

    print(f"  Found {len(folders)} folder(s). Scanning...")

    if mode == "combine":
        all_paths = []
        all_skipped = []
        for folder in folders:
            if cancel and cancel.is_set():
                print("  Cancelled.")
                print("")
                return
            throttle_if_needed(config, cancel)
            paths, skipped = collect_image_paths(folder, IMAGE_EXTENSIONS, use_sort=use_sort)
            _log_group(config, folder.name, paths, folder,
                       extra=f"  ·  {len(skipped)} skipped" if skipped else "")
            all_paths.extend(paths)
            all_skipped.extend(skipped)

        print(f"  Total: {len(all_paths)} images across {len(folders)} folders{_size_suffix(config, all_paths)}")

        if all_paths:
            try:
                if save_book(config, all_paths, out / "output", "Output", cancel) is False:
                    return _cancel()
            except OSError as e:
                if _is_no_space(e):
                    print(f"  ✖ Job stopped — disk full. {_doc_name(config)} may be incomplete.")
                    print("")
                    return
                raise
            _print_summary(config, copied=len(all_paths), skipped=all_skipped or None, label="converted")
            _finalize_output(config, out, cancel)
            print(f"  Done! → {_shown(out)}")
            print("")
        else:
            print("  No images found in any folder.")
            if all_skipped:
                _print_summary(config, skipped=all_skipped)
            print("")

    else:
        total_converted = 0
        total_skipped = []
        used_book_paths = set()
        for folder in folders:
            if cancel and cancel.is_set():
                print(f"  Cancelled. ({total_converted} {_doc_name(config)}s saved so far)")
                print("")
                return
            throttle_if_needed(config, cancel)
            subfolders = sorted(
                [f for f in folder.iterdir() if f.is_dir()],
                key=lambda x: natural_sort_key(x.name)
            ) if use_sort else [f for f in folder.iterdir() if f.is_dir()]
            units = list(subfolders) if subfolders else [folder]
            if subfolders:
                loose_images, loose_skipped = collect_direct_image_paths(
                    folder, IMAGE_EXTENSIONS, use_sort=use_sort)
                if loose_images or loose_skipped:
                    units.append(folder)
            unit_data = []
            for unit in units:
                if unit is folder and subfolders:
                    paths, skipped = collect_direct_image_paths(
                        unit, IMAGE_EXTENSIONS, use_sort=use_sort)
                else:
                    paths, skipped = collect_image_paths(unit, IMAGE_EXTENSIONS, use_sort=use_sort)
                total_skipped.extend(skipped)
                raw_name = f"{folder.name} (loose images)" if unit is folder and subfolders else unit.name
                safe_name = re.sub(r'[^\w\s\-.]', '', raw_name).strip() or raw_name
                if paths:
                    safe_name = unique_output_stem(
                        out, safe_name, f".{_doc_format(config)}", used_book_paths).name
                label = "Loose images" if unit is folder and subfolders else (
                    unit.name if unit is folder else f"{folder.name} / {unit.name}")
                extra = (f"  ·  {len(skipped)} skipped" if skipped else "") + \
                        (f"  →  {safe_name}.{_doc_format(config)}" if paths else "")
                _log_group(config, label, paths, unit, extra=extra)
                unit_data.append((safe_name, paths))
            for safe_name, paths in unit_data:
                if not paths:
                    continue
                try:
                    if save_book(config, paths, out / safe_name, safe_name, cancel) is False:
                        print(f"  Cancelled. ({total_converted} {_doc_name(config)}s saved so far)")
                        print("")
                        return
                    total_converted += 1
                except OSError as e:
                    if _is_no_space(e):
                        print(f"  ✖ Disk full after {total_converted} {_doc_name(config)}(s). Stopping.")
                        print("")
                        return
                    print(f"  Failed to save {safe_name}.{_doc_format(config)}: {e}")
        _print_summary(config, copied=total_converted, skipped=total_skipped or None, label=f"{_doc_name(config)}s saved")
        _finalize_output(config, out, cancel)
        print(f"  Done! → {_shown(out)}")
        print("")


def images_to_pdf(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print(f"Images to {_doc_name(config)}")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, f"images to {_doc_format(config)}", run_name)
    print(f"  Output: {_shown(out)}")
    if not _check_disk_space(out, config):
        return
    if _doc_format(config) == "pdf":
        print(f"  Note: cannot be cancelled once PDF conversion starts.")

    all_files = list(src.rglob("*"))
    image_files = []
    skipped = []
    for f in all_files:
        if not f.is_file():
            continue
        if f.suffix.lower() in IMAGE_EXTENSIONS:
            image_files.append(f)
        else:
            skipped.append((f, "unsupported type"))

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    if use_sort:
        image_files = sorted(image_files,
                             key=lambda x: natural_sort_key(str(x.relative_to(src))))

    if not image_files:
        print("  No images found in Input!")
        if skipped:
            _print_summary(config, skipped=skipped)
        print("")
        return

    from collections import Counter
    ext_counts = Counter(f.suffix.lower() for f in image_files)
    summary = "  ".join(f"{v}× {k}" for k, v in sorted(ext_counts.items()))
    print(f"  Found {len(image_files)} images:  {summary}{_size_suffix(config, image_files)}")

    mode = config.get("default_images_to_pdf_mode", "ask")
    if mode == "ask":
        raw = input("Mode? 1=Combine all (default)  2=Reorder & Combine: ").strip()
        if raw == SENTINEL:
            return _cancel()
        mode = "reorder & combine" if raw == "2" else "combine"
    elif mode not in ("combine", "reorder & combine"):
        mode = "combine"                                  # unusable saved value: fall back to the default
    print(f"  Mode: {mode}")

    if mode == "reorder & combine":
        # One expander PER FOLDER. Lists follow the preview size; each header shows its number range
        # (e.g. #26–#29) so you can still tell which number is which file when names are hidden.
        numbering = {f: n for n, f in enumerate(image_files, 1)}
        print("  Expand a folder to see which number is which file. Numbers run in order; each folder's range is in its header.")
        _log_scan(config, image_files, src, numbering=numbering, unlimited=True)
        print(f"  Enter file numbers (1–{len(image_files)}) separated by commas.")
        print(f"  Leave blank to use all files in default order.")
        raw = input("Order: ").strip()
        if raw == SENTINEL:
            return _cancel()

        if not raw:
            print("  No order given — using all files in default order.")
            final_files = image_files
            out_of_range = []
        else:
            indices = [s.strip() for s in raw.split(",")]
            final_files = []
            out_of_range = []
            for s in indices:
                try:
                    idx = int(s)
                    if 1 <= idx <= len(image_files):
                        final_files.append(image_files[idx - 1])
                    else:
                        out_of_range.append(s)
                except ValueError:
                    out_of_range.append(s)

            if not final_files:
                print("  No valid indices entered. Aborting.")
                print("")
                return

            print(f"  Using {len(final_files)} file(s) in specified order.")
    else:
        _log_scan(config, image_files, src)
        final_files = image_files
        out_of_range = []

    if cancel and cancel.is_set():
        print("  Cancelled.")
        print("")
        return

    try:
        if save_book(config, final_files, out / "output", "Output", cancel) is False:
            return _cancel()
    except OSError as e:
        if _is_no_space(e):
            print(f"  ✖ Job stopped — disk full. {_doc_name(config)} may be incomplete.")
            print("")
            return
        raise

    if out_of_range:
        print(f"  ⚠ {len(out_of_range)} invalid or out-of-range index/indices skipped: {', '.join(out_of_range)}")

    _print_summary(config, copied=len(final_files), skipped=skipped or None, label="converted")
    _finalize_output(config, out, cancel)
    print(f"  Done! → {_shown(out)}")
    print("")


def _folder_tool_name(config, name):
    return folder_tool_name(config, name)


def folder_renamer(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print(_folder_tool_name(config, "Folder Renamer"))
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, _folder_tool_name(config, "Folder Renamer").lower(), run_name)
    print(f"  Output: {_shown(out)}")
    if not _check_disk_space(out, config):
        return

    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    folders = _get_working_folders(src, use_sort)

    if not folders:
        print("  No folders found in Input!")
        print("")
        return

    print(f"  Found {len(folders)} folder(s).")
    for folder in folders:
        folder_files = [f for f in folder.rglob("*") if f.is_file()]
        if use_sort:
            folder_files.sort(key=lambda x: natural_sort_key(_rel(x, folder)))
        _log_group(config, folder.name, folder_files, folder, noun="file")
    mode = config.get("default_folder_renamer_mode", "ask")
    if mode == "ask":
        print("  Modes: 1=Prefix  2=Suffix  3=Replace  4=Extract Number")
        mode = input("Choose mode (1-4, default 4): ").strip() or "4"
        if mode == SENTINEL:
            return _cancel()
        mode = {"1": "prefix", "2": "suffix", "3": "replace", "4": "extract number"}.get(mode, mode)
    if mode not in ("prefix", "suffix", "replace", "extract number"):
        print(f"  ⚠ '{mode}' is not a valid mode. Using the default (extract number).")
        mode = "extract number"
    print(f"  Mode: {mode}")

    preview = []
    skipped = []

    if mode == "prefix":
        param1 = input("Prefix to add: ")
        if param1 == SENTINEL:
            return _cancel()
        if not _validate_rename_text(param1):
            return
        for f in folders:
            preview.append((f, out / (param1 + f.name)))
    elif mode == "suffix":
        param1 = input("Suffix to add: ")
        if param1 == SENTINEL:
            return _cancel()
        if not _validate_rename_text(param1):
            return
        for f in folders:
            preview.append((f, out / (f.name + param1)))
    elif mode == "replace":
        param1 = input("Find: ")
        if param1 == SENTINEL:
            return _cancel()
        if not param1:
            print("  'Find' can't be empty.")
            print("")
            return
        param2 = input("Replace with (Enter for blank): ")
        if param2 == SENTINEL:
            return _cancel()
        if not _validate_rename_text(param1) or not _validate_rename_text(param2):
            return
        for f in folders:
            preview.append((f, out / f.name.replace(param1, param2)))
    elif mode == "extract number":
        for folder in folders:
            match = re.search(r'\d+(?:\.\d+)?', folder.name)
            if match:
                raw = match.group()
                if '.' in raw:
                    integer, decimal = raw.split('.', 1)
                    new_name = f"{int(integer)}.{decimal}"
                else:
                    new_name = str(int(raw))
                preview.append((folder, out / new_name))
            else:
                skipped.append((folder, "no number found"))
    else:
        print("  Invalid mode.")
        print("")
        return

    if not preview and not skipped:
        print("  Nothing to rename.")
        print("")
        return

    print(f"  {len(preview)} folder(s) to rename{f', {len(skipped)} skipped' if skipped else ''}.")
    if not _validate_rename_preview(out, preview, src, folders=True):
        print("")
        return
    if _has_name_conflicts(config, preview):
        print("")
        return
    with _Section(config, f"  Preview ({len(preview)})") as s:
        for old, new in preview:
            s.add(f"    {old.name}  →  {new.name}")

    failed = []
    copied = 0
    for i, (old, new) in enumerate(preview):
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({copied} renamed so far)")
            print("")
            return
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(str(old), str(new), dirs_exist_ok=True)
            copied += 1
        except OSError as e:
            if _is_no_space(e):
                print(f"  ✖ Disk full after {copied} folder(s). Stopping.")
                _print_summary(config, copied=copied, failed=failed or None,
                               skipped=skipped or None, label="renamed")
                print("")
                return
            failed.append((old, str(e)))
        except Exception as e:
            failed.append((old, str(e)))

    _print_summary(config, copied=copied, failed=failed or None,
                   skipped=skipped or None, label="renamed")
    _finalize_output(config, out, cancel)
    print(f"  Done! → {_shown(out)}")
    print("")


def file_renamer(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print("File Renamer")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "renamed", run_name)
    print(f"  Output: {_shown(out)}")
    if not _check_disk_space(out, config):
        return

    items = [f for f in src.rglob("*") if f.is_file()]
    use_sort = resolve_sort(config)
    print(f"  Sort: {'natural' if use_sort else 'none'}")
    if use_sort:
        items = sorted(items, key=lambda x: natural_sort_key(str(x.relative_to(src))))

    if not items:
        print("  No files found in Input!")
        print("")
        return

    print(f"  Found {len(items)} file(s){_size_suffix(config, items)}")
    _log_scan(config, items, src, noun="file")
    mode = config.get("default_file_renamer_mode", "ask")
    if mode == "ask":
        print("  Modes: 1=Prefix  2=Suffix  3=Replace  4=Sequence")
        mode = input("Choose mode (1-4): ").strip()
        if mode == SENTINEL:
            return _cancel()
        mode = {"1": "prefix", "2": "suffix", "3": "replace", "4": "sequence"}.get(mode, mode)
    print(f"  Mode: {mode}")

    if mode == "prefix":
        param1 = input("Prefix to add: ")
        if param1 == SENTINEL:
            return _cancel()
        if not _validate_rename_text(param1):
            return
        preview = [(f, out / f.relative_to(src).parent / (param1 + f.name)) for f in items]
    elif mode == "suffix":
        param1 = input("Suffix to add (before extension): ")
        if param1 == SENTINEL:
            return _cancel()
        if not _validate_rename_text(param1):
            return
        preview = [(f, out / f.relative_to(src).parent / (f.stem + param1 + f.suffix)) for f in items]
    elif mode == "replace":
        param1 = input("Find: ")
        if param1 == SENTINEL:
            return _cancel()
        if not param1:
            print("  'Find' can't be empty.")
            print("")
            return
        param2 = input("Replace with (Enter for blank): ")
        if param2 == SENTINEL:
            return _cancel()
        if not _validate_rename_text(param1) or not _validate_rename_text(param2):
            return
        preview = [(f, out / f.relative_to(src).parent / f.name.replace(param1, param2)) for f in items]
    elif mode == "sequence":
        param1 = input("Base name (leave blank for numbers only): ")
        if param1 == SENTINEL:
            return _cancel()
        if not _validate_rename_text(param1):
            return
        start = input("Start number (default 1): ").strip()
        if start == SENTINEL:
            return _cancel()
        pad = input("Pad digits (default 3): ").strip()
        if pad == SENTINEL:
            return _cancel()
        def _whole_number(text, default, what):
            if not text:
                return default
            try:
                n = int(text)
                if n < 0:
                    raise ValueError
                return n
            except ValueError:
                print(f"  ⚠ '{text}' is not a valid {what}. Using the default ({default}).")
                return default
        start = _whole_number(start, 1, "start number")
        pad   = _whole_number(pad,   3, "number of digits")
        def _seq_name(base, i, ext):
            num = str(start + i).zfill(pad)
            return f"{base}{num}{ext}" if base else f"{num}{ext}"
        preview = [(f, out / f.relative_to(src).parent / _seq_name(param1, i, f.suffix))
                   for i, f in enumerate(items)]
    else:
        print("  Invalid mode.")
        print("")
        return

    if not _validate_rename_preview(out, preview, src):
        print("")
        return
    if _has_name_conflicts(config, preview):
        print("")
        return
    with _Section(config, f"  Preview ({len(preview)})") as s:
        for old, new in preview:
            s.add(f"    {old.name}  →  {new.name}")

    copied = 0
    failed = []
    for i, (old, new) in enumerate(preview):
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({copied} renamed so far)")
            print("")
            return
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(old), str(new))
            copied += 1
        except OSError as e:
            if _is_no_space(e):
                print(f"  ✖ Disk full after {copied} file(s). Stopping.")
                _print_summary(config, copied=copied, failed=failed or None, label="renamed")
                print("")
                return
            failed.append((old, str(e)))
        except Exception as e:
            failed.append((old, str(e)))

    _print_summary(config, copied=copied, failed=failed or None, label="renamed")
    _finalize_output(config, out, cancel)
    print(f"  Done! → {_shown(out)}")
    print("")



def combine_image_sets(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print(_folder_tool_name(config, "Combine Image Sets"))
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "combined image set", run_name)
    print(f"  Output: {_shown(out)}")
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
            print(f"  Cancelled. ({counter - 1} images combined so far)")
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
            names = [f"{_rel(img, target)}  →  {str(first + i).zfill(4)}{img.suffix}" for i, img in enumerate(images)]
            extra = (f"  ·  {len(skipped)} skipped" if skipped else "") + \
                    (f"  →  {str(first).zfill(4)}–{str(first + len(images) - 1).zfill(4)}" if images else "")
            _log_group(config, label if target is folder else f"{folder.name} / {label}",
                       images, target, names=names, extra=extra)

            for img in images:
                dest = out / f"{str(counter).zfill(4)}{img.suffix}"
                try:
                    shutil.copy2(img, dest)
                    counter += 1
                except OSError as e:
                    if _is_no_space(e):
                        print(f"  ✖ Disk full after {counter - 1} image(s). Stopping.")
                        _print_summary(config, copied=counter - 1, failed=total_failed or None,
                                       skipped=total_skipped or None, label="combined")
                        print("")
                        return
                    total_failed.append((img, str(e)))
                except Exception as e:
                    total_failed.append((img, str(e)))

    _print_summary(config, copied=counter - 1, failed=total_failed or None,
                   skipped=total_skipped or None, label="combined")
    _finalize_output(config, out, cancel)
    print(f"  Total: {counter - 1} images combined.")
    print(f"  Done! → {_shown(out)}")
    print("")


def image_converter(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print(_folder_tool_name(config, "Image Converter"))
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()

    fmt = config.get("default_img_fmt", "ask")
    if fmt == "ask":
        raw = input("Format (jpg/png/webp/bmp/tiff, default jpg): ").strip()
        if raw == SENTINEL:                      # must be checked BEFORE .lower(), which changes the text
            return _cancel()
        fmt = raw.lower() or "jpg"
    fmt = {"jpeg": "jpg", "tif": "tiff"}.get(fmt, fmt)
    if fmt not in ("jpg", "png", "webp", "bmp", "tiff"):
        print(f"  ⚠ Unknown format '{fmt}'. Using the default (jpg).")
        fmt = "jpg"
    print(f"  Format: {fmt}")

    pillow_fmt = "JPEG" if fmt == "jpg" else fmt.upper()
    ext        = ".jpg" if fmt == "jpg" else f".{fmt}"

    out = get_output(config, "converted", run_name)
    print(f"  Output: {_shown(out)}")
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
            _print_summary(config, skipped=skipped)
        print("")
        return

    from collections import Counter
    ext_counts = Counter(f.suffix.lower() for f in images)
    summary = "  ".join(f"{v}× {k}" for k, v in sorted(ext_counts.items()))
    print(f"  Found {len(images)} images:  {summary}{_size_suffix(config, images)}")
    _log_scan(config, images, src)
    print(f"  Converting all → {ext} ...")

    converted = copied = 0
    failed = []
    for i, img_path in enumerate(images):
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({converted + copied} images processed so far)")
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
                if _is_no_space(e):
                    print(f"  ✖ Disk full after {converted + copied} image(s). Stopping.")
                    _print_summary(config, copied=converted + copied, failed=failed or None,
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
                if _is_no_space(e):
                    print(f"  ✖ Disk full after {converted + copied} image(s). Stopping.")
                    _print_summary(config, copied=converted + copied, failed=failed or None,
                                   skipped=skipped or None, label="converted")
                    print("")
                    return
                failed.append((img_path, str(e)))
            except Exception as e:
                failed.append((img_path, str(e)))

    _print_summary(config, copied=converted + copied, failed=failed or None,
                   skipped=skipped or None, label="converted")
    _finalize_output(config, out, cancel)
    print(f"  Done! → {_shown(out)}")
    print("")


def find_duplicates(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print(_folder_tool_name(config, "Find Duplicates"))
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, "find duplicates", run_name)
    print(f"  Output: {_shown(out)}")
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
            _print_summary(config, skipped=skipped)
        print("")
        return
    print(f"  Found {len(all_files)} image(s){_size_suffix(config, all_files)}")
    _log_scan(config, all_files, src)

    hash_failed = []
    duplicates  = set()
    dup_groups  = []

    if hash_method == "exact":
        print(f"  Scanning {len(all_files)} image(s)...")
        seen = {}
        file_digests = {}
        for f in all_files:
            if cancel and cancel.is_set():
                print("  Cancelled.")
                print("")
                return
            try:
                digest = hashlib.sha256(f.read_bytes()).hexdigest()
                file_digests[f] = digest
                if digest in seen:
                    duplicates.add(f)
                else:
                    seen[digest] = f
            except Exception as e:
                hash_failed.append((f, str(e)))

        # Build dup_groups for consistent reporting
        groups_by_digest = {}
        for f, d in file_digests.items():
            groups_by_digest.setdefault(d, []).append(f)
        dup_groups = [g for g in groups_by_digest.values() if len(g) > 1]

    else:
        import imagehash
        PHASH_THRESHOLD = _int_cfg(config, "phash_threshold", 4)
        print(f"  Scanning {len(all_files)} image(s) (threshold={PHASH_THRESHOLD})...")

        file_phashes = {}
        for f in all_files:
            if cancel and cancel.is_set():
                print("  Cancelled.")
                print("")
                return
            try:
                with Image.open(f) as img:
                    file_phashes[f] = imagehash.phash(img)
            except Exception as e:
                hash_failed.append((f, str(e)))

        representatives = []
        groups = []
        for f in [f for f in all_files if f in file_phashes]:
            ph = file_phashes[f]
            matched = False
            for i, (_, rep_ph) in enumerate(representatives):
                if (ph - rep_ph) <= PHASH_THRESHOLD:
                    groups[i].append(f)
                    matched = True
                    break
            if not matched:
                representatives.append((f, ph))
                groups.append([f])

        dup_groups = [g for g in groups if len(g) > 1]
        duplicates = {f for g in dup_groups for f in g[1:]}

    if not duplicates:
        print(f"  No duplicates found across {len(all_files)} images.")
        _print_summary(config, copied=len(all_files), failed=hash_failed or None,
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

    if mode == "delete all":
        exclude = {f for g in dup_groups for f in g}
    elif mode == "keep one copy":
        exclude = duplicates  # exact: all but first seen; perceptual: all but group[0]
    else:
        print("  Invalid mode.")
        print("")
        return

    with _Section(config, f"  Excluding ({len(exclude)}) — expand and hover over filenames to preview") as s:
        for f in sorted(exclude, key=lambda x: natural_sort_key(x.name)):
            s.add(f"    {_rel(f, src)}", preview_path=f)
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
            if _is_no_space(e):
                print(f"  ✖ Disk full after {copied} image(s). Stopping.")
                _print_summary(config, copied=copied, failed=failed or None,
                               skipped=skipped or None, label="copied")
                print("")
                return
            failed.append((f, str(e)))
        except Exception as e:
            failed.append((f, str(e)))

    _print_summary(config, copied=copied, failed=failed or None,
                   skipped=skipped or None, label="copied")
    _finalize_output(config, out, cancel)
    print(f"  Done! → {_shown(out)}")
    print("")


def pdf_combiner(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    output_format = _doc_format(config)
    output_name = output_format.upper()
    print(f"{output_name} Combiner")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, f"{output_format} combined", run_name)
    print(f"  Output: {_shown(out)}")
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
            _print_summary(config, skipped=skipped)
        print("")
        return

    n_epub = sum(1 for f in pdfs if f.suffix.lower() == ".epub")
    n_pdf = len(pdfs) - n_epub
    noun = "document" if n_epub else "PDF"
    mix = f"  ({n_pdf} PDF, {n_epub} EPUB)" if n_epub and n_pdf else ""
    print(f"  Found {len(pdfs)} {noun}(s){mix}{_size_suffix(config, pdfs)}")
    _log_scan(config, pdfs, src, noun=noun)
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
        from contextlib import ExitStack
        pages, direction = [], None
        with tempfile.TemporaryDirectory(prefix="tankobon_epub_pages_") as page_dir, ExitStack() as stack:
            for doc_index, doc in enumerate(pdfs):
                if cancel and cancel.is_set():
                    print(f"  Cancelled. ({total_pages} pages combined so far)")
                    print("")
                    return
                throttle_if_needed(config, cancel)
                try:
                    if doc.suffix.lower() == ".epub":
                        zf = stack.enter_context(zipfile.ZipFile(doc))
                        members, doc_direction, _title = epub_pages(zf)
                        if not members:
                            raise ValueError("no page images found (only image-based EPUBs are supported)")
                        direction = direction or doc_direction
                        pages.extend(_ZipImage(zf, m) for m in members)
                        n_pages = len(members)
                    else:
                        reader = PdfReader(str(doc))
                        n_pages = len(reader.pages)
                        pages.extend(_render_pdf_pages_as_images(
                            doc, range(1, n_pages + 1), page_dir, f"doc{doc_index:04d}", cancel))
                    total_pages += n_pages
                    page_rows.append(f"    {_rel(doc, src)}  —  {n_pages} page(s)  (running total: {total_pages})")
                except Exception as e:
                    failed.append((doc, str(e)))
            with _Section(config, f"  Pages per document ({len(page_rows)})") as section:
                for row in page_rows:
                    section.add(row)
            if not pages:
                print("  Nothing to combine.")
                _print_summary(config, copied=0, failed=failed or None, skipped=skipped or None, label="combined")
                print("")
                return
            try:
                if not build_epub(config, pages, out / "combined.epub", "Combined", cancel, direction):
                    return _cancel()
            except OSError as e:
                if _is_no_space(e):
                    print("  ✖ Disk full — could not write combined EPUB.")
                    print("")
                    return
                raise
    else:
        writer = PdfWriter()
        for i, pdf_path in enumerate(pdfs):
            if cancel and cancel.is_set():
                print(f"  Cancelled. ({total_pages} pages combined so far)")
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
                            for page in PdfReader(io.BytesIO(_image_to_pdf_bytes(zf.read(m)))).pages:
                                writer.add_page(page)
                    n_pages = len(members)
                else:
                    reader = PdfReader(str(pdf_path))
                    for page in reader.pages:
                        writer.add_page(page)
                    n_pages = len(reader.pages)
                total_pages += n_pages
                page_rows.append(f"    {_rel(pdf_path, src)}  —  {n_pages} page(s)  (running total: {total_pages})")
            except Exception as e:
                failed.append((pdf_path, str(e)))
        with _Section(config, f"  Pages per {noun} ({len(page_rows)})") as section:
            for row in page_rows:
                section.add(row)
        try:
            out_path = out / "combined.pdf"
            _write_pdf_atomically(writer, out_path)
            size_mb = out_path.stat().st_size / (1024 * 1024)
            print(f"  Saved: combined.pdf  ({total_pages} pages, {size_mb:.1f} MB)")
        except OSError as e:
            if _is_no_space(e):
                print("  ✖ Disk full — could not write combined PDF.")
                print("")
                return
            raise


    _print_summary(config, copied=len(pdfs) - len(failed), failed=failed or None,
                   skipped=skipped or None, label="combined")
    _finalize_output(config, out, cancel)
    print(f"  Done! → {_shown(out)}")
    print("")


def pdf_to_images(config, cancel=None):
    try:
        from pdf2image import convert_from_path
    except ImportError:
        convert_from_path = None          # only a problem if there is a PDF to convert (EPUBs don't need it)

    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    print(f"{_doc_name(config)} to Images")
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

    out = get_output(config, f"{_doc_format(config)} to images", run_name)
    print(f"  Output: {_shown(out)}")
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
            _print_summary(config, skipped=skipped)
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
    print(f"  Found {len(pdfs)} {noun}(s), {total_page_count} pages total{_size_suffix(config, pdfs)}.")
    if has_epub:
        print("  EPUB pages are taken out as they are; DPI only applies to PDFs.")
    _log_scan(config, pdfs, src, noun=noun,
              details={p: (f"{n} page(s)" if (n := page_counts.get(p)) is not None else "unreadable") for p in pdfs})
    print(f"  Note: cancelling will stop after the current page finishes.")
    total_pages = 0
    failed = []
    exported = []
    for pdf_path in pdfs:
        if cancel and cancel.is_set():
            print(f"  Cancelled. ({total_pages} pages exported so far)")
            print("")
            return
        pdf_out = unique_path(out / pdf_path.stem, used_pdf_dirs, directory=True)
        pdf_out.mkdir(exist_ok=True)
        if pdf_path.suffix.lower() == ".epub":
            print(f"  Extracting: {pdf_path.name}...")
            try:
                n_done, was_cancelled = _export_epub_pages(config, pdf_path, pdf_out, fmt, cancel)
            except OSError as e:
                if _is_no_space(e):
                    print(f"  ✖ Disk full after {total_pages} page(s). Stopping.")
                    _print_summary(config, copied=total_pages, failed=failed or None,
                                   skipped=skipped or None, label="pages exported")
                    print("")
                    return
                failed.append((pdf_path, str(e)))
                continue
            except Exception as e:
                failed.append((pdf_path, str(e)))
                continue
            if was_cancelled:
                print(f"  Cancelled. ({total_pages + n_done} pages exported so far)")
                print("")
                return
            total_pages += n_done
            exported.append(f"    {_rel(pdf_path, src)}  →  {pdf_out.name}/  ({n_done} page(s))")
            continue
        print(f"  Converting: {pdf_path.name}  (@ {dpi} DPI)...")
        try:
            page_count = len(PdfReader(str(pdf_path)).pages)
        except Exception as e:
            failed.append((pdf_path, str(e)))
            continue
        for i in range(page_count):
            if cancel and cancel.is_set():
                print(f"  Cancelled. ({total_pages + i} pages exported so far)")
                print("")
                return
            throttle_if_needed(config, cancel)
            try:
                pages = convert_from_path(str(pdf_path), dpi=dpi, first_page=i + 1, last_page=i + 1, poppler_path=_POPPLER_PATH)
                dest = pdf_out / f"{pdf_path.stem}_{str(i + 1).zfill(4)}.{fmt}"
                pages[0].save(str(dest), "JPEG" if fmt == "jpg" else "PNG")
            except OSError as e:
                if _is_no_space(e):
                    print(f"  ✖ Disk full after {total_pages + i} page(s). Stopping.")
                    _print_summary(config, copied=total_pages + i, failed=failed or None,
                                   skipped=skipped or None, label="pages exported")
                    print("")
                    return
                failed.append((pdf_path, f"page {i+1}: {e}"))
            except Exception as e:
                failed.append((pdf_path, f"page {i+1}: {e}"))
        total_pages += page_count
        exported.append(f"    {_rel(pdf_path, src)}  →  {pdf_out.name}/  ({page_count} page(s))")

    with _Section(config, f"  Exported ({len(exported)})") as s:
        for row in exported:
            s.add(row)

    _print_summary(config, copied=total_pages, failed=failed or None,
                   skipped=skipped or None, label="pages exported")
    _finalize_output(config, out, cancel)
    print(f"  Total: {len(pdfs)} {noun}(s), {total_pages} page(s) exported.")
    print(f"  Done! → {_shown(out)}")
    print("")


def pdf_splitter(config, cancel=None):
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)

    output_format = _doc_format(config)
    output_name = output_format.upper()
    print(f"{output_name} Splitter")
    print(f"  Input:  {src}")
    if not extract_archives(config, src, cancel):
        return _cancel()
    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()
    out = get_output(config, f"{output_format} split", run_name)
    print(f"  Output: {_shown(out)}")
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
                print(f"  Cancelled. ({written} part(s) saved so far)")
                print("")
                return
            page_indices = list(range(a - 1, b - 2, -1) if descending else range(a - 1, b))
            try:
                if to_epub:
                    out_path = out / f"{stem}_part{i + 1}.epub"
                    if is_epub:
                        part_pages = [_ZipImage(epub_zf, epub_members[p]) for p in page_indices]
                        if not build_epub(config, part_pages, out_path,
                                          f"{stem} - part {i + 1}", cancel, epub_direction):
                            print(f"  Cancelled. ({written} part(s) saved so far)")
                            print("")
                            return
                    else:
                        with tempfile.TemporaryDirectory(prefix="tankobon_split_pages_") as page_dir:
                            part_pages = _render_pdf_pages_as_images(
                                pdf_path, [p + 1 for p in page_indices], page_dir,
                                f"part{i + 1:04d}", cancel)
                            if not build_epub(config, part_pages, out_path,
                                              f"{stem} - part {i + 1}", cancel):
                                print(f"  Cancelled. ({written} part(s) saved so far)")
                                print("")
                                return
                elif is_epub:
                    writer = PdfWriter()
                    for p in page_indices:
                        for page in PdfReader(io.BytesIO(_image_to_pdf_bytes(epub_zf.read(epub_members[p])))).pages:
                            writer.add_page(page)
                    out_path = out / f"{stem}_part{i + 1}.pdf"
                    _write_pdf_atomically(writer, out_path)
                else:
                    writer = PdfWriter()
                    for p in page_indices:
                        writer.add_page(reader.pages[p])
                    out_path = out / f"{stem}_part{i + 1}.pdf"
                    _write_pdf_atomically(writer, out_path)
                direction = f" (reversed)" if descending else ""
                part_rows.append(f"    Part {i + 1}/{part_count}: pages {a}–{b}{direction}  →  {out_path.name}")
                written += 1
            except OSError as e:
                if _is_no_space(e):
                    print(f"  ✖ Disk full after {written} part(s). Stopping.")
                    _print_summary(config, copied=written, failed=failed or None, label="parts saved")
                    print("")
                    return
                failed.append((pdf_path, f"part {i + 1}: {e}"))
            except Exception as e:
                failed.append((pdf_path, f"part {i + 1}: {e}"))
    finally:
        if epub_zf is not None:
            epub_zf.close()

    with _Section(config, f"  Parts ({len(part_rows)})") as s:
        for row in part_rows:
            s.add(row)
    _print_summary(config, copied=written, failed=failed or None, label="parts saved")
    _finalize_output(config, out, cancel)
    print(f"  Done! {written} part(s) saved.")
    print(f"  → {_shown(out)}")
    print("")


def status(config):
    src = get_input(config)
    out_base = Path(config.get("output", "")) / "output"

    start_section, end_section = _get_log_section_fns()

    print("")
    if src.exists():
        items = list(src.iterdir())
        files = [i for i in items if i.is_file()]
        dirs  = [i for i in items if i.is_dir()]
        parts = []
        if dirs:  parts.append(f"{len(dirs)} folder(s)")
        if files: parts.append(f"{len(files)} file(s)")

        print(f"Input  —  {', '.join(parts) if parts else 'empty'}")

        for d in sorted(dirs, key=lambda x: natural_sort_key(x.name)):
            sub_dirs = sorted([i for i in d.iterdir() if i.is_dir()], key=lambda x: natural_sort_key(x.name))
            file_count = sum(1 for _ in d.rglob("*") if _.is_file())
            if sub_dirs:
                start_section(f"[folder] {d.name}/  ({file_count} file(s))")
                for sd in sub_dirs:
                    sc = sum(1 for _ in sd.rglob("*") if _.is_file())
                    print(f"    {sd.name}/  ({sc} file(s))")
                end_section()
            else:
                print(f"  [folder] {d.name}/  ({file_count} file(s))")

        for f in sorted(files, key=lambda x: natural_sort_key(x.name)):
            tag = {".zip": "[zip]   ", ".cbz": "[cbz]   "}.get(f.suffix.lower(), "[file]  ")
            print(f"  {tag} {f.name}")
    else:
        print("Input  —  (doesn't exist)")

    print("")

    if out_base.exists():
        visible = [i for i in out_base.iterdir() if not i.name.startswith(".")]     # hide staging folders
        items = [i for i in visible if i.is_dir()]
        out_files = [i for i in visible if i.is_file()]
        parts = []
        if items:     parts.append(f"{len(items)} folder(s)")
        if out_files: parts.append(f"{len(out_files)} file(s)")
        print(f"Output  —  {', '.join(parts)}" if parts else "Output  —  empty")
        for f in sorted(out_files, key=lambda x: natural_sort_key(x.name)):
            tag = {".zip": "[zip]   ", ".cbz": "[cbz]   "}.get(f.suffix.lower(), "[file]  ")
            print(f"  {tag} {f.name}")
        for op in sorted(items, key=lambda x: natural_sort_key(x.name)):
            sub_dirs = sorted([i for i in op.iterdir() if i.is_dir()], key=lambda x: natural_sort_key(x.name))
            file_count = sum(1 for f in op.rglob("*") if f.is_file())
            if sub_dirs:
                start_section(f"  {op.name}/  ({file_count} file(s))")
                for sd in sub_dirs:
                    sc = sum(1 for f in sd.rglob("*") if f.is_file())
                    print(f"    {sd.name}/  ({sc} file(s))")
                end_section()
            else:
                print(f"    {op.name}/  ({file_count} file(s))")
    else:
        print("Output  —  empty")

    print("")


configure_output_support(
    natural_sort_key=natural_sort_key, _rel=_rel, _fmt_size=_fmt_size, _total_size=_total_size,
    _Section=_Section, _print_summary=_print_summary, _is_no_space=_is_no_space,
    throttle_if_needed=throttle_if_needed, DOCUMENT_EXTENSIONS=DOCUMENT_EXTENSIONS,
    CBZ_IMAGE_EXTENSIONS=CBZ_IMAGE_EXTENSIONS, _ALREADY_COMPRESSED=_ALREADY_COMPRESSED,
)
