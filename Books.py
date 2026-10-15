"""EPUB parsing, creation, and image-page adapters."""

import io
import os
import posixpath
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote
from xml.sax.saxutils import escape as _xml_escape

from PIL import Image
import img2pdf
from Util import (EPUB_IMAGE_TYPES, READABLE_EPUB_IMAGES, throttle_if_needed,
                  is_no_space, get_poppler_path)
from ToolNames import document_format

_IMG_REF = re.compile(r"<(?:img|image)\b[^>]*?\b(?:src|(?:xlink:)?href)\s*=\s*['\"]([^'\"]+)['\"]", re.I | re.S)

class PathImage:
    """A page image on disk, for build_epub()."""
    def __init__(self, path):
        self.path = Path(path)
        self.suffix = self.path.suffix.lower()

    def read(self):
        return self.path.read_bytes()


class ZipImage:
    """A page image inside an open zip (an EPUB), for build_epub()."""
    def __init__(self, zf, member):
        self.zf, self.member = zf, member
        self.suffix = Path(member).suffix.lower()

    def read(self):
        return self.zf.read(self.member)


def render_pdf_pages_as_images(pdf_path, page_numbers, temp_dir, prefix, cancel=None, dpi=150):
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
                                   last_page=page_number, poppler_path=get_poppler_path())
        if not images:
            raise RuntimeError(f"could not render PDF page {page_number}")
        path = Path(temp_dir) / f"{prefix}_{serial:06d}.png"
        try:
            images[0].save(path, format="PNG")
        finally:
            for image in images:
                image.close()
        rendered_pages.append(PathImage(path))
    return rendered_pages


class BuildCancelled(Exception):
    pass




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
        if Path(page).suffix.lower() in READABLE_EPUB_IMAGES:           # an image listed directly in the spine
            members.append(page)
            continue
        text = zf.read(page).decode("utf-8", "replace")
        for ref in _IMG_REF.findall(text):
            ref = unquote(ref.split("#")[0].split("?")[0])
            if ref.startswith(("http:", "https:", "data:")):
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(page), ref))
            if target in names and Path(target).suffix.lower() in READABLE_EPUB_IMAGES:
                members.append(target)
    return members, direction, title


def build_epub(config, pages, out_path, title, cancel=None, direction=None):
    """Writes a fixed-layout (pre-paginated) EPUB 3 with one image per page.
    pages = objects with .suffix and .read() (see PathImage / ZipImage). Returns False if cancelled.
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
                    raise BuildCancelled()
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
    except BuildCancelled:
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
    if document_format(config) == "pdf":
        save_pdf(image_paths, stem_path.with_name(stem_path.name + ".pdf"))
        return True
    if not image_paths:
        print("  No images to save.")
        return True
    print(f"  Converting {len(image_paths)} images → EPUB...")
    return build_epub(config, [PathImage(p) for p in image_paths],
                      stem_path.with_name(stem_path.name + ".epub"), title or stem_path.name, cancel)


def image_to_pdf_bytes(data):
    """One page image (bytes) as a one-page PDF."""
    try:
        return img2pdf.convert(data)
    except Exception:                                        # transparency, webp, ...: let Pillow flatten it
        buf = io.BytesIO()
        with Image.open(io.BytesIO(data)) as im:
            im.convert("RGB").save(buf, "PDF")
        return buf.getvalue()


def export_epub_pages(config, epub_path, dest_dir, fmt, cancel=None):
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
            if is_no_space(e):
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



def write_pdf_atomically(writer, output_path):
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
