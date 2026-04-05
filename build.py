"""
Build script for Tankobon
Run this from the project folder: python build.py
"""

import subprocess
import sys
import shutil
from pathlib import Path

APP_NAME    = "Tankobon"
MAIN_SCRIPT = "Interface.py"
DIST        = Path("dist")
APP_PATH    = DIST / f"{APP_NAME}.app"
DMG_PATH    = Path(f"{APP_NAME}.dmg")


def run(cmd):
    print(f"\n>>> {' '.join(cmd)}\n")
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
    print("Installing build dependencies...")
    ensure_requirements()
    run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])


HOMEBREW_POPPLER_BIN = [
    "/opt/homebrew/opt/poppler/bin",   # Apple Silicon
    "/usr/local/opt/poppler/bin",       # Intel
]

HOMEBREW_SEARCH = [
    "/opt/homebrew/opt/poppler/lib",
    "/opt/homebrew/lib",
    "/usr/local/opt/poppler/lib",
    "/usr/local/lib",
]

def _get_rpaths(binary):
    out = subprocess.check_output(["otool", "-l", str(binary)], text=True)
    rpaths, lines = [], out.splitlines()
    for i, line in enumerate(lines):
        if "LC_RPATH" in line:
            for j in range(i, min(i + 5, len(lines))):
                if "path " in lines[j]:
                    rpaths.append(lines[j].strip().split("path ")[1].split(" ")[0])
    return rpaths

def _resolve_rpath(dep, binary):
    """Resolve @rpath/libname.dylib to an absolute path by searching known locations."""
    lib_name = dep[len("@rpath/"):]
    loader_dir = Path(binary).parent
    for rpath in _get_rpaths(binary):
        if rpath.startswith("@loader_path"):
            candidate = loader_dir / rpath[len("@loader_path/"):] / lib_name
        elif not rpath.startswith("@"):
            candidate = Path(rpath) / lib_name
        else:
            continue
        if candidate.exists():
            return str(candidate)
    for search in HOMEBREW_SEARCH:
        candidate = Path(search) / lib_name
        if candidate.exists():
            return str(candidate)
    return None

def _all_deps(binary):
    """Return absolute paths for all non-system deps, resolving @rpath entries."""
    try:
        out = subprocess.check_output(["otool", "-L", str(binary)], text=True)
    except subprocess.CalledProcessError:
        return []
    SYSTEM = ("/usr/lib/", "/System/")
    deps = []
    for line in out.splitlines()[1:]:
        parts = line.strip().split()
        if not parts:
            continue
        dep = parts[0]
        if any(dep.startswith(s) for s in SYSTEM):
            continue
        if dep.startswith("@loader_path") or dep.startswith("@executable_path"):
            continue
        if dep.startswith("@rpath"):
            dep = _resolve_rpath(dep, binary) or ""
        if not dep or Path(dep).resolve() == Path(binary).resolve():
            continue
        deps.append(dep)
    return deps


def ensure_poppler_mac():
    """Download/install Poppler binaries into poppler/bin/ if not already present."""
    bin_dir = Path("poppler/bin")

    if (bin_dir / "pdftoppm").exists():
        print(f"  Poppler already at {bin_dir.resolve()}")
        return

    print("Poppler not found locally. Searching for Homebrew installation...")

    def _copy_from(src_bin):
        bin_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for exe in Path(src_bin).iterdir():
            dest = bin_dir / exe.name
            shutil.copy2(exe, dest)
            dest.chmod(0o755)
            count += 1
        print(f"  Copied {count} binaries to {bin_dir}/")

    for path in HOMEBREW_POPPLER_BIN:
        if (Path(path) / "pdftoppm").exists():
            print(f"  Found Poppler at {path}")
            _copy_from(path)
            return

    brew = shutil.which("brew")
    if not brew:
        print("\n  ERROR: Homebrew not found and Poppler is not installed.")
        print("  Install Homebrew from https://brew.sh, then re-run this script.")
        print("  Or install Poppler manually: brew install poppler")
        sys.exit(1)

    print("  Installing Poppler via Homebrew (this may take a minute)...")
    subprocess.run([brew, "install", "poppler"], check=True)

    for path in HOMEBREW_POPPLER_BIN:
        if (Path(path) / "pdftoppm").exists():
            _copy_from(path)
            return

    print("\n  ERROR: Poppler installed but binaries not found at expected paths.")
    print(f"  Expected one of: {HOMEBREW_POPPLER_BIN}")
    sys.exit(1)


