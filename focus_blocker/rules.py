"""Canonical host rules, also implemented in firefox/rules.js."""

import ipaddress
import re
from urllib.parse import unquote, urlsplit

import idna


class Invalid(ValueError):
    pass


def browser_ipv4(host):
    """WHATWG numeric-host parsing; ordinary domain names return unchanged."""
    parts = host.split(".")
    if not (re.fullmatch(r"[0-9]+", parts[-1]) or re.fullmatch(r"0x[0-9a-f]*", parts[-1])):
        return host
    if len(parts) > 4:
        raise ValueError()
    values = []
    for part in parts:
        base = 10
        if part.startswith("0x"):
            part, base = part[2:], 16
        elif len(part) > 1 and part.startswith("0"):
            part, base = part[1:], 8
        elif not part:
            raise ValueError()
        digits = {10: r"[0-9]*", 8: r"[0-7]*", 16: r"[0-9a-f]*"}[base]
        if not re.fullmatch(digits, part):
            raise ValueError()
        values.append(int(part or "0", base))
    if any(n > 255 for n in values[:-1]) or values[-1] >= 256 ** (5 - len(values)):
        raise ValueError()
    number = values[-1] + sum(n * 256 ** (3 - i) for i, n in enumerate(values[:-1]))
    return str(ipaddress.IPv4Address(number))


def hostname(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise Invalid("Enter a hostname or HTTP(S) address.")
    value = value.strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    if any(c.isspace() for c in value) or "\\" in value or "*" in value:
        raise Invalid("Use a hostname without spaces, wildcards, or backslashes.")
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if parsed.scheme not in ("http", "https") or parsed.username is not None:
            raise ValueError()
        host = parsed.hostname
        _ = parsed.port
        if not host:
            raise ValueError()
        host = unquote(host, errors="strict").removesuffix(".").lower()
        try:
            return str(ipaddress.ip_address(host))
        except ValueError:
            pass
        host = idna.encode(host, uts46=True, std3_rules=True).decode("ascii")
        if len(host) > 253 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", p) for p in host.split(".")
        ):
            raise ValueError()
        return browser_ipv4(host)
    except (ValueError, UnicodeError):
        raise Invalid("That address is not a valid HTTP(S) hostname.") from None


def normalize_rules(items):
    if not isinstance(items, list) or len(items) > 500:
        raise Invalid("Use at most 500 website rules.")
    result = {}
    for item in items:
        if not isinstance(item, dict):
            raise Invalid("Invalid website rule.")
        host = hostname(item.get("host"))
        action = item.get("action")
        subdomains = item.get("subdomains", True)
        if action not in ("allow", "block") or type(subdomains) is not bool:
            raise Invalid("A rule needs an allow/block action and a subdomain option.")
        key = (host, subdomains)
        if key in result and result[key]["action"] != action:
            raise Invalid(f"Contradictory rules for {host}.")
        result[key] = {"host": host, "action": action, "subdomains": subdomains}
    return sorted(result.values(), key=lambda r: (r["host"], r["subdomains"]))


def evaluate(address, mode, rules):
    host = hostname(address)
    matches = [
        r
        for r in rules
        if host == r["host"] or (r["subdomains"] and host.endswith("." + r["host"]))
    ]
    winner = max(
        matches, key=lambda r: (len(r["host"].split(".")), not r["subdomains"]), default=None
    )
    return {
        "host": host,
        "allowed": (winner["action"] == "allow") if winner else mode == "blacklist",
        "rule": winner,
    }


def configuration(raw):
    if not isinstance(raw, dict):
        raise Invalid("Invalid session configuration.")
    minutes = raw.get("minutes", 25)
    if type(minutes) is not int or not 1 <= minutes <= 360:
        raise Invalid("Duration must be a whole number of minutes from 1 to 360 (6 hours).")
    mode = raw.get("mode", "blacklist")
    if mode not in ("blacklist", "whitelist"):
        raise Invalid("Choose whitelist or blacklist mode.")
    apps = raw.get("apps", [])
    if (
        not isinstance(apps, list)
        or len(apps) > 500
        or any(not isinstance(a, str) or not a or len(a) > 512 for a in apps)
    ):
        raise Invalid("Invalid application selection.")
    rules = normalize_rules(raw.get("rules", []))
    name = raw.get("preset_name")
    if name is not None and (not isinstance(name, str) or len(name) > 100):
        raise Invalid("Preset names must be at most 100 characters.")
    animation = raw.get("show_animation", True)
    if type(animation) is not bool:
        raise Invalid("Choose whether to show the session animation.")
    return {
        "minutes": minutes,
        "mode": mode,
        "rules": rules,
        "apps": sorted(set(apps)),
        "preset_name": name,
        "show_animation": animation,
    }


def websites_enabled(config):
    return config["mode"] == "whitelist" or any(r["action"] == "block" for r in config["rules"])
