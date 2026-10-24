"""EPUB metadata editing and batch operations."""

import os
import posixpath
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from LogFormat import Section
from ManagerConfig import get_input
from ManagerOutput import finalize_output, get_output, unique_path, shown
from ManagerSupport import SENTINEL, _cancel, _check_disk_space, _get_run_name
from Util import natural_sort_key, rel, throttle_if_needed


_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_OPF_NS = "http://www.idpf.org/2007/opf"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_CHAPTER_LABEL = re.compile(
    r"(?i)(?:\bchapter\b|\bch\.?)\s*#?\s*(\d+(?:\.\d+)?)(?![\w.])"
)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")


def chapter_number_from_filename(filename):
    """Return a clearly identified chapter number without guessing between numbers."""
    stem = Path(filename).stem
    labelled = _CHAPTER_LABEL.findall(stem)
    if len(labelled) == 1:
        return labelled[0]
    if labelled:
        return None
    numbers = _NUMBER.findall(stem)
    return numbers[0] if len(numbers) == 1 else None


def _find_opf_path(archive):
    try:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
    except KeyError as error:
        raise ValueError("not a valid EPUB (no META-INF/container.xml)") from error
    rootfile = next(
        (element for element in container.iter()
         if element.tag == f"{{{_CONTAINER_NS}}}rootfile" and element.get("full-path")),
        None,
    )
    if rootfile is None:
        raise ValueError("not a valid EPUB (no package file)")
    opf_path = posixpath.normpath(rootfile.get("full-path").replace("\\", "/"))
    if opf_path.startswith("/") or opf_path == ".." or opf_path.startswith("../"):
        raise ValueError("invalid EPUB package path")
    if opf_path not in archive.namelist():
        raise ValueError("not a valid EPUB (package file is missing)")
    return opf_path


def _metadata_element(package):
    metadata = package.find(f"{{{_OPF_NS}}}metadata")
    if metadata is None:
        metadata = ET.Element(f"{{{_OPF_NS}}}metadata")
        package.insert(0, metadata)
    return metadata


def _set_dc_value(metadata, name, value):
    tag = f"{{{_DC_NS}}}{name}"
    elements = metadata.findall(tag)
    if not elements:
        elements = [ET.SubElement(metadata, tag)]
    elements[0].text = value
    for duplicate in elements[1:]:
        duplicate_id = duplicate.get("id")
        if duplicate_id:
            for refinement in metadata.findall(f"{{{_OPF_NS}}}meta"):
                if refinement.get("refines") == f"#{duplicate_id}":
                    metadata.remove(refinement)
        metadata.remove(duplicate)


def _remove_existing_series(metadata):
    """Remove the existing series collection and its refinements, if one is present."""
    metas = metadata.findall(f"{{{_OPF_NS}}}meta")
    remove = set()
    for element in metas:
        if element.get("property") != "belongs-to-collection":
            continue
        element_id = element.get("id")
        if not element_id:
            continue
        refinements = [item for item in metas if item.get("refines") == f"#{element_id}"]
        is_series = any(
            item.get("property") == "collection-type" and (item.text or "").strip().casefold() == "series"
            for item in refinements
        )
        if is_series:
            remove.add(element)
            remove.update(refinements)
    for element in remove:
        metadata.remove(element)


def _add_series_metadata(metadata, series, chapter):
    if not series:
        return
    used_ids = {item.get("id") for item in metadata.findall(f"{{{_OPF_NS}}}meta") if item.get("id")}
    collection_id = "tankobon-series"
    suffix = 2
    while collection_id in used_ids:
        collection_id = f"tankobon-series-{suffix}"
        suffix += 1
    ET.SubElement(metadata, f"{{{_OPF_NS}}}meta", {
        "id": collection_id,
        "property": "belongs-to-collection",
    }).text = series
    ET.SubElement(metadata, f"{{{_OPF_NS}}}meta", {
        "refines": f"#{collection_id}",
        "property": "collection-type",
    }).text = "series"
    if chapter:
        ET.SubElement(metadata, f"{{{_OPF_NS}}}meta", {
            "refines": f"#{collection_id}",
            "property": "group-position",
        }).text = chapter


