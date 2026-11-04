"""Small diagnostic/control client; secrets are read interactively, never from argv."""

import argparse
import getpass
import json
import sys
from pathlib import Path

from .client import ServiceError, call, write_audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "status",
            "apps",
            "presets",
            "audit",
            "set-password",
            "preview",
            "start",
            "cancel",
            "stop",
        ],
    )
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "audit":
            write_audit(sys.stdout)
            return
        if args.action == "set-password":
            status = call("status")
            current = getpass.getpass("Current password: ") if status["password_set"] else ""
            password = getpass.getpass("New password: ")
            if getpass.getpass("Confirm new password: ") != password:
                raise ServiceError("Passwords do not match.")
            result = call("set_password", current=current, new=password)
        elif args.action == "stop":
            token = call("authorize", password=getpass.getpass("Stop password: "))["token"]
            try:
                reason = input("Reason (leave empty to continue the session): ")
                result = (
                    call("stop", token=token, reason=reason)
                    if reason.strip()
                    else {"continued": True}
                )
            finally:
                call("revoke", token=token)
        elif args.action in ("preview", "start"):
            if not args.config:
                parser.error("--config is required for preview/start")
            config = json.loads(args.config.read_text())
            preview = call("preview", config=config)
            if args.action == "start":
                print(json.dumps(preview, indent=2))
                if input("Start this session? Type start: ") != "start":
                    return
                result = call("prepare", config=config)
            else:
                result = preview
        else:
            result = call(args.action)
        print(json.dumps(result, indent=2))
    except (ServiceError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
