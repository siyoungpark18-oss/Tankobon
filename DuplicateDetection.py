"""Hashing and grouping helpers for duplicate-image detection."""

import hashlib
from dataclasses import dataclass

from PIL import Image


@dataclass
class DuplicateDetectionResult:
    groups: list
    duplicates: set
    failures: list
    cancelled: bool = False


def find_exact_duplicates(files, cancel=None):
    """Group files with identical contents; keep the first file in each group."""
    seen = {}
    file_digests = {}
    failures = []
    for file_path in files:
        if cancel is not None and cancel.is_set():
            return DuplicateDetectionResult([], set(), failures, cancelled=True)
        try:
            digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
            file_digests[file_path] = digest
            if digest not in seen:
                seen[digest] = file_path
        except Exception as error:
            failures.append((file_path, str(error)))

    groups_by_digest = {}
    for file_path, digest in file_digests.items():
        groups_by_digest.setdefault(digest, []).append(file_path)
    groups = [group for group in groups_by_digest.values() if len(group) > 1]
    duplicates = {file_path for group in groups for file_path in group[1:]}
    return DuplicateDetectionResult(groups, duplicates, failures)


def find_perceptual_duplicates(files, threshold, cancel=None):
    """Group perceptually similar images using their pHash distance."""
    import imagehash

    file_hashes = {}
    failures = []
    for file_path in files:
        if cancel is not None and cancel.is_set():
            return DuplicateDetectionResult([], set(), failures, cancelled=True)
        try:
            with Image.open(file_path) as image:
                file_hashes[file_path] = imagehash.phash(image)
        except Exception as error:
            failures.append((file_path, str(error)))

    representatives = []
    groups = []
    for file_path in files:
        if file_path not in file_hashes:
            continue
        image_hash = file_hashes[file_path]
        for index, (_, representative_hash) in enumerate(representatives):
            if (image_hash - representative_hash) <= threshold:
                groups[index].append(file_path)
                break
        else:
            representatives.append((file_path, image_hash))
            groups.append([file_path])

    duplicate_groups = [group for group in groups if len(group) > 1]
    duplicates = {
        file_path for group in duplicate_groups for file_path in group[1:]
    }
    return DuplicateDetectionResult(duplicate_groups, duplicates, failures)


def files_to_exclude(groups, duplicates, mode):
    """Return the files omitted from output for a validated removal mode."""
    if mode == "delete all":
        return {file_path for group in groups for file_path in group}
    if mode == "keep one copy":
        return duplicates
    return None
