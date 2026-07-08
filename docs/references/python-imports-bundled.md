## Description

Python dependencies in RHOAI component repos must be available from bundled/internal package repositories. In disconnected environments, there is no access to PyPI or external Git repositories. Dependencies that require `pip install` from the internet at build or runtime will fail, breaking container image builds and causing runtime crashes when packages cannot be resolved.

This rule is evaluated by the [disconnected-readiness-scorer](https://github.com/opendatahub-io/disconnected-readiness-scorer) static analysis tool. It checks requirements files, build configuration, and runtime source for dependencies that cannot be resolved without network access.

## What the Check Verifies

The rule scans three categories of files:

### Requirements files
- `requirements*.txt`, `constraints*.txt` (at root and recursively)
- Checks each dependency against a [known-bundled package list](https://github.com/opendatahub-io/disconnected-readiness-scorer/blob/main/rules/python_imports.py) (includes `numpy`, `pandas`, `torch`, `tensorflow`, `transformers`, `requests`, `flask`, `fastapi`, `boto3`, `kfp`, `kubernetes`, and others)
- Detects `git+https://` dependencies that require cloning from the internet

### Build configuration
- `**/setup.py`, `**/pyproject.toml`
- Detects `git+https://` dependencies in build configuration

### Runtime source
- `**/*.py` (recursively, excluding `vendor/`, `node_modules/`, `venv/`, `.venv/`)
- Detects runtime `pip install` and `pip3 install` calls
- Detects `subprocess` calls that invoke pip

### Severity classification

| Condition | Severity |
|-----------|----------|
| `git+https://` dependency in requirements file | blocker |
| `git+https://` dependency in setup.py or pyproject.toml | blocker |
| Runtime `pip install` in Python source | blocker |
| Package not in known-bundled list | info |

## Common Failures

| Failure | Severity | Example |
|---------|----------|---------|
| Git dependency in requirements.txt | blocker | `git+https://github.com/org/repo.git@main#egg=pkg` |
| Runtime pip install | blocker | `subprocess.run(["pip", "install", "some-package"])` |
| Git dependency in pyproject.toml | blocker | `dependencies = ["pkg @ git+https://github.com/org/repo"]` |
| Unknown package | info | `obscure-ml-lib==1.2.3` (not in known-bundled list) |

## Guidance

### Step 1: Replace git+https dependencies with versioned packages

Replace `git+https://` dependencies with published package versions available on PyPI or an internal mirror:

```
# Before (blocker — requires internet to clone)
git+https://github.com/org/custom-lib.git@v1.0#egg=custom-lib

# After (resolvable from internal PyPI mirror)
custom-lib==1.0.0
```

If the package is not published on PyPI, build a wheel and host it on the internal PyPI mirror.

### Step 2: Eliminate runtime pip install calls

Move all package installations to the container image build phase:

```dockerfile
# Install at build time, not runtime
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
```

Remove any runtime code that installs packages:

```python
# Remove this pattern — will fail without internet
import subprocess
subprocess.run(["pip", "install", "some-package"])
```

### Step 3: Verify package availability on internal mirror

For packages flagged as not in the known-bundled list (info severity), verify they are available on the internal PyPI mirror used in disconnected deployments. If not, request that they be added to the mirror.

### Step 4: Vendor private dependencies

For internal or private packages not available on any mirror, vendor them into the container image:

```dockerfile
COPY vendor/custom_lib-1.0.0-py3-none-any.whl /tmp/
RUN pip install /tmp/custom_lib-1.0.0-py3-none-any.whl
```

### False positives

Build-time and CI scripts that call `pip install` but never run on a customer cluster (e.g. lockfile generators, CVE scanners in `scripts/`, `.tekton/`, or `hack/`) are common false positives. Add a path exception in [config/config.yaml](https://github.com/opendatahub-io/disconnected-readiness-scorer/blob/main/config/config.yaml) if the file is not already auto-excepted.

## References

- [Remediation guide](https://github.com/opendatahub-io/disconnected-readiness-scorer/blob/main/docs/remediation-guide.md#4-python-dependency-availability-python-imports-bundled) — investigation workflow and false positive identification
- [Rules reference](https://github.com/opendatahub-io/disconnected-readiness-scorer/blob/main/docs/rules-reference.md#rule-python-imports-bundled) — implementation details
- [Rule source](https://github.com/opendatahub-io/disconnected-readiness-scorer/blob/main/rules/python_imports.py)
