# Component Maturity Integration

## Overview

The disconnected-readiness-scorer runs hourly batch scans on RHOAI component source repos and publishes results as a `disconnected-readiness-report` GitHub Actions artifact. The [component-maturity](https://gitlab.cee.redhat.com/data-hub/component-maturity) system fetches this artifact and merges the evaluations into the maturity report alongside its own internal checks.

Together these answer two complementary questions:

- **"Has disconnected testing been performed?"** -- component-maturity checks testing evidence
- **"Is the source code ready for disconnected deployment?"** -- disconnected-readiness-scorer checks source code and manifests

The external report format and fetching mechanism are documented in the component-maturity repo at [docs/external-reports.md](https://gitlab.cee.redhat.com/data-hub/component-maturity/-/blob/main/docs/external-reports.md).

## Rules

The report evaluates each component against five rules. Per-rule remediation guidance is in [docs/references/](references/).

| Rule ID | What it checks |
|---|---|
| `image-manifest-complete` | All container images are registered in the operator manifest for mirroring |
| `no-image-tags` | Image references use `@sha256:` digests, not mutable tags |
| `no-runtime-egress` | No hardcoded external URLs that would fail without internet |
| `python-imports-bundled` | Python dependencies are available offline (no git+https, no runtime pip install) |
| `params-env-wiring` | The params.env → kustomize → manifest wiring chain is complete |

Rule definitions, status mapping, and the ExternalBatchReport generation live in `maturity_report.py`.

## Data Flow

```
disconnected-readiness-scorer repo      component-maturity repo
──────────────────────────────────      ───────────────────────
hourly scan (readiness-summary.yml)     maturity report pipeline
  │                                       │
  ├─ run_all.py                           │
  │   ├─ clone all component repos        │
  │   ├─ run arch-analyzer                │
  │   └─ score each repo → per-repo JSON  │
  │                                       │
  ├─ maturity_report.py                   │
  │   ├─ load repo_mappings.json          │
  │   ├─ map repos to catalog IDs         │
  │   ├─ merge results by component       │
  │   └─ emit ExternalBatchReport JSON    │
  │       └─ upload artifact:             │
  │          "disconnected-readiness-      │
  │           report"                     │
  │                                 fetch_external_reports()
  │                                   │ downloads artifact
  │                                   │ deserializes ExternalBatchReport
  │                                   │ matches components by repo name
  │                                   └─ merge_external()
  │                                       └─ evaluations merged
  │                                          into maturity report
```

## Component Mapping

Repos are mapped to component-maturity catalog IDs using the software catalog's `repo_mappings.json`. This file contains entries with `{repo, jira_component}` fields. The catalog ID is derived as `jira_component.lower().replace(" ", "-")`, matching how component-maturity generates component IDs.

A vendored copy lives at `.github/config/repo_mappings.json`. The `--repo-mappings` CLI flag can override it with a freshly fetched copy. To update the vendored copy, run `update.py` in the component-maturity repo's `software-catalog-query` skill and copy the resulting `references/repo_mappings.json` here.

When multiple repos share the same Jira component (e.g., `kserve` and `odh-model-controller` both map to "Serving Orchestration"), their results are merged into a single set of evaluations. The evaluation's `component.repositories` list includes all contributing repos for matching purposes.

A repo must exist in `repo_mappings.json` before its results appear in the maturity report. If it doesn't, the upstream repo needs the `jira_component` GitHub custom property set -- see the software catalog documentation for that process.

## Artifact Contract

| Field | Value |
|---|---|
| Artifact name | `disconnected-readiness-report` |
| Contents | Single `.json` file (`ExternalBatchReport` format) |
| Retention | 10 days |
| Freshness constraint | `checked_at` must be within 48h of the maturity pipeline run |

The artifact name and source repo are configured in the component-maturity repo's `src/maturity/external.py`. The freshness constraint is enforced by the maturity system's `fetch_external_reports()` function — if the artifact's `scanned_at` timestamp is older than 48 hours at the time the maturity pipeline runs, the report is considered stale. The hourly scan schedule ensures fresh artifacts are always available.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Component missing from maturity report | Repo not in `repo_mappings.json`, or Jira component name doesn't match a catalog entry |
| Evaluations not appearing | Artifact older than 48h, or artifact name mismatch |
| Rule not evaluated for a component | Rule didn't run for that repo (e.g., `params-env-wiring` requires kustomize overlays) |
| All repos show same score | Repos sharing a Jira component are merged — one failing repo makes the component `unmet` |
| Stale repo_mappings.json | Run `update.py` in the component-maturity software-catalog-query skill, then copy the updated `references/repo_mappings.json` to `.github/config/repo_mappings.json` |
