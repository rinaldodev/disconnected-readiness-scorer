# Disconnected Readiness — Remediation Guide

Guide for component teams on how to investigate and remediate each category of finding from the disconnected readiness scorer. For detailed per-rule guidance with code examples, see the individual [reference docs](references/).

## Background: How Image Injection Works

In disconnected (air-gapped) environments, cluster nodes cannot pull images from public registries. The platform solves this through a multi-stage pipeline:

1. **Build-Config repos** ([RHOAI-Build-Config](https://github.com/red-hat-data-services/RHOAI-Build-Config), [ODH-Build-Config](https://github.com/opendatahub-io/ODH-Build-Config)) declare every container image as a `RELATED_IMAGE_*` entry in `bundle-patch.yaml`. OLM uses these to populate the CSV `relatedImages` list, which `oc-mirror` reads to catalog all images for mirroring.

2. **Operator `*_support.go` files** define an `imageParamMap` (or `imagesMap`) per component that maps `params.env` keys to `RELATED_IMAGE_*` env var names. For example:
   ```go
   imageParamMap = map[string]string{
       "odh-kuberay-operator-controller-image": "RELATED_IMAGE_ODH_KUBERAY_OPERATOR_CONTROLLER_IMAGE",
   }
   ```

3. **At runtime**, OLM injects `RELATED_IMAGE_*` env vars (containing mirrored image digests) into the operator pod. The operator's `ApplyParams()` function reads each `params.env` key, looks up the corresponding `RELATED_IMAGE_*` value via `os.Getenv()`, and overwrites the `params.env` default with the mirrored reference.

4. **Kustomize renders** the updated `params.env` values into manifests, which are applied to the cluster through the deploy action from the operator. All image references now point at the mirror registry.

Any break in this chain — a missing `params.env` key, a missing `imageParamMap` entry, or a missing Build-Config `RELATED_IMAGE_*` — means that image will not be overridden and will fail to pull in disconnected environments.

The opendatahub-operator also runs [validate-related-images.sh](https://github.com/opendatahub-io/opendatahub-operator/blob/main/.github/scripts/validate-related-images.sh) in CI to validate this chain end-to-end. Component teams can reference its output when debugging wiring issues.

---

## 1. Image Manifest Completeness (`image-manifest-complete`)

Every container image reference must be mapped to a `RELATED_IMAGE_*` env var (or listed in CSV `relatedImages`) so the operator can inject mirrored images in disconnected environments.

**Investigate:** Go to the reported file/line. Is this image actually pulled at runtime on a customer cluster? Check whether a `RELATED_IMAGE_*` variable covers it on the same line, in the same file, or in a sibling file.

**Remediate:** See the [image-manifest-complete reference doc](references/image-manifest-complete.md) for detailed guidance by pattern (env_var, params.env, static CSV) with code examples.

**False positives:** Build-time-only images (in scripts that generate Dockerfiles but never run on-cluster), images behind disabled feature gates, and files marked `[out of production scope]`. If a non-production file is not already auto-excepted, add a path exception in [config/config.yaml](https://github.com/opendatahub-io/disconnected-readiness-scorer/blob/main/config/config.yaml).

## 2. Mutable Image Tags (`no-image-tags`)

All image references must use `@sha256:` digests, not mutable tags (`:latest`, `:v1.2.3`). Tags can change after mirroring, causing silent drift or pull failures.

**Investigate:** Check whether the tagged image is deployed to a customer cluster at runtime. Images in `params.env` files are already auto-downgraded (the release process resolves tags to digests in the Build-Config repos before they reach the CSV).

**Remediate:** See the [no-image-tags reference doc](references/no-image-tags.md) for digest lookup commands and before/after code examples.

**False positives:** Build-time images (Dockerfiles, CI scripts) that are never pulled on-cluster should be auto-excepted. Files marked `[out of production scope]` are already downgraded. Non-image strings that happen to match the `registry/org/name:tag` pattern (npm refs in `package.json` are already excluded, but other formats may occasionally trigger).

## 3. Runtime Network Egress (`no-runtime-egress`)

Detects outbound HTTP/network calls (`http.Get`, `requests.get`, `fetch`, `curl`, HuggingFace downloads) that would fail air-gapped. Distinguishes hardcoded external URLs (blocker) from configurable URLs and cluster-internal calls (info).

**Investigate:** Is the URL hardcoded to an external endpoint, or configurable via env var / config / CR field? Is the target cluster-internal (e.g. `*.svc.cluster.local`)? The scanner detects configurability by looking for `os.Getenv`, `config.`, `viper.`, `${...}` on the same line — if the config read is on a different line, the scanner may miss it.

**Remediate:** See the [no-runtime-egress reference doc](references/no-runtime-egress.md) for options including making URLs configurable, pre-caching model artifacts, and requesting exceptions.

**False positives:** HTTP client setup code that constructs a client but only calls internal endpoints; Go files outside production scope; URLs that are configurable but the config read happens on a different line. Verify manually and add a central exception if confirmed safe.

## 4. Python Dependency Availability (`python-imports-bundled`)

Flags `git+https://` dependencies (require internet at install time), runtime `pip install` calls, and packages not in the known-bundled list.

**Investigate:** For `git+https://` deps, check if the package is available on PyPI or an internal mirror. For runtime `pip install`, determine if the code path runs in production or only in dev/build scripts. "Not in known-bundled list" findings are info-level — just verify availability in the internal PyPI mirror.

**Remediate:** See the [python-imports-bundled reference doc](references/python-imports-bundled.md) for guidance on replacing git deps, eliminating runtime pip installs, and vendoring private packages.

**False positives:** Build-time and CI scripts that call `pip install` but never run on a customer cluster (e.g. lockfile generators, CVE scanners). Check if the file is in `scripts/`, `.tekton/`, or `hack/`.

## 5. Params.env + Kustomize Wiring (`params-env-wiring`)

Validates the full wiring chain: `params.env` key → kustomize `configMapKeyRef`/replacement → rendered `RELATED_IMAGE_*` env var → Go `os.Getenv()`.

**Investigate:** For hardcoded images, check the kustomize overlay to see if the image should be parameterized. For unwired keys, check whether a `configMapKeyRef` or replacement is missing. For orphan `os.Getenv`, check for typos in the var name or missing kustomize mappings.

**Remediate:** See the [params-env-wiring reference doc](references/params-env-wiring.md) for step-by-step wiring examples including the operator manifest and Build-Config entries.

**False positives:** Orphan `os.Getenv` findings are the most likely to be false positives — the scanner checks for `os.Getenv("RELATED_IMAGE_*")` calls repo-wide, which may match code in non-production binaries (e.g. `cmd/test-tool/`), utility functions that are never called at runtime, or vars that are injected through a different mechanism than kustomize. Verify the Go file is in the production binary's import graph before treating these as real blockers.

---

## Identifying False Positives

Ask yourself: **does this code actually run on a customer cluster in production?**

- **No, it's test/CI/docs/examples** → Should be auto-excepted; if not, add a path exception
- **No, it only runs at build time** → Not a runtime concern (e.g. Dockerfiles, build scripts, lockfile generators)
- **No, it's in a manifest that isn't deployed** → Not a customer-facing resource
- **Unsure** → Check whether the finding is annotated `[out of production scope]`, which means the scanner determined it's not in the production code path. If there's no annotation and you still believe it's a false positive, open a PR to request a central exception with a reason.
- **Yes, it runs in production** → The finding is real and needs remediation

## Configuring Exceptions

The centralized [config/config.yaml](https://github.com/opendatahub-io/disconnected-readiness-scorer/blob/main/config/config.yaml) already excludes common test directories, CI config, build files, docs, examples, and samples. For repo-specific overrides, create a new exception entry that references your repo, open a PR, and request a review.

Example:
```yaml
exceptions:
  - rules: "*"
    paths:
      - "internal/devtools/**"
    repo: opendatahub-io/kserve
    reason: "Dev tooling — not deployed in production"
```

**Time-bounded exceptions:** For temporary workarounds, add an `expires: "YYYY-MM-DD"` field. The scanner will stop honoring the exception after that date, and the PR check will start failing again — ensuring the team returns to fix the root cause. The scanner warns 14 days before expiration in its report output. To renew, update the `expires` date and submit a PR.

```yaml
exceptions:
  - rule: no-runtime-egress
    repo: my-component
    paths:
      - "internal/legacy_client.go"
    reason: "Legacy HTTP client — migrating to configurable URL"
    expires: "2026-12-31"
```
