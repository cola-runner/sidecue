from __future__ import annotations

import plistlib
import fcntl
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path


APP_NAME = "Sidecue"
APP_BUNDLE_NAME = f"{APP_NAME}.app"
APP_IDENTIFIER = "local.sidecue"
BUNDLED_PACKAGES = [
    "objc",
    "PyObjCTools",
    "Foundation",
    "AppKit",
    "AVFoundation",
    "AVFAudio",
    "Speech",
    "CoreFoundation",
    "CoreAudio",
    "CoreMedia",
    "Quartz",
    "pypdf",
    "docx",
    "lxml",
    "typing_extensions.py",
]


def copy_public_resources(repo_root: Path, resources_dir: Path) -> None:
    package = resources_dir / "sidecue"
    package.mkdir(parents=True, exist_ok=True)
    for source in (repo_root / "sidecue").glob("*.py"):
        if not source.is_symlink():
            shutil.copy2(source, package / source.name)
    assets = package / "assets"
    assets.mkdir(exist_ok=True)
    for source in (repo_root / "sidecue" / "assets").iterdir():
        if source.is_file() and not source.is_symlink() and (source.suffix == ".png" or source.name == "LUCIDE-LICENSE"):
            shutil.copy2(source, assets / source.name)
    (resources_dir / "knowledge").mkdir(exist_ok=True)
    # Never copy a user's entire meeting directory or local config into an app.
    sample = repo_root / "knowledge" / "sample_notes.txt"
    config = repo_root / "config.toml"
    license_file = repo_root / "LICENSE"
    if any(path.is_symlink() for path in (sample, config, license_file)):
        raise ValueError("Public sample, config, and license must not be symlinks")
    shutil.copy2(sample, resources_dir / "knowledge" / sample.name)
    shutil.copy2(config, resources_dir / "config.toml")
    shutil.copy2(license_file, resources_dir / "LICENSE")


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    source_app = Path(sys.base_prefix) / "Resources" / "Python.app"
    if not source_app.exists():
        raise SystemExit(f"Python.app not found: {source_app}")

    build_dir = repo_root / "build"
    build_dir.mkdir(exist_ok=True)
    cache_dir = Path.home() / "Library" / "Caches" / APP_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / "app.lock"
    lock_handle = lock_path.open("a")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Sidecue is already running or being built. Close the existing window first.")
    app_path = build_dir / APP_BUNDLE_NAME
    if app_path.exists():
        shutil.rmtree(app_path)
    build_dir.mkdir(exist_ok=True)
    shutil.copytree(source_app, app_path)

    plist_path = app_path / "Contents" / "Info.plist"
    with plist_path.open("rb") as fh:
        plist = plistlib.load(fh)

    plist.update(
        {
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": "Sidecue",
            "CFBundleIdentifier": APP_IDENTIFIER,
            "CFBundleExecutable": APP_NAME,
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "1",
            "NSDocumentsFolderUsageDescription": "Sidecue needs access to this project folder under Documents to load meeting notes and Python dependencies.",
            "NSSpeechRecognitionUsageDescription": "Sidecue needs speech recognition to turn meeting audio into prompt context.",
            "NSMicrophoneUsageDescription": "Sidecue needs microphone access to listen to meeting audio.",
        }
    )
    with plist_path.open("wb") as fh:
        plistlib.dump(plist, fh)

    resources_dir = app_path / "Contents" / "Resources" / "sidecue"
    resources_dir.mkdir(parents=True, exist_ok=True)
    copy_public_resources(repo_root, resources_dir)

    launcher_path = app_path / "Contents" / "MacOS" / APP_NAME
    site_packages = repo_root / ".venv" / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    app_site_packages = app_path / "Contents" / "Resources" / "site-packages"
    app_site_packages.mkdir(parents=True, exist_ok=True)
    for name in BUNDLED_PACKAGES:
        source = site_packages / name
        if not source.exists():
            raise SystemExit(f"Missing dependency: {source}. Install requirements.txt first.")
        if source.is_dir():
            shutil.copytree(source, app_site_packages / name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, app_site_packages / name)
    with (app_path / "Contents" / "Resources" / "LaunchConfig.plist").open("wb") as fh:
        plistlib.dump({
            "PythonHome": sys.base_prefix,
            "CodexDirectory": str(Path(shutil.which("codex") or "/usr/bin/codex").parent),
            "LockPath": str(lock_path),
        }, fh)
    subprocess.run([
        "xcrun", "clang", "-fobjc-arc", "-O2",
        "-I", sysconfig.get_config_var("INCLUDEPY"),
        str(Path(sys.base_prefix) / "Python"),
        "-framework", "Foundation",
        str(repo_root / "scripts" / "macos_launcher.m"),
        "-o", str(launcher_path),
    ], check=True)
    (app_path / "Contents" / "MacOS" / "Python").unlink()

    subprocess.run(
        ["codesign", "--force", "--deep", "--sign", "-", str(app_path)],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app_path)], check=True)

    print(app_path)


if __name__ == "__main__":
    main()
