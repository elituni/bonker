"""Unprivileged Firefox native-messaging bridge. No passwords or arbitrary methods."""

import json
import os
import struct
import sys
from pathlib import Path

from .client import ServiceError, call


def read_exact(stream, size):
    result = b""
    while len(result) < size:
        chunk = stream.read(size - len(result))
        if not chunk:
            raise EOFError()
        result += chunk
    return result


def serve(source, target, request=call):
    while True:
        try:
            length = struct.unpack("=I", read_exact(source, 4))[0]
            if length > 65536:
                return
            payload = json.loads(read_exact(source, length))
            reply = request(
                "browser",
                instance=payload.get("instance"),
                revision=payload.get("revision"),
                private=payload.get("private"),
                managed=payload.get("managed"),
            )
        except EOFError:
            return
        except (ValueError, AttributeError, ServiceError) as e:
            reply = {"error": str(e)}
        data = json.dumps(reply).encode()
        target.write(struct.pack("=I", len(data)) + data)
        target.flush()


def main():
    config = Path("/etc/focus-blocker/config.json")
    request = call
    try:
        st = config.stat()
        if (
            st.st_uid == 0
            and not st.st_mode & 0o022
            and json.loads(config.read_text())["uid"] != os.getuid()
        ):
            # Policy installation is system-wide, sessions are not. Other users'
            # extensions remain inert even while the configured user's service is down.
            def request(*_args, **_kwargs):
                return {
                    "session_id": None,
                    "revision": "idle",
                    "config": None,
                    "phase": "idle",
                    "remaining": 0,
                }
    except (OSError, ValueError, KeyError):
        pass
    serve(sys.stdin.buffer, sys.stdout.buffer, request)


if __name__ == "__main__":
    main()
