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

Use [research.config.example.json](https://github.com/lightcoloror/ai-research-workflow-oss/blob/9f1b2c03f2ee71fcb1bc7cbd4fe70e72db252161/research.config.example.json) to create a
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
outside version control. See [LICENSE](https://github.com/lightcoloror/ai-research-workflow-oss/blob/9f1b2c03f2ee71fcb1bc7cbd4fe70e72db252161/LICENSE) and
[THIRD_PARTY_NOTICES.md](https://github.com/lightcoloror/ai-research-workflow-oss/blob/9f1b2c03f2ee71fcb1bc7cbd4fe70e72db252161/THIRD_PARTY_NOTICES.md) for component-specific terms.

## Public edition scope

This edition provides the Python CLI, HTTP/MCP implementations, web assets,
client templates and portable synthetic tests. Machine-specific launcher,
Windows service, Docker deployment and private lifecycle adapter tests are
excluded together with the deployment files they inspect. Deployment on those
systems requires separate configuration and validation.

One embedding test requires an explicitly configured local runtime and model
and is skipped when they are unavailable. Offline tests allow loopback mock
servers and reject external network connections.
