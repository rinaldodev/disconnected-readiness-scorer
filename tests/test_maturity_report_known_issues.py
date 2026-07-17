"""Regression tests for maturity_report.py and check_repo_mappings.py."""

import importlib.util
import json
import logging
from pathlib import Path

from maturity_report import _load_repo_reports, build_report, load_repo_mappings
from tests.test_maturity_report import _write_repo_mappings, _write_repo_report

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestRepoMappings:
    """load_repo_mappings() should give the same result no matter how the
    file is written: the winner of a duplicate entry should not depend on
    which one comes first in the array, and a null tier should fall back
    to the default the same way a missing tier does."""

    def test_same_tier_duplicate_winner_is_order_independent(self, tmp_path):
        """Two entries for the same repo and tier: the winner must not depend
        on which one is listed first."""
        entries = [
            {"repo": "org/repo", "jira_component": "Component One", "tier": "midstream"},
            {"repo": "org/repo", "jira_component": "Component Two", "tier": "midstream"},
        ]
        path = tmp_path / "repo_mappings.json"

        _write_repo_mappings(path, entries)
        winner_forward = load_repo_mappings(str(path))["org/repo"][1]

        _write_repo_mappings(path, list(reversed(entries)))
        winner_reversed = load_repo_mappings(str(path))["org/repo"][1]

        assert winner_forward == winner_reversed

    def test_null_tier_falls_back_to_midstream_default(self, tmp_path):
        """An explicit `"tier": null` must fall back to "midstream", same as a missing tier key."""
        path = tmp_path / "repo_mappings.json"
        path.write_text(
            json.dumps({"mappings": [{"repo": "org/repo", "jira_component": "Comp", "tier": None}]})
        )
        assert load_repo_mappings(str(path))["org/repo"][2] == "midstream"


class TestEvidence:
    """The evidence list drops any blocker that has no file to point to,
    but the count in "detail" still includes it. A reader sees "2
    blockers" and can only click through to 1, with nothing telling them
    the other exists."""

    def test_evidence_count_matches_detail_count(self, tmp_path):
        rules = [
            {
                "name": "params-env-wiring",
                "passed": False,
                "blockers": 2,
                "infos": 0,
                "findings": [
                    {
                        "severity": "blocker",
                        "file": "params.env",
                        "line": 3,
                        "image": "",
                        "message": "unwired key",
                    },
                    {
                        "severity": "blocker",
                        "file": "",
                        "line": 0,
                        "image": "",
                        "message": "orphan os.Getenv call",
                    },
                ],
            },
        ]
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/repo", rules)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [{"repo": "org/repo", "jira_component": "Test", "tier": "midstream"}],
        )

        report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")
        env_eval = next(e for e in report["evaluations"] if e["rule_id"] == "params-env-wiring")

        reported_count = int(env_eval["detail"].split()[0])
        assert len(env_eval.get("evidence", [])) == reported_count


class TestUnknownRuleName:
    """_aggregate_evaluations() logs a warning when it skips an unmapped
    repo or a malformed report, but not when it skips a rule name that
    isn't in RULES -- that rule's data just disappears from the report."""

    def test_unrecognized_rule_name_is_logged(self, tmp_path, caplog):
        rules = [
            {
                "name": "not-a-known-rule",
                "passed": False,
                "findings": [
                    {
                        "severity": "blocker",
                        "file": "main.go",
                        "line": 1,
                        "image": "",
                        "message": "something failed",
                    },
                ],
            },
        ]
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        _write_repo_report(reports_dir, "org/repo", rules)

        mappings_path = tmp_path / "mappings.json"
        _write_repo_mappings(
            mappings_path,
            [{"repo": "org/repo", "jira_component": "Test", "tier": "midstream"}],
        )

        with caplog.at_level(logging.WARNING):
            report = build_report(str(reports_dir), str(mappings_path), "", "1.0.0")

        assert report["evaluations"] == []
        assert any("not-a-known-rule" in record.message for record in caplog.records)


class TestLoadRepoReports:
    """_load_repo_reports() warns when a report file is malformed or
    missing its "repo" field, but not when two files describe the same
    repo -- the second file just overwrites the first."""

    def test_duplicate_repo_field_across_files_is_logged(self, tmp_path, caplog):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "first.json").write_text(
            json.dumps({"repo": "org/repo", "date": "2026-07-01", "score": "READY", "rules": []})
        )
        (reports_dir / "second.json").write_text(
            json.dumps(
                {"repo": "org/repo", "date": "2026-07-15", "score": "NOT READY", "rules": []}
            )
        )

        with caplog.at_level(logging.WARNING):
            reports = _load_repo_reports(str(reports_dir))

        assert reports["org/repo"]["date"] == "2026-07-15"
        assert any("org/repo" in record.message for record in caplog.records)


def _load_check_repo_mappings_module():
    path = REPO_ROOT / ".github" / "scripts" / "check_repo_mappings.py"
    spec = importlib.util.spec_from_file_location("check_repo_mappings", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return self._body


class TestCheckRepoMappingsShapeValidation:
    """check_repo_mappings.py assumes the GitHub API returns the raw file
    body. If the API instead returns its normal "contents" envelope, the
    script reads that as if every local mapping had been removed upstream."""

    def test_missing_mappings_key_is_not_treated_as_confirmed_stale(self, tmp_path, monkeypatch):
        module = _load_check_repo_mappings_module()

        local_path = tmp_path / "repo_mappings.json"
        local_path.write_text(
            json.dumps(
                {"mappings": [{"repo": "org/a", "jira_component": "X", "tier": "midstream"}]}
            )
        )
        monkeypatch.setattr(module, "LOCAL_PATH", local_path)
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")

        # A GitHub contents-API envelope, not the raw file body the script expects.
        envelope = json.dumps({"content": "ZmFrZQ==", "encoding": "base64"}).encode()
        monkeypatch.setattr(
            module.urllib.request, "urlopen", lambda *args, **kwargs: _FakeResponse(envelope)
        )

        assert module.main() == 0
