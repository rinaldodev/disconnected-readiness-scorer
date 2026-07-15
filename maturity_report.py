#!/usr/bin/env python3
"""Generate an ExternalBatchReport JSON for the component-maturity system.

Reads per-repo disconnected-readiness JSON reports (as produced by run_all.py),
maps repos to component-maturity catalog IDs via the software catalog's
repo_mappings.json, and emits a single JSON file conforming to the
ExternalBatchReport wire format.
"""

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

VERSION = "1.0.0"

GITHUB_BASE = "https://github.com"
REFERENCE_DOC_BASE = (
    "https://github.com/opendatahub-io/disconnected-readiness-scorer/blob/main/docs/references"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)


# ─── Rule definitions ──────────────────────────────────────────────

RULES = {
    "image-manifest-complete": {
        "id": "image-manifest-complete",
        "name": "Image manifest completeness",
        "severity": "blocker",
        "remediation": (
            "Register all container images used by this component in the "
            "operator manifest. For repos using the env-var pattern, add a "
            "RELATED_IMAGE_* environment variable to the operator deployment. "
            "For repos using the params.env pattern, add a key to params.env "
            "and wire it through kustomize."
        ),
        "reference_doc": f"{REFERENCE_DOC_BASE}/image-manifest-complete.md",
    },
    "no-image-tags": {
        "id": "no-image-tags",
        "name": "No mutable image tags",
        "severity": "blocker",
        "remediation": (
            "Replace all mutable image tags (:latest, :v1.2, etc.) with "
            "immutable @sha256: digest references. Mutable tags can resolve "
            "to different images over time and cannot be pre-pulled reliably "
            "for disconnected deployments."
        ),
        "reference_doc": f"{REFERENCE_DOC_BASE}/no-image-tags.md",
    },
    "no-runtime-egress": {
        "id": "no-runtime-egress",
        "name": "No runtime external calls",
        "severity": "blocker",
        "remediation": (
            "Remove hardcoded external URLs from source code and manifests, "
            "or make them configurable via environment variables or "
            "configuration files. In a disconnected cluster, any outbound "
            "network call to an external service will fail."
        ),
        "reference_doc": f"{REFERENCE_DOC_BASE}/no-runtime-egress.md",
    },
    "python-imports-bundled": {
        "id": "python-imports-bundled",
        "name": "Python imports bundled",
        "severity": "blocker",
        "remediation": (
            "Ensure all Python package dependencies are included in the "
            "offline-bundled package set or vendored into the container "
            "image at build time. Runtime pip install calls will fail "
            "without network access."
        ),
        "reference_doc": f"{REFERENCE_DOC_BASE}/python-imports-bundled.md",
    },
    "params-env-wiring": {
        "id": "params-env-wiring",
        "name": "Params.env wiring",
        "severity": "blocker",
        "remediation": (
            "Complete the params.env wiring chain: every image reference "
            "must flow from params.env through a kustomize configMapGenerator "
            "into the rendered manifests. Check for hardcoded images not "
            "sourced from params.env, unwired params.env keys, and orphan "
            "Go os.Getenv calls."
        ),
        "reference_doc": f"{REFERENCE_DOC_BASE}/params-env-wiring.md",
    },
}


# ─── Repo mappings ─────────────────────────────────────────────────

# When a repo appears at multiple tiers, prefer the tier closest to the
# productised artifact.  The scanner runs once per repo checkout so only
# one mapping per repo is meaningful.
_TIER_PRIORITY: dict[str, int] = {"downstream": 0, "midstream": 1, "upstream": 2}


def _default_repo_mappings_path() -> Path:
    return Path(__file__).parent / ".github" / "config" / "repo_mappings.json"


