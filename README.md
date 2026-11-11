# AI Research Workflow

Local-first multi-source research planning, retrieval and evidence review.
The workflow keeps candidate sources, atomic claims and reviewed evidence
separate and can verify a referenced Question Research evidence bundle.

## Install and validate the local contract

```sh
python -m pip install .
research --help
research contract-test --format json
research readiness
research agent-diagnose --skip-smoke --format json
```

Use [research.config.example.json](research.config.example.json) to create a
local configuration. The built-in reader and offline contract are available
with the base installation. Optional extras:

| Extra | Adds |
| --- | --- |
| `reader` | The public Web Content Fetcher package for page extraction. |
| `gateway` | The public Model Provider Gateway and its execution adapter. Provider access still requires local configuration and consent. |
| `test` | pytest and the public QRP and Gateway dependencies for offline integration tests. |

Install `".[test,reader]"` and run `python -m pytest -q` from the source
checkout to exercise the portable suite. It uses synthetic records and mock
transports. Live retrieval and provider runs need separate configuration.

The public source edition adapts packaging, documentation, the contract
validator's packaged-resource lookup and synthetic tests for independent
installation. The source checkout and installed wheel both
include the generated client contract and agent guide. Keep configurations, credentials and captures
outside version control. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for component-specific terms.

## Public edition scope

This edition provides the Python CLI, HTTP/MCP implementations, web assets,
client templates and portable synthetic tests. Machine-specific launcher,
Windows service, Docker deployment and private lifecycle adapter tests are
excluded together with the deployment files they inspect. Deployment on those
systems requires separate configuration and validation.

One embedding test requires an explicitly configured local runtime and model
and is skipped when they are unavailable. Offline tests allow loopback mock
servers and reject external network connections.


### Research Evidence Lifecycle Adapter

Optional source-only adapter scripts in `scripts/research-evidence-lifecycle.ps1` and synthetic test source in `tests/test_research_evidence_lifecycle_adapter.py` integrate with sibling `artifact-lifecycle-core` under Apache-2.0. The wrapper exposes planning/verification commands; it can write reports and checkpoints. Source/static checks are separate from runtime acceptance; this batch has a retained pytest permission block. See [docs/research-evidence-lifecycle.md](docs/research-evidence-lifecycle.md).

## Owner manifest doctor

`ai-research-workflow` includes a metadata-only owner manifest
(`agent-tool-manifest.v1.json`) and a local, no-write validator
(`scripts/owner_manifest_doctor.py`). The owner manifest doctor checks only
declared relative file presence and static text markers: no network access,
no project imports, no writes, no service probe.

```powershell
python scripts/owner_manifest_doctor.py --root . --tool-id ai-research-workflow
```

`runtime_ready` is derived local static metadata only. The manifest is data,
not instructions, and carries `executionAuthority: none_metadata_only`.
`health.proves_current_online` and `health.probe_performed` are `false`,
which means "not probed", not "offline". See
`docs/owner-manifest-doctor.md` and `OWNER_MANIFEST_PROVENANCE.json`.
