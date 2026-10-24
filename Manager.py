#IMPORTS——————————————————————————————————————————————————————————————————————————————————————————————————
import re
import shutil
from Util import (natural_sort_key, rel, int_cfg, is_no_space,
                  IMAGE_EXTENSIONS, resolve_sort, get_poppler_path)
from LogFormat import Section, log_group, log_scan, size_suffix, print_summary
from ToolNames import document_name, folder_tool_name
from ManagerConfig import get_input
from ManagerSupport import SENTINEL, _cancel, _check_disk_space, _get_run_name
from RenameOperations import file_renamer as _file_renamer, folder_renamer as _folder_renamer
from ManagerStatus import status as _status_report
from DuplicateDetection import (
    find_exact_duplicates,
    find_perceptual_duplicates,
    files_to_exclude,
)
from DuplicateManager import find_duplicates as _find_duplicates
from ImageSets import (
    collect_direct_image_paths,
    collect_image_paths,
    combine_image_sets as _combine_image_sets,
)
from ImageConversion import image_converter as _image_converter
from ImageBooks import folders_to_file as _folders_to_file, images_to_file as _images_to_file
from DocumentSplitter import document_splitter as _document_splitter
from DocumentImages import document_to_images as _document_to_images
from DocumentCombiner import document_combiner as _document_combiner
from MetadataTools import epub_metadata as _epub_metadata
from ManagerOutput import (
    get_output, finalize_output, shown, unique_path, unique_output_stem,
)
from Archives import extract_archives
from Books import (
    save_book, export_epub_pages,
)
#Thanks to codex for making this so much shorter :D

def folders_to_file(config, cancel=None):
    return _folders_to_file(config, cancel)


def images_to_file(config, cancel=None):
    return _images_to_file(config, cancel)



def folder_renamer(config, cancel=None):
    return _folder_renamer(config, cancel)


def file_renamer(config, cancel=None):
    return _file_renamer(config, cancel)


def combine_image_sets(config, cancel=None):
    return _combine_image_sets(config, cancel)


def image_converter(config, cancel=None):
    return _image_converter(config, cancel)


def find_duplicates(config, cancel=None):
    return _find_duplicates(config, cancel)


def document_combiner(config, cancel=None):
    return _document_combiner(config, cancel)


def document_to_images(config, cancel=None):
    return _document_to_images(config, cancel)


def document_splitter(config, cancel=None):
    return _document_splitter(config, cancel)


def epub_metadata(config, cancel=None):
    return _epub_metadata(config, cancel)


def status(config):
    return _status_report(config)
