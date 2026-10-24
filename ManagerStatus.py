"""Input and Output status reporting."""

from pathlib import Path

from LogFormat import section_callbacks
from ManagerConfig import get_input
from Util import natural_sort_key


def status(config):
    """Print a summary of the configured Input and Output folders."""
    src = get_input(config)
    out_base = Path(config.get("output", "")) / "output"

    start_section, end_section = section_callbacks()

    print("")
    if src.exists():
        items = list(src.iterdir())
        files = [item for item in items if item.is_file()]
        dirs = [item for item in items if item.is_dir()]
        parts = []
        if dirs:
            parts.append(f"{len(dirs)} folder(s)")
        if files:
            parts.append(f"{len(files)} file(s)")

        print(f"Input  —  {', '.join(parts) if parts else 'empty'}")

        for folder in sorted(dirs, key=lambda item: natural_sort_key(item.name)):
            sub_dirs = sorted(
                (item for item in folder.iterdir() if item.is_dir()),
                key=lambda item: natural_sort_key(item.name),
            )
            file_count = sum(1 for item in folder.rglob("*") if item.is_file())
            if sub_dirs:
                start_section(f"[folder] {folder.name}/  ({file_count} file(s))")
                for sub_dir in sub_dirs:
                    count = sum(1 for item in sub_dir.rglob("*") if item.is_file())
                    print(f"    {sub_dir.name}/  ({count} file(s))")
                end_section()
            else:
                print(f"  [folder] {folder.name}/  ({file_count} file(s))")

        for file_path in sorted(files, key=lambda item: natural_sort_key(item.name)):
            tag = {".zip": "[zip]   ", ".cbz": "[cbz]   "}.get(
                file_path.suffix.lower(), "[file]  "
            )
            print(f"  {tag} {file_path.name}")
    else:
        print("Input  —  (doesn't exist)")

    print("")

    if out_base.exists():
        visible = [item for item in out_base.iterdir() if not item.name.startswith(".")]
        folders = [item for item in visible if item.is_dir()]
        files = [item for item in visible if item.is_file()]
        parts = []
        if folders:
            parts.append(f"{len(folders)} folder(s)")
        if files:
            parts.append(f"{len(files)} file(s)")
        print(f"Output  —  {', '.join(parts)}" if parts else "Output  —  empty")

        for file_path in sorted(files, key=lambda item: natural_sort_key(item.name)):
            tag = {".zip": "[zip]   ", ".cbz": "[cbz]   "}.get(
                file_path.suffix.lower(), "[file]  "
            )
            print(f"  {tag} {file_path.name}")

        for folder in sorted(folders, key=lambda item: natural_sort_key(item.name)):
            sub_dirs = sorted(
                (item for item in folder.iterdir() if item.is_dir()),
                key=lambda item: natural_sort_key(item.name),
            )
            file_count = sum(1 for item in folder.rglob("*") if item.is_file())
            if sub_dirs:
                start_section(f"  {folder.name}/  ({file_count} file(s))")
                for sub_dir in sub_dirs:
                    count = sum(1 for item in sub_dir.rglob("*") if item.is_file())
                    print(f"    {sub_dir.name}/  ({count} file(s))")
                end_section()
            else:
                print(f"    {folder.name}/  ({file_count} file(s))")
    else:
        print("Output  —  empty")

    print("")
