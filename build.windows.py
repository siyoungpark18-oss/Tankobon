"""
Build script for Tankobon — Windows
Run this from the project folder: python build.windows.py

Prerequisites:
  1. Python 3.8+ installed from python.org (with tkinter checked)
  2. Internet connection (Poppler for Windows is downloaded automatically)
"""

import subprocess
import sys
import shutil
import urllib.request
import json
import zipfile
import io
from pathlib import Path

APP_NAME    = "Tankobon"
MAIN_SCRIPT = "Interface.py"
DIST        = Path("dist")
APP_DIR     = DIST / APP_NAME
POPPLER_SRC = Path("poppler")

POPPLER_RELEASES_API = "https://api.github.com/repos/oschwartz10612/poppler-windows/releases/latest"


def run(cmd):
    print(f"\n>>> {' '.join(str(c) for c in cmd)}\n")
    subprocess.run(cmd, check=True)


def clean():
    print("Cleaning previous build artifacts...")
    for folder in ["build", "dist", "__pycache__"]:
        if Path(folder).exists():
            shutil.rmtree(folder)
            print(f"  Removed {folder}/")
    for spec in Path(".").glob("*.spec"):
        spec.unlink()
        print(f"  Removed {spec.name}")


def prepare_poppler_windows():
    bin_dir = POPPLER_SRC / "bin"

    if (bin_dir / "pdftoppm.exe").exists():
        print(f"  Poppler already at {bin_dir.resolve()}")
        return

    print("Downloading Poppler for Windows...")
    try:
        req = urllib.request.Request(
            POPPLER_RELEASES_API,
            headers={"User-Agent": "Tankobon-build"}
        )
        with urllib.request.urlopen(req) as resp:
            release = json.loads(resp.read())
    except Exception as e:
        print(f"\n  ERROR: Could not fetch Poppler release info: {e}")
        print("  Download manually from: https://github.com/oschwartz10612/poppler-windows/releases")
        print("  Extract and place binaries at: poppler/bin/pdftoppm.exe")
        sys.exit(1)

    zip_url = next(
        (a["browser_download_url"] for a in release["assets"] if a["name"].endswith(".zip")),
        None
    )
    if not zip_url:
        print("  ERROR: No zip found in latest Poppler release.")
        sys.exit(1)

    print(f"  Fetching {release['tag_name']} from {zip_url} ...")
    with urllib.request.urlopen(zip_url) as resp:
        data = resp.read()

    bin_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        extracted = 0
        for member in zf.namelist():
            if "/Library/bin/" in member and not member.endswith("/"):
                filename = Path(member).name
                with zf.open(member) as src:
                    (bin_dir / filename).write_bytes(src.read())
                extracted += 1

    print(f"  Done. ({extracted} files extracted to {bin_dir}/)")


REQUIREMENTS = """\
pyinstaller
pillow
img2pdf
pypdf
pdf2image
psutil
"""

def ensure_requirements():
    if not Path("requirements.txt").exists():
        Path("requirements.txt").write_text(REQUIREMENTS)
        print("  Created requirements.txt")

def install_dependencies():
    print("Installing dependencies...")
    ensure_requirements()
    run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])


def build_app():
    print("Building with PyInstaller...")
    poppler_bin = str(POPPLER_SRC / "bin")

    source_files = ["Manager.py", "Log.py", "Preferences.py", "Themes.py"]
    add_data_args = []
    for f in source_files:
        add_data_args += ["--add-data", f"{f};."]
    add_data_args += ["--add-data", "assets;assets"]

    run([
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--onedir",
        "--name", APP_NAME,
        "--icon", "assets/icon.ico",
        "--icon", "assets/icon.ico",
        "--hidden-import", "psutil",
        "--hidden-import", "pdf2image",
        "--hidden-import", "PIL._tkinter_finder",
        *add_data_args,
        "--add-data", f"{poppler_bin};poppler/bin",
        MAIN_SCRIPT,
    ])
    print(f"\nBuild output: {APP_DIR}")


if __name__ == "__main__":
    try:
        clean()
        prepare_poppler_windows()
        install_dependencies()
        build_app()
        print(f"\nDone! Distribute the folder: dist/{APP_NAME}/")
        print(f"  Run with: dist/{APP_NAME}/{APP_NAME}.exe")
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed: {e}")
        sys.exit(1)
