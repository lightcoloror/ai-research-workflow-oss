# Web UI capture (`scripts/capture-ui.mjs`)

`scripts/capture-ui.mjs` captures a full-page screenshot of a locally served
web UI over loopback HTTP. It exists for documentation and interface
archiving; it is not a test harness and not a network sandbox.

## Prerequisites

- A Node.js runtime that supports ES modules and top-level `await`.
- Playwright with a Chromium build, installed separately from this repository.
  Neither Playwright nor Chromium is bundled with this repository or added to
  its dependency declarations. Install Playwright in an operator-controlled
  location, or point `PLAYWRIGHT_CHROMIUM_EXECUTABLE` at an existing Chromium
  executable.
- Playwright 1.31 or newer. The script calls `route.fetch({ maxRedirects: 0 })`,
  and `maxRedirects` is a `Route.fetch()` option available from Playwright
  1.31. See
  <https://playwright.dev/docs/api/class-route#route-fetch>.

## Usage

```bash
# Default mode: load the page and write a full-page PNG.
node scripts/capture-ui.mjs <http-loopback-url> <output-png-path>

# Example
node scripts/capture-ui.mjs http://127.0.0.1:8766/ ./output.png

# Interaction mode: run the diagnostic and research sequence first.
node scripts/capture-ui.mjs http://127.0.0.1:8766/ ./output.png --interact
```

Both positional arguments are required; there is no default target URL or
output path. The URL must use `http:` (not HTTPS), must name a loopback host
(`127.0.0.1`, `localhost` or `[::1]`), must include an explicit port, and must
not contain a username or password. The default page timeout and navigation
timeout are both 120 seconds.

## What the browser-side rule covers

The script installs one context-level route handler for `**/*`. For browser
requests that this handler receives, it allows only requests whose origin is
exactly the target origin and whose URL carries no credentials, and it allows
only `GET` and `HEAD` by default. It aborts a request whose URL cannot be
parsed. It forwards with `route.fetch({ maxRedirects: 0 })` and aborts any
response that carries a `Location` header or a 3xx status, in both modes.
Service workers are disabled for the context (`serviceWorkers: "block"`), so
they cannot be used to bypass the handler. A failure while handling a route
aborts that request. After the page loads, its final origin must still be the
target origin.

This is a rule for routed same-origin browser requests. It is not a claim that
all browser or server traffic is blocked:

- The rule applies only to browser-side HTTP requests that pass through this
  Playwright context route handler. It is not operating-system-level network
  isolation and does not constrain other processes.
- It does not observe or stop traffic that the captured service starts on its
  own, such as server-side outbound requests or background jobs.
- Allowing `GET` and `HEAD` by default is a client-side method filter, not a
  guarantee of read-only behavior. A `GET` is not intrinsically
  side-effect-free: a loopback service may change state on `GET`, and loading
  the page can itself make the service do work.

Treat the script as browser-side defense in depth for the capture process, not
as a sandbox for the captured application.

## Interaction mode (`--interact`)

`--interact` (also accepted as `--enable-interaction`) runs a diagnostic and
research sequence before the screenshot: it clicks `#doctorBtn`,
`#selftestBtn`, `#evalsBtn` and `#readinessBtn`, fills `#topicInput`, enables
`#networkToggle` when it is enabled, clicks `#runBtn`, fills `#urlInput` and
clicks `#ingestBtn`, fills `#evidenceUrl` and `#evidenceText` and clicks
`#addEvidenceBtn`, then clicks `#fullRunBtn`. API steps wait for a same-origin
`/api/...` response with status 200, and the readiness step tolerates a
30-second timeout.

This is an operator option for an authorized mock or synthetic service
instance. It writes service state, and the network-related controls can make
the service issue its own outbound requests. In this mode the `GET`/`HEAD`
method filter is bypassed; the same-origin, credential and redirect rules still
apply. Do not run it against production or an unisolated environment.

## Verification status

This guide is written from source review. The eight synthetic-substitute
controls recorded for the source, with `GET` and `HEAD` counted separately,
are:

1. same-origin `GET` allowed
2. same-origin `HEAD` allowed
3. direct cross-origin request rejected
4. request with URL credentials rejected
5. default-mode non-`GET`/`HEAD` method (for example `POST`) rejected
6. redirect (3xx or `Location`) blocked before it reaches the page
7. `route.fetch` failure aborted deterministically
8. navigation failure together with `browser.close()` failure preserves the
   primary error and reports the close error as a warning

The ordering of the readiness response listener in interaction mode is a
source-reading observation, not one of these eight executed controls.

Not verified, and not implied by the controls above: a real run in a browser
with a real Playwright/Chromium installation; a real web service, including
page load, DOM selectors and API response protocols; the end-to-end
interaction-mode flow; and any operating-system- or host-level network
isolation. No real browser or service run is claimed.

## Contents

This guide and the script are source-only materials. They contain no real
captures, service configuration, credentials, logs or run artifacts; keep such
files outside version control.
