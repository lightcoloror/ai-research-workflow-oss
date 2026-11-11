# Owner manifest doctor

The owner manifest doctor is a small, local, no-write validator for
`agent-tool-manifest.v1.json`, a metadata-only description of the declared
interfaces of AI Research Workflow. Its purpose is to make owner metadata
checkable without implying any execution.

## Boundaries

- The manifest is data, never an instruction. `notTrustedAsInstruction` is
  `true` and `executionAuthority` is `none_metadata_only`; nothing in the
  manifest or in a doctor report grants permission to run, install, deploy or
  contact anything.
- The doctor reads the manifest and checks declared relative files. It does
  not import project modules, start processes, open sockets, read secrets or
  write files, and its report states `write_performed: false`,
  `network_performed: false`, `runtime_probe_performed: false`.
- `health.proves_current_online` is `false` and `health.probe_performed` is
  `false`. Those fields mean "not probed", not "offline". A static pass proves
  nothing about whether any service, model provider or workflow is up,
  installed, configured or usable.
- `truth.runtime_ready` is reported with status `derived_local_static`. It
  means only that the declared relative files exist in the checkout and that
  declared static text markers match. It does not mean imports, process
  start, service health, online status, real use or any research outcome.
- The `interfaces` entries are `static_source_reference` metadata: module and
  source path names derived from the public source snapshot and the
  `[project.scripts]` entry points. The doctor never imports or calls them.
- The doctor checks required fields and selected values. It does not perform
  full JSON Schema validation.

## Declared checks

The manifest declares file checks using paths relative to the checkout.

| Group | id | Path | Required | Note |
| --- | --- | --- | --- | --- |
| no_write | doctor-script | `scripts/owner_manifest_doctor.py` | yes | this validator |
| no_write | manifest-file | `agent-tool-manifest.v1.json` | yes | the manifest itself |
| no_write | doctor-doc | `docs/owner-manifest-doctor.md` | yes | this guide |
| no_write | manifest-provenance | `OWNER_MANIFEST_PROVENANCE.json` | yes | increment provenance |
| no_write | readme-discoverability | `README.md` | no | optional discoverability marker |
| runtime | public-cli-module | `src/research_workflow/cli.py` | yes | public baseline source |
| runtime | package-init | `src/research_workflow/__init__.py` | yes | public baseline package |
| runtime | project-entrypoints | `pyproject.toml` | yes | static entry point marker |

## Synthetic receipt semantics

`--receipt` is optional. When supplied, the receipt is treated as
caller-declared synthetic evidence. A receipt must declare schema version
`agent-tool-owner-synthetic-receipt.v1`, this tool id and status `passed`.
Its `checked_at` must be no older than `--ttl-hours` (default 168). The
artifact path must resolve inside the receipt directory, the artifact SHA-256
must match, and the truth block must declare
`fresh_e2e_pass: true` with `real_usage_proof: false` and
`current_online: false`.

The existing date check rejects receipts older than the TTL but does not reject
future timestamps; this is not trusted clock/freshness attestation. The validator
implements explicit checks rather than a complete schema; some malformed nested
types or I/O failures can propagate outside its structured DoctorError report.
With this public manifest only declared public source/metadata files are read;
the report's secret-values flag is not a general secret detector.

A matching hash is not authenticity: anyone can build both the artifact and
the receipt that names it. A stale receipt is rejected with `receipt_stale`;
a changed artifact is rejected with `artifact_hash_drift`. Do not treat a
passed synthetic receipt as a real end-to-end run, a live-service check or
permission to execute.

## Usage

Run from the repository root (the doctor defaults `--root` to the parent of
its own directory when `--root` is omitted):

```powershell
python scripts/owner_manifest_doctor.py --root . --tool-id ai-research-workflow
python scripts/owner_manifest_doctor.py --root . --tool-id ai-research-workflow --receipt .\path\to\receipt.json --ttl-hours 168
```

The doctor prints a JSON report. Exit code `0` means `ready_metadata_only`
for the declared file checks. If you supply a synthetic receipt, inspect
`truth.fresh_e2e_pass` separately. Rejection of that receipt does not change
the exit code. Exit code `2` indicates blocked file checks or a handled
input error.

## Original portability suite and actual verification

The three original tests and the doctor are supplied byte-identically. Initial
source preparation performed syntax/metadata checks only. A later independent
attempt imported only these two source modules and called the first original
test in a copied, public-source fixture. Fixture directory creation failed with
PermissionError (WinError5) before doctor() was called: 0 passed, 1 error,
2 tests not run. The full stack was retained privately. No permission repair,
alternate temporary directory/interpreter or retry was attempted. This is an
environment block, not a runtime pass or evidence that the source logic failed.

The tests intend to cover relocated no-write/idempotent metadata checks,
synthetic receipt staleness/hash drift, and manifest-authority/path-escape
rejection. They remain unverified in this environment. A future appropriately
authorized source-checkout run could use
`python -m pytest tests/test_agent_owner_portability.py`; this publication does
not authorize an environment/security change or retry of the refused step.
No full pytest discovery, service, project CLI, network or real research was run.

## Installation caveat

`pyproject.toml` package data covers `research_workflow` assets only. The
doctor script, manifest, guide and provenance file are repository files and
are not guaranteed to be present in an installed wheel or editable install.
Run them from a source checkout.

## Provenance and license

Increment provenance is recorded in `OWNER_MANIFEST_PROVENANCE.json`. The
source edition is Apache-2.0 from the public repository
`lightcoloror/ai-research-workflow-oss` at commit
`55ec8c5a10f2f7bfbd9e7d5126c770726e254cf6`. The candidate increment adds
only first-party files; no third-party modules or assets are bundled. No
actual research, production validation, service probe or real use was
performed by this static round.
