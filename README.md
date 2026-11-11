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

### Web UI capture helper (`scripts/capture-ui.mjs`)

`scripts/capture-ui.mjs` captures a full-page screenshot of a locally served
web UI over loopback HTTP. It needs an external Playwright installation with a
Chromium build; neither is bundled with this repository or added to its
dependency declarations, and the helper requires Playwright 1.31 or newer
because it uses `route.fetch({ maxRedirects: 0 })`. Both arguments are
required: an `http:` loopback URL with an explicit port, and a PNG output path.
See [docs/ui-capture.md](docs/ui-capture.md) for prerequisites, usage and the
exact scope of the browser-side request rule.

In the default mode the script's browser-side route handler allows only
same-origin `GET`/`HEAD` requests and blocks redirects. That restriction covers
routed browser requests only, not service-side traffic, and it is not
operating-system network isolation. A `GET` is not inherently side-effect-free.
`--interact` is a synthetic-service operator option that writes service state
and can trigger service-side requests; use it only against an authorized mock
instance. The helper and its guide are source-only: no real browser or service
run is claimed.
