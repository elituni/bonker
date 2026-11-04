"""Bounded, static launcher inspection. Never evaluates or executes launcher code."""

import os
import re
import shlex
import shutil
import stat
from pathlib import Path

# Distro launchers whose runtime executables differ even without a surviving
# parent or a matching GNOME scope. Exact paths, never basename/directory grants.
PACKAGE_EXECUTABLES = {
    "/usr/bin/gnome-terminal": ("/usr/libexec/gnome-terminal-server",),
    "/usr/bin/libreoffice": tuple(
        f"/usr/{lib}/libreoffice/program/{binary}"
        for lib in ("lib", "lib64")
        for binary in ("soffice.bin", "oosplash")
    ),
    "/usr/lib/pentablet/PenTablet.sh": ("/usr/lib/pentablet/PenTablet",),
}
INTERPRETERS = re.compile(
    r"(?:python(?:\d+(?:\.\d+)*)?|sh|bash|dash|zsh|fish|gjs(?:-console)?|node|perl|ruby)"
)


def resolve_program(program):
    resolved = shutil.which(program, path="/usr/local/bin:/usr/bin:/bin") or program
    return os.path.realpath(resolved) if resolved.startswith("/") else ""


def launcher_text(path):
    """Do not block on FIFO/device launchers or read unbounded user files as root."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return ""
            data = os.read(fd, 256)
            if not data.startswith(b"#!"):
                return ""
            return (data + os.read(fd, 16384 - len(data))).decode("utf-8", errors="replace")
        finally:
            os.close(fd)
    except OSError:
        return ""


def forwarded_program(path, text):
    """Understand only a single literal exec, or a precise sibling-exec idiom."""
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(lines) != 1:
        return None
    line = lines[0]
    for prefix in ('exec "$(dirname "$(readlink -f "$0")")/', 'exec "$(dirname "$0")/'):
        suffix = '" "$@"'
        if line.startswith(prefix) and line.endswith(suffix):
            name = line[len(prefix) : -len(suffix)]
            if re.fullmatch(r"[A-Za-z0-9_.+-]+", name) and name not in (".", ".."):
                return str(Path(path).parent / name)
    try:
        args = shlex.split(line)
    except ValueError:
        return None
    if len(args) == 3 and args[0] == "exec" and args[2] == "$@":
        target = args[1]
        if (
            target.startswith("/")
            and not any(c in target for c in "$`\\;|&<>*?()[]{}")
            and all(ord(c) >= 32 for c in target)
        ):
            return target
    return None


def runtime_identities(executable):
    """Return exact (runtime executable, required script) pairs for a launcher."""
    result = set()
    visited = set()

    def inspect(path, depth):
        path = os.path.realpath(path)
        if path in visited or depth > 3:
            return
        visited.add(path)
        for target in PACKAGE_EXECUTABLES.get(path, ()):
            if os.path.isfile(target):
                result.add((os.path.realpath(target), ""))
        text = launcher_text(path)
        if not text:
            return
        try:
            command = shlex.split(text.splitlines()[0][2:])
        except ValueError:
            return
        if command and Path(command[0]).name == "env":
            command = command[1:]
            if command and command[0] == "-S":
                command = command[1:]
        if not command or not INTERPRETERS.fullmatch(Path(command[0]).name):
            return
        interpreter = resolve_program(command[0])
        if interpreter:
            result.add((interpreter, path))
        if Path(command[0]).name in ("sh", "bash", "dash"):
            target = forwarded_program(path, text)
            if target and os.path.isfile(target):
                target = os.path.realpath(target)
                result.add((target, ""))
                inspect(target, depth + 1)

    if executable:
        inspect(executable, 0)
    return tuple(sorted(result))


def script_argument_matches(script, args):
    # Script identity must be an actual interpreter argument, not a substring
    # in code passed to -c or a filename supplied to a different script.
    for arg in args[1:]:
        if arg in ("-c", "-m", "-e", "--eval"):
            return False
        if arg == "--" or arg.startswith("-"):
            continue
        return arg.startswith("/") and os.path.realpath(arg) == script
    return False
