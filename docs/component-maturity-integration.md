# Component Maturity Integration

## Overview

The disconnected-readiness-scorer runs batch scans on RHOAI component source repos and publishes results as a `disconnected-readiness-report` GitHub Actions artifact. The [component-maturity](https://gitlab.cee.redhat.com/data-hub/component-maturity) system fetches this artifact and merges the evaluations into the maturity report alongside its own internal checks.

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

Rule definitions, status mapping, and the ExternalBatchReport generation live in `maturity_report.py`. Each rule's `reference_doc` URL points to the corresponding file in `docs/references/`.

**Authoring constraint:** The `docs/references/` files are consumed standalone by the component-maturity system — they may be rendered outside this repo with no access to sibling files. They must be fully self-contained: all links absolute, no relative paths, no assumptions about surrounding repo context.

## Data Flow

```
disconnected-readiness-scorer repo      component-maturity repo
──────────────────────────────────      ───────────────────────
batch scan (readiness-summary.yml)      maturity report pipeline
  │                                       │
  ├─ run_all.py                           │
  │   ├─ clone all component repos        │
  │   └─ score each repo → per-repo JSON  │
  │                                       │
  ├─ maturity_report.py                   │
  │   ├─ load repo_mappings.json          │
  │   ├─ map repos to catalog IDs         │
  │   ├─ merge results by component       │
  │   └─ emit ExternalBatchReport JSON    │
  │       └─ upload artifact              │
  │                                       │
  │                                 fetch artifact
  │                                   │ deserialize ExternalBatchReport
  │                                   │ match components by repo name
  │                                   └─ merge into maturity report
```

## Component Mapping

Repos are mapped to component-maturity catalog IDs using the software catalog's `repo_mappings.json`. Each entry has `{repo, jira_component}` fields. The catalog ID is derived from the Jira component name, matching how component-maturity generates component IDs.

A vendored copy lives at `.github/config/repo_mappings.json`. The `--repo-mappings` CLI flag can override it with a freshly fetched copy. To update the vendored copy, re-run the software catalog update script and copy the resulting `repo_mappings.json` here.

When multiple repos share the same Jira component (e.g., `kserve` and `odh-model-controller` both map to "Serving Orchestration"), their results are merged into a single set of evaluations. The evaluation's `component.repositories` list includes all contributing repos for matching purposes.

A repo must exist in `repo_mappings.json` before its results appear in the maturity report. If it doesn't, the upstream repo needs the `jira_component` GitHub custom property set — see the software catalog documentation for that process.

## Artifact Contract

| Field | Value |
|---|---|
| Artifact name | `disconnected-readiness-report` |
| Contents | Single `.json` file (`ExternalBatchReport` format) |
| Retention | 10 days |

The artifact name and source repo are configured in the component-maturity repo's external report source list. The maturity system enforces a freshness constraint on `scanned_at` — see its docs for the current threshold.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Component missing from maturity report | Repo not in `repo_mappings.json`, or Jira component name doesn't match a catalog entry |
| Evaluations not appearing | Artifact too old (exceeds freshness threshold), or artifact name mismatch |
| Rule not evaluated for a component | Rule didn't run for that repo (e.g., `params-env-wiring` requires kustomize overlays) |
| All repos show same score | Repos sharing a Jira component are merged — one failing repo makes the component `unmet` |
| Stale repo_mappings.json | Re-run the software catalog update script and copy the updated `repo_mappings.json` to `.github/config/` |
