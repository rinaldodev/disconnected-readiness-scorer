## Description

RHOAI components that use the params.env + kustomize pattern must have a complete wiring chain from `params.env` through kustomize `configMapGenerator` entries into the rendered Kubernetes manifests. In disconnected deployments, the operator overrides image references by substituting values in this chain. If the wiring is broken — a hardcoded image bypasses params.env, or a Go controller reads an env var that kustomize never injects — the operator cannot substitute the mirrored registry URL, and the pod will fail with `ImagePullBackOff`.

This rule is evaluated by the [disconnected-readiness-scorer](https://github.com/opendatahub-io/disconnected-readiness-scorer) static analysis tool. It validates the full wiring chain by running kustomize with probe values and comparing the rendered output against expected image references. **Requires the `kustomize` binary on PATH** — the rule is skipped with an info finding if kustomize is not available.

## What the Check Verifies

The rule only runs when it discovers kustomize overlays (directories containing both `params.env` and `kustomization.yaml`). When the operator's overlay path mapping is available, non-production overlays are filtered out to reduce false positives.

| Check | What's verified |
|-------|----------------|
| **Image wiring** | All images in rendered manifests come from `params.env` keys — no hardcoded images bypassing the substitution chain |
| **Key consumption** | All image keys in `params.env` are consumed by a `configMapKeyRef` or kustomize replacement |
| **Go env var consistency** | `os.Getenv("RELATED_IMAGE_*")` calls in Go source match variables present in rendered manifests |
| **Operator manifest alignment** | `RELATED_IMAGE_*` vars mapped from params.env exist in the operator manifest |

### Severity classification

| Condition | Severity |
|-----------|----------|
| Hardcoded image in rendered manifest (not from params.env) | blocker |
| Go code calls `os.Getenv("RELATED_IMAGE_*")` for a var not in rendered manifests | blocker |
| `RELATED_IMAGE_*` var mapped from params.env but missing from operator manifest | blocker |
| Unused params.env key (not consumed by kustomize) | info |
| `RELATED_IMAGE` var in manifests but Go code never reads it | info |

## Common Failures

| Failure | Severity | Example |
|---------|----------|---------|
| Hardcoded image in deployment | blocker | A YAML manifest has `image: quay.io/org/sidecar:v1` directly instead of sourcing from params.env |
| Missing Go env var | blocker | Go code reads `os.Getenv("RELATED_IMAGE_PROXY")` but kustomize never injects that variable |
| Stale operator manifest var | blocker | params.env maps to `RELATED_IMAGE_OLD` but the operator manifest has renamed it |
| Unused params.env key | info | `params.env` has `odh_unused_image=...` but no kustomize replacement references it |

## Guidance

### Step 1: Add the image to params.env

Add a key for the missing image in the component's `params.env` file:

```
odh_my_sidecar=quay.io/org/sidecar@sha256:abc123...
```

### Step 2: Wire through kustomize

Add a `configMapGenerator` entry in `kustomization.yaml`:

```yaml
configMapGenerator:
  - name: my-component-config
    envs:
      - params.env
```

Then reference the key in the deployment manifest using a `configMapKeyRef`:

```yaml
env:
  - name: RELATED_IMAGE_MY_SIDECAR
    valueFrom:
      configMapKeyRef:
        name: my-component-config
        key: odh_my_sidecar
```

Or use kustomize replacements to inject the image directly into a container spec:

```yaml
replacements:
  - source:
      kind: ConfigMap
      name: my-component-config
      fieldPath: data.odh_my_sidecar
    targets:
      - select:
          kind: Deployment
        fieldPaths:
          - spec.template.spec.containers.[name=my-container].image
```

### Step 3: Verify in Go source

If the component controller reads the image from an environment variable, ensure the variable name matches what kustomize injects:

```go
image := os.Getenv("RELATED_IMAGE_MY_SIDECAR")
if image == "" {
    image = "quay.io/org/sidecar@sha256:abc123..." // fallback for connected
}
```

### Step 4: Verify the operator manifest

Confirm that the `RELATED_IMAGE_*` variable exists in the opendatahub-operator's kustomize overlays for the component:

```yaml
# In opendatahub-operator config/overlays/odh/my-component/deployment_patch.yaml
env:
  - name: RELATED_IMAGE_MY_SIDECAR
    value: quay.io/org/sidecar@sha256:abc123...
```

The operator manifest is the authoritative source for what images are mirrored in disconnected deployments.

## References

- [Kustomize configMapGenerator docs](https://kubectl.docs.kubernetes.io/references/kustomize/kustomization/configmapgenerator/)
- [disconnected-readiness-scorer source](https://github.com/opendatahub-io/disconnected-readiness-scorer/blob/main/rules/params_env.py)
