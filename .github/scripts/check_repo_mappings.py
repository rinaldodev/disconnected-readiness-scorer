#!/usr/bin/env python3
"""Check if the committed repo_mappings.json is up to date with software-catalog.

Compares the local mapping count against the upstream version. Exits 0 if
up to date or if the check can't run (no token, network error, private repo
inaccessible). Only exits non-zero if the file is confirmed stale.

Requires GITHUB_TOKEN with read access to opendatahub-io/software-catalog.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

OWNER = "opendatahub-io"
REPO = "software-catalog"
FILE_PATH = ".claude/skills/software-catalog-query/references/repo_mappings.json"
API_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE_PATH}"

LOCAL_PATH = Path(__file__).resolve().parents[2] / ".github" / "config" / "repo_mappings.json"


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        return 0

    try:
        local = json.loads(LOCAL_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return 0

    try:
        req = urllib.request.Request(
            API_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.raw+json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            upstream = json.loads(resp.read())
    except Exception:
        return 0

    local_mappings = {json.dumps(m, sort_keys=True) for m in local.get("mappings", [])}
    upstream_mappings = {json.dumps(m, sort_keys=True) for m in upstream.get("mappings", [])}

    if local_mappings == upstream_mappings:
        return 0

    added = len(upstream_mappings - local_mappings)
    removed = len(local_mappings - upstream_mappings)
    parts = []
    if added:
        parts.append(f"{added} added")
    if removed:
        parts.append(f"{removed} removed")
    print(
        f"repo_mappings.json is out of date ({', '.join(parts)}). Run: make update-repo-mappings",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
