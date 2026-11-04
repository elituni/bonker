# Repository preparation

`.gitignore` excludes virtual environments, caches, build outputs, signed XPIs,
archives, environment/credential files, key material, local databases, browser
profiles, handoffs, screenshots and host validation records. Excluded files remain
available locally. Source bundle creation applies the same exclusion rules.

Before a public push:

- Write the app description in `README.md`.
- Choose a software license. No license has been selected automatically.
- Confirm redistribution rights for the supplied third-party artwork, including
  `icon.webp`, `packaging/bonk.jpg`, the GIF and derived packaged assets. Provenance
  is recorded in [ICON.md](ICON.md) and [SESSION_ANIMATION.md](SESSION_ANIMATION.md).
- Review the commit author identity and the exact tracked files with
  `git log -1 --format=fuller` and `git ls-files`.
- Create the intended GitHub repository and add its remote when ready. No remote
  or push is required for local development.

A local pattern-based scan cannot prove that every possible secret is absent.
Recheck staged additions when introducing configuration, logs, screenshots,
release artifacts or external credentials. Do not publish the ignored local
handoff and evidence directories as release attachments.
