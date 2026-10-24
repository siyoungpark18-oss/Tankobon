"""Stable tool IDs and the metadata the interface needs to present each tool."""

from Manager import (
    folders_to_file,
    images_to_file,
    folder_renamer,
    file_renamer,
    combine_image_sets,
    image_converter,
    find_duplicates,
    document_combiner,
    document_splitter,
    document_to_images,
    epub_metadata,
)


TOOL_REGISTRY = {
    "folders_to_file": {
        "options": ["combine", "individual"],
        "config_key": "default_folders_to_pdf_mode",
        "handler": folders_to_file,
        "tip": "Turns image folders into the selected document format: one combined file or one file per folder.",
    },
    "images_to_file": {
        "options": ["combine", "reorder & combine"],
        "config_key": "default_images_to_pdf_mode",
        "handler": images_to_file,
        "tip": "Combines the images in Input into one file in the selected document format.",
    },
    "folder_renamer": {
        "options": ["prefix", "suffix", "replace", "extract number"],
        "config_key": "default_folder_renamer_mode",
        "handler": folder_renamer,
        "tip": "Renames folders: add a prefix or suffix, find and replace, or keep just the number. Renamed copies go to Output.",
    },
    "file_renamer": {
        "options": ["prefix", "suffix", "replace", "sequence"],
        "config_key": "default_file_renamer_mode",
        "handler": file_renamer,
        "tip": "Renames files by prefix, suffix, find and replace, or sequence numbering. Renamed copies go to Output.",
    },
    "combine_image_sets": {
        "options": [],
        "handler": combine_image_sets,
        "tip": "Merges multiple folders of images into one flat folder, preserving order.",
    },
    "image_converter": {
        "options": ["jpg", "png", "webp", "bmp", "tiff"],
        "config_key": "default_img_fmt",
        "handler": image_converter,
        "tip": "Converts images in Input to a chosen format (JPG, PNG, WebP, and more).",
    },
    "find_duplicates": {
        "options": ["keep one copy", "delete all"],
        "config_key": "default_dedupe_mode",
        "handler": find_duplicates,
        "tip": "Finds duplicate images and copies the remaining images to Output. Nothing in Input is changed.",
    },
    "document_combiner": {
        "options": [],
        "handler": document_combiner,
        "tip": "Combines PDF and image-based EPUB inputs into one file in the selected document format. A document that fails to read completely is omitted by default; enable page salvage in Preferences > Tools to keep its readable pages.",
    },
    "document_splitter": {
        "options": [],
        "handler": document_splitter,
        "tip": "Splits the selected PDF or image-based EPUB into parts at page ranges you enter.",
    },
    "document_to_images": {
        "options": ["jpg", "png"],
        "config_key": "default_pdf_to_images_fmt",
        "handler": document_to_images,
        "tip": "Exports page images from PDFs and image-based EPUBs.",
    },
    "epub_metadata": {
        "options": [],
        "handler": epub_metadata,
        "tip": "Adds metadata to copies of EPUB files in Input. Enter Series and Language once, then a Title for each EPUB; pressing Enter repeats the previous title. A clear chapter number in the filename is stored as its position in the Series.",
    },
    "add_input": {
        "options": ["files", "folder", "output"],
        "tip": "Copies files or a folder into Input for processing. Zip files are unpacked automatically.",
    },
}

OPTION_LABELS = {
    "combine": "Combine all → one file",
    "individual": "One file per folder",
    "prefix": "Prefix",
    "suffix": "Suffix",
    "replace": "Find & Replace",
    "extract number": "Extract Number",
    "sequence": "Sequence",
    "jpg": "JPG",
    "png": "PNG",
    "webp": "WebP",
    "bmp": "BMP",
    "tiff": "TIFF",
    "keep one copy": "Keep one copy",
    "delete all": "Delete all instances",
    "files": "Files",
    "folder": "Individual Folder",
    "output": "From Output",
    "reorder & combine": "Reorder & Combine",
}

TOGGLEABLE = [
    ("show_folders_to_pdf", "Folder", "folders_to_file"),
    ("show_images_to_pdf", "Folder", "images_to_file"),
    ("show_folder_renamer", "Folder", "folder_renamer"),
    ("show_file_renamer", "Folder", "file_renamer"),
    ("show_combine", "Folder", "combine_image_sets"),
    ("show_converter", "Folder", "image_converter"),
    ("show_duplicates", "Folder", "find_duplicates"),
    ("show_pdf_combiner", "Folder", "document_combiner"),
    ("show_pdf_splitter", "File", "document_splitter"),
    ("show_pdf_to_images", "File", "document_to_images"),
    ("show_epub_metadata", "Folder", "epub_metadata"),
]

UTILITY_TOOLTIPS = {
    "Clear Input": "Deletes everything in the Input folder. Your original files are not affected (Input only holds copies).\nTo be asked first, turn on Preferences > General > Confirm before Clearing.",
    "Status": "Shows what is currently in the Input and Output folders.",
    "Clear Log": "Clears the log display.",
    "Clear Output": "Deletes everything in the Output folder.\nTo be asked first, turn on Preferences > General > Confirm before Clearing.",
    "Cancel Operation": "Stops the running job at its next safe point. PDF writing itself cannot be interrupted.",
    "Open Input": "Opens the input folder in Finder/Explorer.",
    "Open Output": "Opens the output folder in Finder/Explorer and prints the path to the log.",
}