def prepare_poppler_mac():
    """Copy all Poppler dylib dependencies into poppler/lib/ and fix install names."""
    print("Preparing Poppler for bundling...")
    bin_dir = Path("poppler/bin")
    lib_dir = Path("poppler/lib")
    lib_dir.mkdir(parents=True, exist_ok=True)

    # BFS: collect all transitive deps starting from the binaries
    queue = list(bin_dir.iterdir())
    queued = {str(p.resolve()) for p in queue}
    bin_count = len(queue)

    i = 0
    while i < len(queue):
        item = queue[i]
        i += 1
        for dep in _all_deps(item):
            key = str(Path(dep).resolve())
            if key not in queued and Path(dep).exists():
                queued.add(key)
                queue.append(Path(dep))

    # Copy all discovered dylibs
    copied = 0
    for src in queue[bin_count:]:
        dst = lib_dir / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
            dst.chmod(0o755)
            print(f"  Bundled {src.name}")
            copied += 1

    # Fix install names in bin/ and lib/
    all_files = list(bin_dir.iterdir()) + list(lib_dir.iterdir())
    for item in all_files:
        is_bin = item.parent == bin_dir
        for dep in _all_deps(item):
            dep_name = Path(dep).name
            new_path = f"@loader_path/../lib/{dep_name}" if is_bin else f"@loader_path/{dep_name}"
            subprocess.run(
                ["install_name_tool", "-change", dep, new_path, str(item)],
                capture_output=True,
            )
        # Also fix any @rpath entries that now live in lib/
        if is_bin:
            try:
                out = subprocess.check_output(["otool", "-L", str(item)], text=True)
            except subprocess.CalledProcessError:
                continue
            for line in out.splitlines()[1:]:
                parts = line.strip().split()
                if not parts:
                    continue
                dep = parts[0]
                if dep.startswith("@rpath/"):
                    lib_name = dep[len("@rpath/"):]
                    if (lib_dir / lib_name).exists():
                        subprocess.run(
                            ["install_name_tool", "-change", dep,
                             f"@loader_path/../lib/{lib_name}", str(item)],
                            capture_output=True,
                        )

    # Fix -id for each dylib in lib/
    for lib in lib_dir.iterdir():
        subprocess.run(
            ["install_name_tool", "-id", f"@rpath/{lib.name}", str(lib)],
            capture_output=True,
        )

    print(f"  Done. ({copied} libraries bundled into poppler/lib/)")


def build_app():
    print("Building .app with PyInstaller...")

    source_files = ["Manager.py", "Log.py", "Preferences.py", "Themes.py"]

    add_data_args = []
    for f in source_files:
        add_data_args += ["--add-data", f"{f}:."]
    add_data_args += ["--add-data", "assets:assets"]
    add_data_args += ["--add-data", "poppler/bin:poppler/bin"]
    add_data_args += ["--add-data", "poppler/lib:poppler/lib"]

    run([
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--onedir",
        "--name", APP_NAME,
        "--icon", "assets/icon.icns",
        "--osx-bundle-identifier", "com.tankobon.app",
        "--hidden-import", "psutil",
        "--hidden-import", "pdf2image",
        "--hidden-import", "PIL._tkinter_finder",
        *add_data_args,
        MAIN_SCRIPT,
    ])
    subprocess.run(["chflags", "-R", "nouchg", str(APP_PATH)], check=True)
    subprocess.run(["chmod", "-R", "u+w", str(APP_PATH)], check=True)
    print(f"\n.app built at: {APP_PATH}")


def build_dmg():
    print("Building .dmg...")
    if DMG_PATH.exists():
        DMG_PATH.unlink()

    staging = Path("dist/dmg_staging")
    staging.mkdir(parents=True, exist_ok=True)

    app_dest = staging / APP_PATH.name
    if app_dest.exists():
        shutil.rmtree(app_dest)
    shutil.copytree(str(APP_PATH), str(app_dest))
    subprocess.run(["chflags", "-R", "nouchg", str(app_dest)], check=True)
    subprocess.run(["chmod", "-R", "u+w", str(app_dest)], check=True)

    applications_link = staging / "Applications"
    if not applications_link.exists():
        applications_link.symlink_to("/Applications")

    run([
        "hdiutil", "create",
        "-volname", APP_NAME,
        "-srcfolder", str(staging),
        "-ov",
        "-format", "UDZO",
        str(DMG_PATH),
    ])
    shutil.rmtree(staging)
    print(f"\n.dmg built at: {DMG_PATH}")


if __name__ == "__main__":
    try:
        clean()
        install_dependencies()
        ensure_poppler_mac()
        prepare_poppler_mac()
        build_app()
        build_dmg()
        print(f"\n✓ Done! '{DMG_PATH}' is ready to distribute.")
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed: {e}")
        sys.exit(1)
