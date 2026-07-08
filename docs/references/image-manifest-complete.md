## Description

Every container image used by an RHOAI component must be registered in the operator's image management system. In disconnected deployments, the operator injects mirrored registry URLs for all registered images via environment variables or kustomize overlays. Images that are not registered cannot be mirrored by the disconnected install helper and will not be available in the air-gapped cluster, causing pod failures with `ImagePullBackOff` at runtime.

This rule is evaluated by the [disconnected-readiness-scorer](https://github.com/opendatahub-io/disconnected-readiness-scorer) static analysis tool. It scans component source code and manifests to verify that all image references are accounted for in the operator's image management system.

## What the Check Verifies

The rule auto-detects which image management pattern the repo uses:

| Pattern | Detection | How images are registered |
|---------|-----------|---------------------------|
| **env_var** | 5+ `RELATED_IMAGE_*` references in `.go` files | Each image has a `RELATED_IMAGE_*` env var in the operator deployment |
| **params.env** | Directory with both `params.env` and `kustomization.yaml` | Images listed in `params.env`, wired through kustomize |
| **static CSV** | YAML file with both `relatedImages:` and `ClusterServiceVersion` | Images listed in the CSV `relatedImages` section |

For each image reference found in source code (`.go`, `.py`) and manifests (`.yaml`, `.yml`, `.json`, `.sh`), the rule checks whether a corresponding entry exists in the detected image management system. Only git-tracked files are scanned.

Findings in non-production kustomize overlays (determined by the operator's overlay path mapping) are downgraded from blocker to info severity.

When the orchestrator provides operator manifest data, the rule also cross-references `RELATED_IMAGE_*` env vars against the authoritative operator manifest to catch stale or renamed variables.

## Common Failures

| Failure | Severity | Example |
|---------|----------|---------|
| Image without `RELATED_IMAGE_*` mapping | blocker | Go source assigns `"quay.io/org/image:v1"` with no `RELATED_IMAGE_*` on the line |
| Env var not in operator manifest | blocker | Repo uses `RELATED_IMAGE_FOO` but the operator manifest has no such variable |
| Image missing from CSV `relatedImages` | blocker | YAML manifest references an image not listed in the ClusterServiceVersion |
| Stale env var in repo source | blocker | `RELATED_IMAGE_OLD_NAME` in repo, but operator renamed it to `RELATED_IMAGE_NEW_NAME` |

## Guidance

### Option 1: Add a RELATED_IMAGE env var (env_var pattern)

For repos using the env_var pattern, add a `RELATED_IMAGE_*` environment variable on the same line as the image reference in Go code:

```go
image := os.Getenv("RELATED_IMAGE_MY_COMPONENT")
if image == "" {
    image = "quay.io/org/my-component@sha256:abc123..."
}
```

The operator must also define this variable in its kustomize overlay so the disconnected install helper can inject the mirrored image reference:

```yaml
# In the operator's component overlay (e.g. config/overlays/odh/deployment.yaml)
env:
  - name: RELATED_IMAGE_MY_COMPONENT
    value: quay.io/org/my-component@sha256:abc123...
```

### Option 2: Add to params.env (params.env pattern)

For repos using the params.env + kustomize pattern, add the image to `params.env`:

```
odh_my_component=quay.io/org/my-component@sha256:abc123...
```

Then wire it through kustomize by adding a `configMapGenerator` entry and a `configMapKeyRef` in the deployment manifest. See the [params-env-wiring reference](params-env-wiring.md) for complete wiring examples.

### Option 3: Add to relatedImages (static CSV pattern)

For repos using the static CSV pattern, add the image to the `relatedImages` section of the ClusterServiceVersion:

```yaml
relatedImages:
  - name: my-component
    image: quay.io/org/my-component@sha256:abc123...
```

## References

- [Disconnected install helper](https://github.com/opendatahub-io/opendatahub-operator/blob/main/docs/disconnected.md)
- [disconnected-readiness-scorer source](https://github.com/opendatahub-io/disconnected-readiness-scorer/blob/main/rules/image_manifest_complete.py)
