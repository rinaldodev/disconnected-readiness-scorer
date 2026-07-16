#!/usr/bin/env python3
"""Fetch repo_mappings.json from the software-catalog and update the local copy.

Source: opendatahub-io/software-catalog (private GitHub repo)
Path:   .claude/skills/software-catalog-query/references/repo_mappings.json

Requires a GitHub token with read access to the software-catalog repo,
passed via GITHUB_TOKEN environment variable.

Usage:
    python .github/scripts/update_repo_mappings.py
    GITHUB_TOKEN=ghp_... python .github/scripts/update_repo_mappings.py
"""

import json
import os
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

OWNER = "opendatahub-io"
REPO = "software-catalog"
FILE_PATH = ".claude/skills/software-catalog-query/references/repo_mappings.json"
API_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE_PATH}"

LOCAL_PATH = Path(__file__).resolve().parents[2] / ".github" / "config" / "repo_mappings.json"


def fetch(token: str) -> dict:
    req = urllib.request.Request(
        API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.raw+json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("error: GITHUB_TOKEN not set", file=sys.stderr)
        return 1

    try:
        data = fetch(token)
    except urllib.error.HTTPError as e:
        print(f"error: {e.code} fetching {API_URL}", file=sys.stderr)
        if e.code == 404:
            print("  The repo may be private — ensure GITHUB_TOKEN has access.", file=sys.stderr)
        return 1

    mappings = data.get("mappings", [])
    if not mappings:
        print("error: fetched data has no mappings", file=sys.stderr)
        return 1

    output = {
        "mappings": mappings,
        "lifecycle": {
            "last_updated": datetime.now(UTC).isoformat(),
            "source": f"https://github.com/{OWNER}/{REPO}",
        },
    }

    LOCAL_PATH.write_text(json.dumps(output, indent=2) + "\n")
    print(f"Updated {LOCAL_PATH} ({len(mappings)} mappings)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
