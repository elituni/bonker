import json
import os
import socket
import tempfile
from pathlib import Path

from . import SOCKET


class ServiceError(Exception):
    pass


def call(action, socket_path=SOCKET, **args):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(8)
            sock.connect(socket_path)
            sock.sendall(json.dumps({"action": action, **args}).encode() + b"\n")
            with sock.makefile("rb") as stream:
                line = stream.readline(4 * 1024 * 1024)
            reply = json.loads(line)
    except (OSError, ValueError) as e:
        raise ServiceError(
            "Cannot reach Bonker's service. Check installation and service status."
        ) from e
    if not reply.get("ok"):
        raise ServiceError(reply.get("error", "Service request failed."))
    return reply.get("result")


def write_audit(stream, fetch=call):
    """Stream a consistent snapshot in bounded pages, never loading the whole log."""
    before = through = None
    first = True
    stream.write("[\n")
    while True:
        page = fetch("audit", before=before, through=through)
        through = page["through"]
        for record in page["records"]:
            stream.write(("" if first else ",\n") + json.dumps(record, ensure_ascii=False))
            first = False
        before = page["next_before"]
        if before is None:
            break
    stream.write("\n]\n")


def export_audit(path, fetch=call):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".focus-blocker-export-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            write_audit(stream, fetch)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
