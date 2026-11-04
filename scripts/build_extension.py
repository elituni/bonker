#!/usr/bin/env python3
"""Produce a deterministic unsigned ZIP for Mozilla signing (not an installable signed XPI)."""

import argparse
import json
import zipfile
from pathlib import Path

# Explicit release inventory: local files never become assets just because they
# exist in the extension directory. Update deliberately when adding a real asset.
RELEASE_FILES = (
    "background.js",
    "blocked.html",
    "blocked.js",
    "bonk.jpg",
    "icons/icon-16.png",
    "icons/icon-32.png",
    "icons/icon-48.png",
    "icons/icon-96.png",
    "icons/icon-128.png",
    "manifest.json",
    "popup.html",
    "popup.js",
    "presentation.js",
    "privacy.txt",
    "rules.js",
    "style.css",
)


def build_extension(root, destination):
    root, destination = Path(root), Path(destination)
    payload = {}
    for name in sorted(RELEASE_FILES):
        path = root / name
        # Check directory components too: icons/ or the source root can link
        # outside the repository even when the file itself is not a symlink.
        if (
            root.is_symlink()
            or any((root / parent).is_symlink() for parent in path.relative_to(root).parents)
            or path.is_symlink()
        ):
            raise ValueError(f"Refusing symlink in extension release path: {name}")
        if not path.is_file():
            raise ValueError(f"Missing regular extension release file: {name}")
        payload[name] = path.read_bytes()
    json.loads(payload["manifest.json"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in payload.items():
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist/focus-blocker-firefox-unsigned.zip")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "firefox"
    destination = Path(args.output)
    build_extension(root, destination)
    print(destination.resolve())


if __name__ == "__main__":
    main()
