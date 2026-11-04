"""Reversible Firefox policy edits; unrelated administrator settings are preserved."""

import copy

from . import EXTENSION_ID
from .rules import Invalid


def merge_policy(document, install_url):
    updated = copy.deepcopy(document)
    policies = updated.setdefault("policies", {})
    extensions = policies.setdefault("ExtensionSettings", {})
    if EXTENSION_ID in extensions:
        raise Invalid(
            "Bonker's policy already exists. Uninstall the old installation before replacing it."
        )
    addition = {
        "installation_mode": "force_installed",
        "install_url": install_url,
        "private_browsing": True,
    }
    originals = {
        k: {"present": k in policies, "value": policies.get(k)}
        for k in ("DisableSafeMode", "DisableProfileRefresh")
    }
    extensions[EXTENSION_ID] = addition
    managed = (
        policies.setdefault("3rdparty", {})
        .setdefault("Extensions", {})
        .setdefault(EXTENSION_ID, {})
    )
    managed_original = {
        "present": "localBridgeConsent" in managed,
        "value": managed.get("localBridgeConsent"),
    }
    managed["localBridgeConsent"] = True
    policies["DisableSafeMode"] = True
    policies["DisableProfileRefresh"] = True
    return updated, {
        "extension": addition,
        "originals": originals,
        "managed_original": managed_original,
    }


def remove_policy(document, receipt):
    updated = copy.deepcopy(document)
    policies = updated.get("policies", {})
    extensions = policies.get("ExtensionSettings", {})
    if extensions.get(EXTENSION_ID) == receipt["extension"]:
        extensions.pop(EXTENSION_ID)
    elif EXTENSION_ID in extensions:
        raise Invalid(
            "The blocker extension policy was changed externally; review it before uninstalling."
        )
    if not extensions:
        policies.pop("ExtensionSettings", None)
    if "managed_original" in receipt:
        thirdparty = policies.get("3rdparty", {})
        managed_extensions = thirdparty.get("Extensions", {})
        managed = managed_extensions.get(EXTENSION_ID, {})
        original = receipt["managed_original"]
        if managed.get("localBridgeConsent") is True:
            if original["present"]:
                managed["localBridgeConsent"] = original["value"]
            else:
                managed.pop("localBridgeConsent", None)
        if not managed:
            managed_extensions.pop(EXTENSION_ID, None)
        if not managed_extensions:
            thirdparty.pop("Extensions", None)
        if not thirdparty:
            policies.pop("3rdparty", None)
    for key, original in receipt["originals"].items():
        if policies.get(key) is True:
            if original["present"]:
                policies[key] = original["value"]
            else:
                policies.pop(key, None)
    return updated
