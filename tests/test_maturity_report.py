"""Unit tests for maturity_report.py."""

import json

from maturity_report import (
    RULES,
    _extract_exception_reasons,
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
    REQUIRED_FIELDS = {
        "id",
        "name",
        "severity",
        "remediation",
        "reference_doc",
    }

    # Fields that belong to internal maturity system and must not appear
    FORBIDDEN_FIELDS = {"category", "stage", "scope", "owner", "depends_on"}

    def test_all_rules_have_required_fields(self):
        for rule_id, rule_def in RULES.items():
            missing = self.REQUIRED_FIELDS - set(rule_def.keys())
            assert not missing, f"Rule {rule_id} missing fields: {missing}"

    def test_no_internal_fields_present(self):
        for rule_id, rule_def in RULES.items():
            leaked = self.FORBIDDEN_FIELDS & set(rule_def.keys())
            assert not leaked, f"Rule {rule_id} contains internal fields: {leaked}"

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
        _write_repo_mappings(
            path,
            [
                {
                    "repo": "opendatahub-io/odh-dashboard",
                    "jira_component": "AI Core Dashboard",
                    "tier": "midstream",
                },
            ],
        )

        lookup = load_repo_mappings(str(path))
        assert "opendatahub-io/odh-dashboard" in lookup
        catalog_id, name, tier = lookup["opendatahub-io/odh-dashboard"]
        assert catalog_id == "ai-core-dashboard"
        assert name == "AI Core Dashboard"
        assert tier == "midstream"

    def test_catalog_id_derivation(self, tmp_path):
        path = tmp_path / "repo_mappings.json"
        _write_repo_mappings(
            path,
            [
                {"repo": "org/repo", "jira_component": "Model as a Service", "tier": "midstream"},
            ],
        )

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

    def test_duplicate_repo_prefers_midstream_over_upstream(self, tmp_path):
        """When the same repo appears at midstream and upstream, midstream wins."""
        path = tmp_path / "repo_mappings.json"
        _write_repo_mappings(
            path,
            [
                {"repo": "org/repo", "jira_component": "Comp", "tier": "midstream"},
                {"repo": "org/repo", "jira_component": "Comp", "tier": "upstream"},
            ],
        )

        lookup = load_repo_mappings(str(path))
        assert lookup["org/repo"][2] == "midstream"

    def test_duplicate_repo_prefers_higher_priority_tier(self, tmp_path):
        """When upstream appears first but midstream second, midstream still wins."""
        path = tmp_path / "repo_mappings.json"
        _write_repo_mappings(
            path,
            [
                {"repo": "org/repo", "jira_component": "Comp", "tier": "upstream"},
                {"repo": "org/repo", "jira_component": "Comp", "tier": "midstream"},
            ],
        )

        lookup = load_repo_mappings(str(path))
        assert lookup["org/repo"][2] == "midstream"

    def test_duplicate_repo_prefers_downstream(self, tmp_path):
        """Downstream beats midstream and upstream."""
        path = tmp_path / "repo_mappings.json"
        _write_repo_mappings(
            path,
            [
                {"repo": "org/repo", "jira_component": "Comp", "tier": "midstream"},
                {"repo": "org/repo", "jira_component": "Comp", "tier": "downstream"},
            ],
        )

        lookup = load_repo_mappings(str(path))
        assert lookup["org/repo"][2] == "downstream"


# ─── 3. Finding locations ──────────────────────────────────────────


class TestFindingLocation:
    def test_file_and_line(self):
        loc = _finding_location("org/repo", {"file": "main.go", "line": 42, "message": "test"})
        assert loc["label"] == "main.go:42"
        assert loc["url"] == "https://github.com/org/repo/blob/HEAD/main.go#L42"
        assert loc["line"] == 42

    def test_file_without_line(self):
        loc = _finding_location("org/repo", {"file": "main.go", "line": 0, "message": "test"})
        assert loc["label"] == "main.go"
        assert loc["url"] == "https://github.com/org/repo/blob/HEAD/main.go"
        assert "line" not in loc

    def test_no_file_returns_none(self):
        loc = _finding_location(
            "org/repo", {"file": "", "line": 0, "message": "Something went wrong"}
        )
        assert loc is None

    def test_git_sha_in_url(self):
        loc = _finding_location("org/repo", {"file": "main.go", "line": 10}, git_sha="abc123")
        assert "/blob/abc123/" in loc["url"]


# ─── 4. Status mapping ─────────────────────────────────────────────


class TestStatusMapping:
    def test_passing_repo_yields_met(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/passing-repo", PASSING_RULES)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [
                {
                    "repo": "org/passing-repo",
                    "jira_component": "Test Component",
                    "tier": "midstream",
                },
            ],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")
        assert report is not None

        for ev in report["evaluations"]:
            assert ev["status"] == "met"

    def test_failing_repo_yields_unmet(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/failing-repo", FAILING_RULES, score="NOT READY")

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [
                {
                    "repo": "org/failing-repo",
                    "jira_component": "Test Component",
                    "tier": "midstream",
                },
            ],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")

        tags_eval = next(e for e in report["evaluations"] if e["rule_id"] == "no-image-tags")
        assert tags_eval["status"] == "unmet"
        assert "blocker" in tags_eval["detail"]

        other_evals = [e for e in report["evaluations"] if e["rule_id"] != "no-image-tags"]
        for ev in other_evals:
            assert ev["status"] == "met"


# ─── 5. Component matching ─────────────────────────────────────────


class TestComponentMatching:
    def test_unmapped_repo_skipped(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/unknown-repo", PASSING_RULES)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [
                {"repo": "org/other-repo", "jira_component": "Other", "tier": "midstream"},
            ],
        )

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
        _write_repo_mappings(
            mappings_path,
            [
                {"repo": "org/repo-a", "jira_component": "Shared Component", "tier": "midstream"},
                {"repo": "org/repo-b", "jira_component": "Shared Component", "tier": "midstream"},
            ],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")

        shared_evals = [e for e in report["evaluations"] if e["component_id"] == "shared-component"]
        assert len(shared_evals) == 5

        # Component catalog should list both repos
        comp = next(c for c in report["components"] if c["id"] == "shared-component")
        assert set(comp["repositories"]) == {"org/repo-a", "org/repo-b"}

        tags_eval = next(e for e in shared_evals if e["rule_id"] == "no-image-tags")
        assert tags_eval["status"] == "unmet"


# ─── 7. Locations ──────────────────────────────────────────────────


class TestEvidence:
    def test_unmet_has_evidence(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/repo", FAILING_RULES, score="NOT READY")

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [{"repo": "org/repo", "jira_component": "Test", "tier": "midstream"}],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")

        tags_eval = next(e for e in report["evaluations"] if e["rule_id"] == "no-image-tags")
        assert "evidence" in tags_eval
        assert len(tags_eval["evidence"]) == 1

        loc = tags_eval["evidence"][0]
        assert "config/manager/kustomization.yaml:12" in loc["label"]
        assert "github.com/org/repo" in loc["url"]
        assert "#L12" in loc["url"]
        assert loc["line"] == 12

    def test_met_has_no_evidence(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/repo", PASSING_RULES)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [{"repo": "org/repo", "jira_component": "Test", "tier": "midstream"}],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")

        for ev in report["evaluations"]:
            assert "evidence" not in ev

    def test_info_findings_excluded_from_evidence(self, tmp_path):
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
        _write_repo_mappings(
            mappings_path,
            [
                {"repo": "org/repo", "jira_component": "Test", "tier": "midstream"},
            ],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")

        for ev in report["evaluations"]:
            assert "evidence" not in ev


# ─── 8. Full report structure ──────────────────────────────────────


class TestBuildReport:
    def test_basic_structure(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/repo", PASSING_RULES)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [{"repo": "org/repo", "jira_component": "Test Component", "tier": "midstream"}],
        )

        report = build_report(
            str(reports_dir),
            str(mappings_path),
            "https://github.com/example/run/1",
            "1.0.0-test",
        )

        assert report["source"]["tool"] == "disconnected-readiness-scorer"
        assert report["source"]["version"] == "1.0.0-test"
        assert report["source"]["url"] == "https://github.com/example/run/1"
        assert "scanned_at" in report
        assert len(report["evaluations"]) > 0

        # Top-level catalogs
        assert isinstance(report["repositories"], list)
        assert len(report["repositories"]) == 1
        assert report["repositories"][0]["name"] == "org/repo"
        assert report["repositories"][0]["url"] == "https://github.com/org/repo"

        assert isinstance(report["images"], list)
        assert report["images"] == []

        assert isinstance(report["components"], list)
        assert len(report["components"]) == 1
        assert report["components"][0]["id"] == "test-component"
        assert report["components"][0]["repositories"] == ["org/repo"]

        # Rules as a list without internal fields
        assert isinstance(report["rules"], list)
        rule_ids = {r["id"] for r in report["rules"]}
        assert rule_ids == set(RULES.keys())
        for rule in report["rules"]:
            assert "category" not in rule
            assert "stage" not in rule
            assert "scope" not in rule
            assert "owner" not in rule

        # Evaluations use component_id references, not inlined component
        for ev in report["evaluations"]:
            assert "rule_id" in ev
            assert ev["rule_id"] in rule_ids
            assert "component_id" in ev
            assert "component" not in ev  # no inlined component
            assert "rule" not in ev  # no inlined rule dict

        # Targets have no tier
        for ev in report["evaluations"]:
            assert "target" in ev
            assert ev["target"]["kind"] == "repository"
            assert ev["target"]["name"] == "org/repo"
            assert ev["target"]["url"] == "https://github.com/org/repo"
            assert "tier" not in ev["target"]

    def test_no_run_url_omits_url_field(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [
                {"repo": "org/repo", "jira_component": "Test", "tier": "midstream"},
            ],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")
        assert "url" not in report["source"]


# ─── 9. Empty input ────────────────────────────────────────────────


class TestEmptyInput:
    def test_no_reports_yields_empty_evaluations(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [
                {"repo": "org/repo", "jira_component": "Test", "tier": "midstream"},
            ],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")
        assert report is not None
        assert report["evaluations"] == []

    def test_summary_json_is_skipped(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "summary.json").write_text(
            json.dumps([{"repo": "org/repo", "score": "READY"}])
        )

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [
                {"repo": "org/repo", "jira_component": "Test", "tier": "midstream"},
            ],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")
        assert report["evaluations"] == []


# ─── 10. Exception policy ──────────────────────────────────────────


class TestExceptionPolicy:
    def test_all_rules_have_exception_policy(self):
        for rule_id, rule_def in RULES.items():
            assert "exception_policy" in rule_def, f"Rule {rule_id} missing exception_policy"
            ep = rule_def["exception_policy"]
            assert "mechanism" in ep
            assert "source" in ep
            assert "url" in ep["source"]
            assert ep["source"]["url"].startswith("https://")

    def test_exception_policy_in_report(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/repo", PASSING_RULES)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [{"repo": "org/repo", "jira_component": "Test", "tier": "midstream"}],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")
        for rule in report["rules"]:
            assert "exception_policy" in rule
            assert "mechanism" in rule["exception_policy"]
            assert "source" in rule["exception_policy"]


# ─── 11. Exception on evaluations ──────────────────────────────────


EXCEPTED_RULES = [
    {
        "name": "no-image-tags",
        "passed": True,  # passed because all blockers were excepted
        "blockers": 0,
        "infos": 1,
        "findings": [
            {
                "severity": "info",
                "file": "config/manager/kustomization.yaml",
                "line": 12,
                "image": "quay.io/example/foo:latest",
                "message": "Mutable tag :latest on image quay.io/example/foo:latest"
                " [Exception: test images use mutable tags]",
            },
        ],
    },
    {"name": "image-manifest-complete", "passed": True, "blockers": 0, "infos": 0, "findings": []},
    {"name": "no-runtime-egress", "passed": True, "blockers": 0, "infos": 0, "findings": []},
    {"name": "python-imports-bundled", "passed": True, "blockers": 0, "infos": 0, "findings": []},
    {"name": "params-env-wiring", "passed": True, "blockers": 0, "infos": 0, "findings": []},
]


class TestExceptionOnEvaluations:
    def test_excepted_rule_is_unmet_with_exception(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/repo", EXCEPTED_RULES)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [{"repo": "org/repo", "jira_component": "Test", "tier": "midstream"}],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")

        tags_eval = next(e for e in report["evaluations"] if e["rule_id"] == "no-image-tags")
        assert tags_eval["status"] == "unmet"
        assert "exception" in tags_eval
        assert "test images use mutable tags" in tags_eval["exception"]["reason"]
        assert "url" in tags_eval["exception"]["location"]

    def test_truly_passing_rule_has_no_exception(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/repo", EXCEPTED_RULES)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [{"repo": "org/repo", "jira_component": "Test", "tier": "midstream"}],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")

        met_evals = [e for e in report["evaluations"] if e["rule_id"] != "no-image-tags"]
        for ev in met_evals:
            assert ev["status"] == "met"
            assert "exception" not in ev


class TestExtractExceptionReasons:
    def test_single_reason(self):
        findings = [
            {"message": "some finding [Exception: reason one]"},
        ]
        assert _extract_exception_reasons(findings) == ["reason one"]

    def test_deduplicates(self):
        findings = [
            {"message": "a [Exception: same reason]"},
            {"message": "b [Exception: same reason]"},
        ]
        assert _extract_exception_reasons(findings) == ["same reason"]

    def test_no_exceptions(self):
        findings = [{"message": "clean finding"}]
        assert _extract_exception_reasons(findings) == []


# ─── 12. Repository ref ───────────────────────────────────────────


class TestRepositoryRef:
    def test_git_sha_populates_ref(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        data = {
            "repo": "org/repo",
            "date": "2026-07-15",
            "score": "READY",
            "git_sha": "abc123def456",
            "rules": PASSING_RULES,
        }
        (reports_dir / "repo.json").write_text(json.dumps(data))

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [{"repo": "org/repo", "jira_component": "Test", "tier": "midstream"}],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")
        assert len(report["repositories"]) == 1
        repo = report["repositories"][0]
        assert "ref" in repo
        assert repo["ref"]["value"] == "abc123def456"
        assert repo["ref"]["type"] == "commit"

    def test_no_git_sha_omits_ref(self, tmp_path):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/repo", PASSING_RULES)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [{"repo": "org/repo", "jira_component": "Test", "tier": "midstream"}],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")
        repo = report["repositories"][0]
        assert "ref" not in repo