def update_opf_metadata(opf_bytes, title, series, language, chapter=None):
    """Update a package document while preserving its other metadata and structure."""
    ET.register_namespace("", _OPF_NS)
    ET.register_namespace("dc", _DC_NS)
    package = ET.fromstring(opf_bytes)
    metadata = _metadata_element(package)
    _set_dc_value(metadata, "title", str(title))
    _set_dc_value(metadata, "language", str(language))
    _remove_existing_series(metadata)
    _add_series_metadata(metadata, str(series).strip(), str(chapter).strip() if chapter else None)
    return ET.tostring(package, encoding="utf-8", xml_declaration=True)


def update_epub_metadata(source, destination, title, series, language, chapter=None):
    """Write an EPUB copy with updated title, language, series, and optional chapter position."""
    source, destination = Path(source), Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".partial", dir=destination.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(source, "r") as original:
            opf_path = _find_opf_path(original)
            updated_opf = update_opf_metadata(
                original.read(opf_path), title, series, language, chapter
            )
            with zipfile.ZipFile(temporary, "w", allowZip64=True) as updated:
                updated.comment = original.comment
                for info in original.infolist():
                    payload = updated_opf if info.filename == opf_path else original.read(info.filename)
                    updated.writestr(info, payload)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _required_input(prompt, field_name):
    while True:
        value = input(prompt)
        if value == SENTINEL:
            return None
        value = value.strip()
        if value:
            return value
        print(f"  {field_name} is required.")


def epub_metadata(config, cancel=None):
    """Add manually supplied EPUB metadata to copies of every EPUB in Input."""
    src = get_input(config)
    src.mkdir(parents=True, exist_ok=True)
    print("EPUB Metadata")
    print(f"  Input:  {src}")

    run_name = _get_run_name(config)
    if run_name is None:
        return _cancel()

    files = sorted(
        (path for path in src.rglob("*") if path.is_file() and path.suffix.lower() == ".epub"),
        key=lambda path: natural_sort_key(rel(path, src)),
    )
    if not files:
        print("  No EPUB files found in Input!")
        print("")
        return

    series = input("Series (needed to store chapter position; leave blank if none): ")
    if series == SENTINEL:
        return _cancel()
    series = series.strip()
    language = _required_input("Language (for example, en or ja): ", "Language")
    if language is None:
        return _cancel()

    metadata_rows = []
    previous_title = None
    for path in files:
        if cancel is not None and cancel.is_set():
            return _cancel()
        while True:
            title = input(f"Title for {rel(path, src)} (Enter repeats the previous title): ")
            if title == SENTINEL:
                return _cancel()
            title = title.strip()
            if title:
                previous_title = title
                break
            if previous_title:
                title = previous_title
                break
            print("  Enter a title for the first EPUB; there is no previous title to repeat yet.")
        chapter = chapter_number_from_filename(path.name)
        if chapter is None:
            print(f"  ⚠ Could not identify one chapter number in '{path.name}'; chapter position will be omitted.")
        metadata_rows.append((path, title, chapter))

    out = get_output(config, "EPUB Metadata", run_name)
    print(f"  Output: {shown(out)}")
    if not _check_disk_space(out, config):
        return

    completed, failed = [], []
    reserved = set()
    for path, title, chapter in metadata_rows:
        if cancel is not None and cancel.is_set():
            print(f"  Cancelled. ({len(completed)} EPUB(s) updated so far)")
            finalize_output(config, out, cancel)
            return
        throttle_if_needed(config, cancel)
        destination = unique_path(out / path.name, reserved=reserved)
        try:
            update_epub_metadata(path, destination, title, series, language, chapter)
            completed.append(f"    {rel(path, src)}  →  {destination.name}")
        except Exception as error:
            failed.append((path, str(error)))

    with Section(config, f"  EPUB files updated ({len(completed)})") as section:
        for row in completed:
            section.add(row)
    if failed:
        with Section(config, f"  EPUB files failed ({len(failed)})") as section:
            for path, error in failed:
                section.add(f"    {rel(path, src)}: {error}")

    if finalize_output(config, out, cancel):
        print(f"  Done! → {shown(out)}")
    print("")