def load_repo_mappings(path: str) -> dict[str, tuple[str, str, str]]:
    """Load repo_mappings.json and build a repo → (catalog_id, name, tier) lookup.

    When a repo appears more than once (e.g. at different tiers), the entry
    closest to the productised artifact wins (downstream > midstream > upstream)
    and a warning is logged.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to load repo mappings from %s: %s", path, e)
        return {}

    mappings = data.get("mappings", [])
    lookup: dict[str, tuple[str, str, str]] = {}
    for entry in mappings:
        repo = entry.get("repo", "")
        jira_component = entry.get("jira_component", "")
        tier = entry.get("tier", "midstream")
        if repo and jira_component:
            catalog_id = jira_component.lower().replace(" ", "-")
            if repo in lookup:
                existing_tier = lookup[repo][2]
                existing_prio = _TIER_PRIORITY.get(existing_tier, 99)
                new_prio = _TIER_PRIORITY.get(tier, 99)
                kept = existing_tier if existing_prio <= new_prio else tier
                logger.warning(
                    "Duplicate repo mapping for %s (tiers: %s, %s — keeping %s)",
                    repo,
                    existing_tier,
                    tier,
                    kept,
                )
                if new_prio < existing_prio:
                    lookup[repo] = (catalog_id, jira_component, tier)
                continue
            lookup[repo] = (catalog_id, jira_component, tier)
    return lookup


# ─── Location construction ─────────────────────────────────────────


def _finding_location(repo_name: str, finding: dict) -> dict:
    """Build an evidence dict from a finding with file and line info."""
    fpath = finding.get("file", "")
    line = finding.get("line", 0)

    if not fpath:
        label = finding.get("message", "")[:120]
    elif line:
        label = f"{fpath}:{line}"
    else:
        label = fpath

    loc = {"label": label}

    if fpath:
        url = f"{GITHUB_BASE}/{repo_name}/blob/HEAD/{fpath}"
        if line:
            url += f"#L{line}"
        loc["url"] = url

    if line:
        loc["line"] = line

    return loc


# ─── Report building ──────────────────────────────────────────────


def _load_repo_reports(reports_dir: str) -> dict[str, dict]:
    """Load all per-repo JSON reports from a directory.

    Returns: dict of {repo_name: parsed_json}
    """
    reports = {}
    reports_path = Path(reports_dir)

    for json_file in sorted(reports_path.glob("*.json")):
        if json_file.name in ("summary.json",):
            continue
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping %s: %s", json_file.name, e)
            continue

        repo_name = data.get("repo", "")
        if not repo_name:
            logger.warning("Skipping %s: no 'repo' field", json_file.name)
            continue

        reports[repo_name] = data

    return reports


def _build_component_data(
    repo_reports: dict[str, dict],
    repo_lookup: dict[str, tuple[str, str, str]],
) -> dict:
    """Group repo reports by catalog_id.

    Returns: {catalog_id: {name, repos: [{repo_name, report_data}]}}
    """
    components = defaultdict(lambda: {"name": "", "repos": []})

    for repo_name, report_data in repo_reports.items():
        mapping = repo_lookup.get(repo_name)
        if not mapping:
            logger.warning("Repo %s not in repo mappings — skipping", repo_name)
            continue

        catalog_id, component_name, tier = mapping
        components[catalog_id]["name"] = component_name
        components[catalog_id]["repos"].append(
            {
                "repo_name": repo_name,
                "tier": tier,
                "report": report_data,
            }
        )

    return dict(components)


def _aggregate_evaluations(catalog_id: str, comp_data: dict, checked_at: str) -> list[dict]:
    """Build evaluation dicts for one component across all rules."""
    repo_entries = comp_data["repos"]

    # Pick the first repo (alphabetically) as the representative target
    first_entry = sorted(repo_entries, key=lambda e: e["repo_name"])[0]
    target = {
        "kind": "repository",
        "name": first_entry["repo_name"],
        "url": f"{GITHUB_BASE}/{first_entry['repo_name']}",
    }

    all_rules_seen: dict[str, list[dict]] = {}
    for entry in repo_entries:
        for rule in entry["report"].get("rules", []):
            rule_name = rule.get("name", "")
            if rule_name not in all_rules_seen:
                all_rules_seen[rule_name] = []
            all_rules_seen[rule_name].append(
                {
                    "repo_name": entry["repo_name"],
                    "rule_data": rule,
                }
            )

    evaluations = []
    for rule_id, rule_def in RULES.items():
        rule_entries = all_rules_seen.get(rule_id, [])
        if not rule_entries:
            continue

        all_passed = all(r["rule_data"].get("passed", True) for r in rule_entries)
        blocker_findings = []
        for r in rule_entries:
            for f in r["rule_data"].get("findings", []):
                if f.get("severity") == "blocker":
                    blocker_findings.append((r["repo_name"], f))

        if all_passed:
            status = "met"
            detail = f"All repos passed {rule_def['name'].lower()} checks"
        else:
            status = "unmet"
            count = len(blocker_findings)
            repos_failing = sorted(
                {r["repo_name"] for r in rule_entries if not r["rule_data"].get("passed", True)}
            )
            detail = (
                f"{count} blocker(s) in {', '.join(repos_failing)}"
                if repos_failing
                else f"{count} blocker(s) found"
            )

        evaluation = {
            "rule_id": rule_id,
            "component_id": catalog_id,
            "target": target,
            "status": status,
            "detail": detail,
            "checked_at": checked_at,
        }

        if blocker_findings:
            evidence = []
            for repo_name, finding in blocker_findings:
                loc = _finding_location(repo_name, finding)
                if loc:
                    evidence.append(loc)
            if evidence:
                evaluation["evidence"] = evidence

        evaluations.append(evaluation)

    return evaluations


def build_report(
    reports_dir: str, repo_mappings_path: str, run_url: str, version: str
) -> dict | None:
    """Build the full ExternalBatchReport dict."""
    repo_lookup = load_repo_mappings(repo_mappings_path)
    if not repo_lookup:
        logger.error("No repo mappings loaded")
        return None

    repo_reports = _load_repo_reports(reports_dir)
    if not repo_reports:
        logger.warning("No repo reports found in %s", reports_dir)

    now = datetime.now(UTC).isoformat()
    component_data = _build_component_data(repo_reports, repo_lookup)

    # Build top-level catalogs
    repos_catalog: dict[str, dict] = {}  # keyed by name
    components_catalog: list[dict] = []

    for catalog_id, comp_data in sorted(component_data.items()):
        repo_names = []
        for entry in comp_data["repos"]:
            name = entry["repo_name"]
            repo_names.append(name)
            if name not in repos_catalog:
                repos_catalog[name] = {
                    "name": name,
                    "url": f"{GITHUB_BASE}/{name}",
                }
        components_catalog.append({
            "id": catalog_id,
            "name": comp_data["name"],
            "repositories": sorted(set(repo_names)),
        })

    evaluations = []
    for catalog_id, comp_data in sorted(component_data.items()):
        evals = _aggregate_evaluations(catalog_id, comp_data, now)
        evaluations.extend(evals)

    source: dict = {
        "tool": "disconnected-readiness-scorer",
        "version": version,
    }
    if run_url:
        source["url"] = run_url

    return {
        "source": source,
        "scanned_at": now,
        "repositories": list(repos_catalog.values()),
        "images": [],
        "components": components_catalog,
        "rules": list(RULES.values()),
        "evaluations": evaluations,
    }


# ─── CLI ───────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate ExternalBatchReport JSON for component-maturity",
    )
    parser.add_argument(
        "reports_dir",
        help="Directory containing per-repo JSON reports from run_all.py",
    )
    parser.add_argument(
        "--output",
        default="disconnected-readiness-report.json",
        help="Output JSON path (default: disconnected-readiness-report.json)",
    )
    parser.add_argument(
        "--repo-mappings",
        default=str(_default_repo_mappings_path()),
        help="Path to repo_mappings.json (default: .github/config/repo_mappings.json)",
    )
    parser.add_argument(
        "--run-url",
        default="",
        help="GitHub Actions run URL",
    )
    parser.add_argument(
        "--version",
        default=VERSION,
        help=f"Tool version string (default: {VERSION})",
    )
    args = parser.parse_args()

    report = build_report(
        reports_dir=args.reports_dir,
        repo_mappings_path=args.repo_mappings,
        run_url=args.run_url,
        version=args.version,
    )

    if report is None:
        logger.error("Report generation failed")
        return 1

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    total = len(report["evaluations"])
    unmet = sum(1 for e in report["evaluations"] if e["status"] == "unmet")
    components = len({e["component_id"] for e in report["evaluations"]})
    rules_count = len(report.get("rules", []))
    logger.info("Maturity report: %s", args.output)
    logger.info(
        "  Rules: %d | Components: %d | Evaluations: %d | Unmet: %d",
        rules_count,
        components,
        total,
        unmet,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
