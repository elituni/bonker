#!/usr/bin/env python3
"""Bundle source and unsigned Firefox submission without local environment or Git data."""

import fnmatch
import subprocess
import sys
import zipfile
from pathlib import Path


def ignored(path, patterns):
    """Apply this repository's simple ordered gitignore rules to a relative path."""
    parts = path.parts
    excluded = False
    for rule in patterns:
        if not rule or rule.startswith("#"):
            continue
        include = rule.startswith("!")
        pattern = rule.lstrip("!")
        directory = pattern.endswith("/")
        pattern = pattern.rstrip("/")
        if "/" in pattern:
            matches = fnmatch.fnmatchcase(path.as_posix(), pattern)
            if directory:
                matches = path.as_posix().startswith(pattern + "/")
        else:
            matches = any(
                fnmatch.fnmatchcase(part, pattern) for part in (parts[:-1] if directory else parts)
            )
        if matches:
            excluded = not include
    return excluded


def public_source_files(root):
    patterns = [line.strip() for line in (root / ".gitignore").read_text().splitlines()]
    candidates = [
        root / name
        for name in (
            "README.md",
            "FEATURE_SPEC.md",
            "pyproject.toml",
            ".gitignore",
            ".gitattributes",
            "icon.webp",
        )
    ]
    for directory in ("focus_blocker", "firefox", "scripts", "packaging", "tests", "docs"):
        candidates.extend((root / directory).rglob("*"))
    return [
        path
        for path in candidates
        if path.is_file()
        and not path.is_symlink()
        and not ignored(path.relative_to(root), patterns)
    ]


def main():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            sys.executable,
            str(root / "scripts/build_extension.py"),
            "--output",
            str(root / "dist/focus-blocker-firefox-unsigned.zip"),
        ],
        check=True,
    )
    files = public_source_files(root)
    files.append(root / "dist/focus-blocker-firefox-unsigned.zip")
    output = root / "dist/bonker-0.3.1-source.zip"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            name = "bonker-0.3.1/" + str(path.relative_to(root))
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    print(output)


if __name__ == "__main__":
    main()
