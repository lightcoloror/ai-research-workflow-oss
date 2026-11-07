# Third-Party Notices

Updated: 2026-08-09 20:45:00 +08:00 | Codex (GPT-5.6)

This repository is licensed under Apache-2.0. The following components are
dependencies or interoperability boundaries; their source is not copied into
this repository.

## JSON Schema Validation

`jsonschema` (`>=4.23,<5`) is MIT-licensed and provides Draft 2020-12
validation for referenced `research-output.v1` contracts. ARW does not copy
the QRP schema: callers supply the authoritative schema and the adapter checks
its pinned SHA-256 before validation.

## Question Research POC

The `evidence-bundle.v1` facade is the unchanged QRP `research-output.v1`
payload. Its authoritative schema is owned by
`https://github.com/lightcoloror/question-research-poc` at commit
`2d83fc7bb0114417b045726de69f7a3f6b46242b`, licensed under Apache-2.0. ARW
references its schema and hash; it does not vendor QRP source, schema, or state
machine code. The test extra uses the [public source edition](https://github.com/lightcoloror/question-research-poc-oss)
at commit `29f751e610a7a29fcb65b84d0455ccc8eb99b328`, whose schema bytes match.

## Web Content Fetcher

The optional `reader` extra installs `web-content-fetcher` from its
[public source edition](https://github.com/lightcoloror/web-content-fetcher-oss)
at commit `c9432cd98bafa69523722e3ab8e081f373fcb7fb`. That project is licensed under
Apache-2.0. It is the shared page acquisition/extraction layer; this project
does not duplicate or vendor its implementation.

## Model Provider Gateway

The optional `gateway` and `test` extras install
`model-provider-gateway` from its
[public source edition](https://github.com/lightcoloror/model-provider-gateway-oss)
at commit `6e69689d02dc97bc2cd3e476518ac7571e310461` (tag `v0.3.0`). That separate package is
licensed under AGPL-3.0-only and uses LiteLLM for provider protocol execution.
ARW does not vendor its source. Installing or distributing the combined
optional runtime must preserve the Gateway's license and source obligations.

## SearXNG

SearXNG is an optional, separately installed HTTP search service distributed
under AGPL-3.0-or-later. This repository communicates with a configured JSON
endpoint and does not distribute SearXNG source, configuration, indexes, logs,
or runtime data. See `https://github.com/searxng/searxng` for its license and
source.

## Development And Optional Tools

- `pytest` is used for tests and is MIT-licensed. The complete `test` extra also
  installs the exact QRP and Gateway revisions required during test collection.
- Playwright may be used by the optional UI capture script and is
  Apache-2.0-licensed; it is not a Python runtime dependency of this package.
- Optional search-provider APIs remain external services. Provider SDK source,
  credentials, responses, and account configuration are not bundled.

Reference projects named in architecture and source-review documents were
reviewed for interfaces and design evidence. Their source is not vendored or
adapted into this repository unless a file explicitly says otherwise.
