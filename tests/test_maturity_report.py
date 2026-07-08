"""Unit tests for maturity_report.py."""

import json

from maturity_report import (
    RULES,
    _finding_location,
    build_report,
    load_repo_mappings,
)

# ─── Helpers ────────────────────────────────────────────────────────

def _write_repo_mappings(path, mappings):
    """Write a minimal repo_mappings.json."""
    path.write_text(json.dumps({"mappings": mappings}, indent=2))


def _write_repo_report(reports_dir, repo_name, rules, score="READY"):
    """Write a per-repo JSON report matching run_all.py output format."""
    filename = repo_name.rsplit("/", 1)[-1] + ".json"
    data = {
        "repo": repo_name,
        "date": "2026-07-07",
        "score": score,
        "rules": rules,
    }
    (reports_dir / filename).write_text(json.dumps(data, indent=2))


PASSING_RULES = [
    {"name": "image-manifest-complete", "passed": True, "blockers": 0, "infos": 0, "findings": []},
    {"name": "no-image-tags", "passed": True, "blockers": 0, "infos": 0, "findings": []},
    {"name": "no-runtime-egress", "passed": True, "blockers": 0, "infos": 0, "findings": []},
    {"name": "python-imports-bundled", "passed": True, "blockers": 0, "infos": 0, "findings": []},
    {"name": "params-env-wiring", "passed": True, "blockers": 0, "infos": 0, "findings": []},
]

FAILING_RULES = [
    {
        "name": "no-image-tags",
        "passed": False,
        "blockers": 1,
        "infos": 0,
        "findings": [
            {
                "severity": "blocker",
                "file": "config/manager/kustomization.yaml",
                "line": 12,
                "image": "quay.io/example/foo:latest",
                "message": "Mutable tag :latest on image quay.io/example/foo:latest",
            },
        ],
    },
    {"name": "image-manifest-complete", "passed": True, "blockers": 0, "infos": 0, "findings": []},
    {"name": "no-runtime-egress", "passed": True, "blockers": 0, "infos": 0, "findings": []},
    {"name": "python-imports-bundled", "passed": True, "blockers": 0, "infos": 0, "findings": []},
    {"name": "params-env-wiring", "passed": True, "blockers": 0, "infos": 0, "findings": []},
]


# ─── 1. Rule definitions ───────────────────────────────────────────

class TestRuleDefinitions:

    REQUIRED_FIELDS = {"id", "name", "category", "stage", "severity", "owner", "remediation", "reference_doc"}

    def test_all_rules_have_required_fields(self):
        for rule_id, rule_def in RULES.items():
            missing = self.REQUIRED_FIELDS - set(rule_def.keys())
            assert not missing, f"Rule {rule_id} missing fields: {missing}"

    def test_rule_ids_match_keys(self):
        for rule_id, rule_def in RULES.items():
            assert rule_def["id"] == rule_id

    def test_reference_doc_urls_are_absolute(self):
        for rule_id, rule_def in RULES.items():
            url = rule_def["reference_doc"]
            assert url.startswith("https://"), f"Rule {rule_id} reference_doc not absolute: {url}"
            assert rule_id in url, f"Rule {rule_id} reference_doc doesn't contain rule id"

    def test_five_rules_defined(self):
        assert len(RULES) == 5


# ─── 2. Repo mappings ──────────────────────────────────────────────

class TestRepoMappings:

    def test_load_valid_mappings(self, tmp_path):
        path = tmp_path / "repo_mappings.json"
        _write_repo_mappings(path, [
            {"repo": "opendatahub-io/odh-dashboard", "jira_component": "AI Core Dashboard", "tier": "midstream"},
        ])

        lookup = load_repo_mappings(str(path))
        assert "opendatahub-io/odh-dashboard" in lookup
        catalog_id, name, tier = lookup["opendatahub-io/odh-dashboard"]
        assert catalog_id == "ai-core-dashboard"
        assert name == "AI Core Dashboard"
        assert tier == "midstream"

    def test_catalog_id_derivation(self, tmp_path):
        path = tmp_path / "repo_mappings.json"
        _write_repo_mappings(path, [
            {"repo": "org/repo", "jira_component": "Model as a Service", "tier": "midstream"},
        ])

        lookup = load_repo_mappings(str(path))
        assert lookup["org/repo"][0] == "model-as-a-service"

    def test_missing_file_returns_empty(self, tmp_path):
        lookup = load_repo_mappings(str(tmp_path / "nonexistent.json"))
        assert lookup == {}

    def test_invalid_json_returns_empty(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json")
        lookup = load_repo_mappings(str(path))
        assert lookup == {}


# ─── 3. Finding locations ──────────────────────────────────────────

class TestFindingLocation:

    def test_file_and_line(self):
        loc = _finding_location("org/repo", {"file": "main.go", "line": 42, "message": "test"})
        assert loc["label"] == "main.go:42"
        assert loc["url"] == "https://github.com/org/repo/blob/HEAD/main.go#L42"

    def test_file_without_line(self):
        loc = _finding_location("org/repo", {"file": "main.go", "line": 0, "message": "test"})
        assert loc["label"] == "main.go"
        assert loc["url"] == "https://github.com/org/repo/blob/HEAD/main.go"

    def test_no_file_uses_message(self):
        loc = _finding_location("org/repo", {"file": "", "line": 0, "message": "Something went wrong"})
        assert loc["label"] == "Something went wrong"
        assert "url" not in loc

    def test_long_message_truncated(self):
        msg = "x" * 200
        loc = _finding_location("org/repo", {"file": "", "line": 0, "message": msg})
        assert len(loc["label"]) == 120


# ─── 4. Status mapping ─────────────────────────────────────────────

class TestStatusMapping:

    def test_passing_repo_yields_met(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/passing-repo", PASSING_RULES)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(mappings_path, [
            {"repo": "org/passing-repo", "jira_component": "Test Component", "tier": "midstream"},
        ])

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")
        assert report is not None

        for ev in report["evaluations"]:
            assert ev["status"] == "met"

    def test_failing_repo_yields_unmet(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/failing-repo", FAILING_RULES, score="NOT READY")

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(mappings_path, [
            {"repo": "org/failing-repo", "jira_component": "Test Component", "tier": "midstream"},
        ])

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")

        tags_eval = next(
            e for e in report["evaluations"]
            if e["rule"]["id"] == "no-image-tags"
        )
        assert tags_eval["status"] == "unmet"
        assert "blocker" in tags_eval["detail"]

        other_evals = [
            e for e in report["evaluations"]
            if e["rule"]["id"] != "no-image-tags"
        ]
        for ev in other_evals:
            assert ev["status"] == "met"


# ─── 5. Component matching ─────────────────────────────────────────

class TestComponentMatching:

    def test_unmapped_repo_skipped(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/unknown-repo", PASSING_RULES)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(mappings_path, [
            {"repo": "org/other-repo", "jira_component": "Other", "tier": "midstream"},
        ])

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")
        assert report is not None
        assert report["evaluations"] == []


