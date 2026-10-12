"""Shared display and operation names for format-sensitive tools."""


def document_name(config):
    fmt = config.get("default_doc_format", "epub")
    return (fmt if fmt in ("pdf", "epub") else "epub").upper()


def folder_format_title(config):
    return {"zip": "ZIP", "cbz": "CBZ", "uncompressed": "Uncompressed"}.get(
        config.get("zip_compression", "cbz"), "CBZ")


def folder_tool_name(config, name):
    if config.get("zip_compression", "cbz") == "uncompressed":
        return name
    if name == "Folder Renamer":
        return f"{folder_format_title(config)} Renamer"
    return f"{folder_format_title(config)} {name}"


def tool_label(tool_id, doc_format="epub", folder_format="cbz"):
    doc = (doc_format if doc_format in ("pdf", "epub") else "epub").upper()
    folder = {"zip": "ZIP", "cbz": "CBZ", "uncompressed": "Uncompressed"}.get(folder_format, "CBZ")
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
        if folder_format == "uncompressed":
            return "Folder Renamer" if tool_id == "folder_renamer" else folder_labels[tool_id]
        return f"{folder} {folder_labels[tool_id]}"
    return {"file_renamer": "File Renamer", "add_input": "Add Input"}.get(
        tool_id, tool_id.replace("_", " ").title())
