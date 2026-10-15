"""Canonical production-format choices and format-sensitive tool labels."""

DOCUMENT_FORMATS = ("pdf", "epub")
FOLDER_FORMATS = ("cbz", "zip", "uncompressed")
DEFAULT_DOCUMENT_FORMAT = "epub"
DEFAULT_FOLDER_FORMAT = "cbz"


def normalize_document_format(value):
    return value if value in DOCUMENT_FORMATS else DEFAULT_DOCUMENT_FORMAT


def normalize_folder_format(value):
    if value == "folder":
        value = "uncompressed"
    return value if value in FOLDER_FORMATS else DEFAULT_FOLDER_FORMAT


def document_format(config):
    return normalize_document_format(config.get("default_doc_format", DEFAULT_DOCUMENT_FORMAT))


def folder_format(config):
    return normalize_folder_format(config.get("zip_compression", DEFAULT_FOLDER_FORMAT))


def document_format_title(value):
    return normalize_document_format(value).upper()


def document_name(config):
    return document_format_title(document_format(config))


def folder_format_title(value):
    return {"zip": "ZIP", "cbz": "CBZ", "uncompressed": "Uncompressed"}[normalize_folder_format(value)]


def folder_tool_name(config, name):
    fmt = folder_format(config)
    if fmt == "uncompressed":
        return name
    if name == "Folder Renamer":
        return f"{folder_format_title(fmt)} Renamer"
    return f"{folder_format_title(fmt)} {name}"


def tool_label(tool_id, doc_format=DEFAULT_DOCUMENT_FORMAT, folder_format_value=DEFAULT_FOLDER_FORMAT):
    doc = document_format_title(doc_format)
    folder = normalize_folder_format(folder_format_value)
    doc_labels = {
        "folders_to_file": f"Folders to {doc}", "images_to_file": f"Images to {doc}",
        "document_combiner": f"{doc} Combiner", "document_splitter": f"{doc} Splitter",
        "document_to_images": f"{doc} to Images",
    }
    if tool_id in doc_labels:
        return doc_labels[tool_id]
    folder_labels = {
        "folder_renamer": "Renamer", "combine_image_sets": "Combine Image Sets",
        "image_converter": "Image Converter", "find_duplicates": "Find Duplicates",
    }
    if tool_id in folder_labels:
        base_name = "Folder Renamer" if tool_id == "folder_renamer" else folder_labels[tool_id]
        return folder_tool_name({"zip_compression": folder}, base_name)
    return {"file_renamer": "File Renamer", "add_input": "Add Input"}.get(
        tool_id, tool_id.replace("_", " ").title())