# ─── 6. Catalog ID merging ─────────────────────────────────────────

class TestCatalogIdMerging:

    def test_two_repos_same_catalog_id_merged(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/repo-a", PASSING_RULES)
        _write_repo_report(reports_dir, "org/repo-b", FAILING_RULES, score="NOT READY")

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(mappings_path, [
            {"repo": "org/repo-a", "jira_component": "Shared Component", "tier": "midstream"},
            {"repo": "org/repo-b", "jira_component": "Shared Component", "tier": "midstream"},
        ])

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")

        shared_evals = [
            e for e in report["evaluations"]
            if e["component"]["id"] == "shared-component"
        ]
        assert len(shared_evals) == 5

        repos_in_component = {
            r["name"] for r in shared_evals[0]["component"]["repositories"]
        }
        assert repos_in_component == {"org/repo-a", "org/repo-b"}

        tags_eval = next(
            e for e in shared_evals if e["rule"]["id"] == "no-image-tags"
        )
        assert tags_eval["status"] == "unmet"


# ─── 7. Locations ──────────────────────────────────────────────────

class TestLocations:

    def test_unmet_has_locations(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/repo", FAILING_RULES, score="NOT READY")

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(mappings_path, [
            {"repo": "org/repo", "jira_component": "Test", "tier": "midstream"},
        ])

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")

        tags_eval = next(
            e for e in report["evaluations"]
            if e["rule"]["id"] == "no-image-tags"
        )
        assert "locations" in tags_eval
        assert len(tags_eval["locations"]) == 1

        loc = tags_eval["locations"][0]
        assert "config/manager/kustomization.yaml:12" in loc["label"]
        assert "github.com/org/repo" in loc["url"]
        assert "#L12" in loc["url"]

    def test_met_has_no_locations(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/repo", PASSING_RULES)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(mappings_path, [
            {"repo": "org/repo", "jira_component": "Test", "tier": "midstream"},
        ])

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")

        for ev in report["evaluations"]:
            assert "locations" not in ev

    def test_info_findings_excluded_from_locations(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()

        rules_with_info = [
            {
                "name": "no-image-tags",
                "passed": True,
                "blockers": 0,
                "infos": 1,
                "findings": [
                    {
                        "severity": "info",
                        "file": "test/data.yaml",
                        "line": 5,
                        "image": "",
                        "message": "Info finding in test dir",
                    },
                ],
            },
        ]
        _write_repo_report(reports_dir, "org/repo", rules_with_info)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(mappings_path, [
            {"repo": "org/repo", "jira_component": "Test", "tier": "midstream"},
        ])

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")

        for ev in report["evaluations"]:
            assert "locations" not in ev


# ─── 8. Full report structure ──────────────────────────────────────

class TestBuildReport:

    def test_basic_structure(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/repo", PASSING_RULES)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(mappings_path, [
            {"repo": "org/repo", "jira_component": "Test Component", "tier": "midstream"},
        ])

        report = build_report(
            str(reports_dir), str(mappings_path),
            "https://github.com/example/run/1", "1.0.0-test",
        )

        assert report["source"]["tool"] == "disconnected-readiness-scorer"
        assert report["source"]["version"] == "1.0.0-test"
        assert report["source"]["url"] == "https://github.com/example/run/1"
        assert "scanned_at" in report
        assert len(report["evaluations"]) > 0

    def test_no_run_url_omits_url_field(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(mappings_path, [
            {"repo": "org/repo", "jira_component": "Test", "tier": "midstream"},
        ])

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")
        assert "url" not in report["source"]


# ─── 9. Empty input ────────────────────────────────────────────────

class TestEmptyInput:

    def test_no_reports_yields_empty_evaluations(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(mappings_path, [
            {"repo": "org/repo", "jira_component": "Test", "tier": "midstream"},
        ])

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")
        assert report is not None
        assert report["evaluations"] == []

    def test_summary_json_is_skipped(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "summary.json").write_text(json.dumps([{"repo": "org/repo", "score": "READY"}]))

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(mappings_path, [
            {"repo": "org/repo", "jira_component": "Test", "tier": "midstream"},
        ])

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")
        assert report["evaluations"] == []
