# Research Evidence Lifecycle Adapter

`docs/research-evidence-lifecycle.md` documents the optional, source-only research-evidence lifecycle adapter and test suite, licensed under Apache-2.0 for `question-research-poc` (QRP) and `ai-research-workflow` (ARW). The integration reuses the public `artifact-lifecycle-core` module at commit `fab239085724458c2a6b9452d0a1ba4f4012593e`.

## Sibling Checkout Layout

Both repository wrappers and test suites expect a literal sibling directory named `artifact-lifecycle-core`. When cloning public repositories whose URLs end in `-oss`, the clone target directory must explicitly override to this literal name. The core root provides `artifact_lifecycle_core.py`, `research_evidence_adapter.py`, and `profiles/artifact-lifecycle-profiles.v1.json`. Tests prepend this sibling directory to `sys.path` to load both modules:

```
workspace/
├── artifact-lifecycle-core/   (at fab239085724458c2a6b9452d0a1ba4f4012593e)
└── question-research-poc/     (or ai-research-workflow/)
```

## Test Invocation and Fixture Materialization

The following setup commands are examples, not commands executed by this batch. Choose either research repository, using these explicit sibling clone names:

```sh
git clone https://github.com/lightcoloror/artifact-lifecycle-core-oss.git artifact-lifecycle-core
git -C artifact-lifecycle-core checkout --detach fab239085724458c2a6b9452d0a1ba4f4012593e
git clone https://github.com/lightcoloror/question-research-poc-oss.git question-research-poc
# Alternative: git clone https://github.com/lightcoloror/ai-research-workflow-oss.git ai-research-workflow
```

From the chosen research checkout, an already prepared Python environment with `pytest` can run only this test file:

```sh
python -B -m pytest -q -p no:cacheprovider tests/test_research_evidence_lifecycle_adapter.py
```

The Core adapter is loaded from that sibling source tree, not an installed wheel. No dependencies are installed by the wrapper or this guide.

Committed fixtures (`tests/fixtures/artifact-lifecycle/research-evidence.v1.json`) are synthetic JSON descriptors, not pre-materialized directories. The tests are designed to construct temporary synthetic trees under `tmp_path`: QRP covers manifest stability, reference pinning, drift, synthetic quarantine/restore and low-disk blocking; ARW covers stability, pinning, drift and rejection of an unconfigured root. This is not an OS sandbox or real research acceptance. Configured labels such as `verified` and `rebuild_proof` represent illustrative fixture assertions rather than primary evidence or factual certification.

## Wrapper Usage and Lifecycle Operations

The PowerShell wrapper `scripts/research-evidence-lifecycle.ps1` validates that sibling adapter files and local configs exist before delegating to `python`:

```powershell
# Example only: first materialize a wholly synthetic tree at the configured root.
powershell.exe -NoProfile -File .\scripts\research-evidence-lifecycle.ps1 doctor --root .\tests\fixtures\artifact-lifecycle\research-evidence
```

- **Permitted Commands**: `doctor`, `preflight`, `scan`, `explain`, `plan`, and `verify-plan`. Destructive commands (`apply`, `delete`, `restore`) are omitted from wrapper dispatch.
- **Filesystem Writes**: Lifecycle actions are not read-only: `scan` writes SQLite checkpoints and manifests; `plan` writes plan files. The declared `real_mutation_count: 0` is not proof of no disk writes or permission to mutate real evidence.
- **Configured Root**: The config accepts only `tests/fixtures/artifact-lifecycle/research-evidence`, which must already exist for wrapper operations. A descriptor JSON alone does not create it. Unclassified paths default to pinned unknown references; this is not OS-level confinement or complete path-security proof.

## Operational Boundaries

This batch passed Python AST parsing and Windows PowerShell 5.1 nonexecuting parsing. The QRP pytest attempt exited 1 at a temporary-directory `PermissionError` (three error indicators, no passing test body proven); the serial ARW runtime invocation was not run. The failure was retained with no ACL, temporary-directory-mode, guard or assertion changes and no retry. Neither suite is claimed to pass here. Live wrappers, production `doctor`, installed packages, cross-platform execution, real evidence quality and real-data lifecycle actions remain unverified. No new service, collection, model call, cleanup policy or deletion authorization is provided.
