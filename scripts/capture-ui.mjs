import { chromium } from "playwright";
import { resolve } from "node:path";

function printUsageAndExit(message) {
  if (message) {
    console.error(`Error: ${message}\n`);
  }
  console.error(
    "Usage: node scripts/capture-ui.mjs <url> <output-path> [--interact]\n\n" +
    "Arguments:\n" +
    "  <url>          Explicit loopback HTTP URL (e.g. http://127.0.0.1:8766/)\n" +
    "  <output-path>  Target path for output PNG screenshot\n" +
    "  --interact     (Optional) Run diagnostic and research interaction sequence.\n" +
    "                 WARNING: Modifies service state and may trigger server-side external network requests.\n" +
    "                 Only use against an operator-authorized mock/synthetic service instance."
  );
  process.exit(1);
}

const args = process.argv.slice(2);
let rawUrl = null;
let rawOutput = null;
let enableInteraction = false;

for (const arg of args) {
  if (arg === "--interact" || arg === "--enable-interaction") {
    enableInteraction = true;
  } else if (arg === "--help" || arg === "-h") {
    printUsageAndExit();
  } else if (arg.startsWith("-")) {
    printUsageAndExit(`Unknown option: ${arg}`);
  } else if (!rawUrl) {
    rawUrl = arg;
  } else if (!rawOutput) {
    rawOutput = arg;
  } else {
    printUsageAndExit(`Unexpected positional argument: ${arg}`);
  }
}

if (!rawUrl || !rawOutput) {
  printUsageAndExit("Both <url> and <output-path> are required.");
}

let targetUrl;
try {
  targetUrl = new URL(rawUrl);
} catch {
  printUsageAndExit("Invalid URL.");
}

if (targetUrl.protocol !== "http:") {
  printUsageAndExit(`URL protocol must be "http:", got "${targetUrl.protocol}".`);
}

if (targetUrl.username || targetUrl.password) {
  printUsageAndExit("URL credentials (username or password) are disallowed.");
}

const loopbackHosts = new Set(["127.0.0.1", "localhost", "[::1]"]);
if (!loopbackHosts.has(targetUrl.hostname)) {
  printUsageAndExit(
    `URL hostname must be a loopback address (127.0.0.1, localhost, [::1]), got "${targetUrl.hostname}".`
  );
}

if (!targetUrl.port) {
  printUsageAndExit("URL must specify an explicit port number.");
}

const port = parseInt(targetUrl.port, 10);
if (isNaN(port) || port < 1 || port > 65535) {
  printUsageAndExit(`URL port must be an integer between 1 and 65535, got "${targetUrl.port}".`);
}

const output = resolve(rawOutput);
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined;

let browser;
try {
  browser = await chromium.launch({ headless: true, executablePath });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 1,
    serviceWorkers: "block",
  });

  await context.route("**/*", async (route) => {
    try {
      const request = route.request();
      let requestUrl;
      try {
        requestUrl = new URL(request.url());
      } catch {
        await route.abort("blockedbyclient");
        return;
      }

      if (requestUrl.origin !== targetUrl.origin) {
        console.error("Blocked cross-origin request.");
        await route.abort("blockedbyclient");
        return;
      }

      if (requestUrl.username || requestUrl.password) {
        console.error("Blocked request with credentials.");
        await route.abort("blockedbyclient");
        return;
      }

      const method = request.method().toUpperCase();
      if (!enableInteraction && method !== "GET" && method !== "HEAD") {
        console.error("Blocked non-GET/HEAD request in default mode.");
        await route.abort("blockedbyclient");
        return;
      }

      const response = await route.fetch({ maxRedirects: 0 });
      const status = response.status();
      const headers = response.headers();
      const location = headers["location"] || headers["Location"];

      if (location || (status >= 300 && status < 400)) {
        console.error("Blocked redirect response.");
        await route.abort("blockedbyclient");
        return;
      }

      await route.fulfill({ response });
    } catch (routeErr) {
      console.error("Could not handle a routed browser request.");
      await route.abort("failed").catch(() => {});
    }
  });

  const page = await context.newPage();
  page.setDefaultTimeout(120000);
  page.setDefaultNavigationTimeout(120000);

  const response = await page.goto(targetUrl.href, { waitUntil: "networkidle" });
  if (!response || !response.ok()) {
    throw new Error(`Failed to load target URL: HTTP ${response ? response.status() : "no response"}`);
  }

  const finalUrl = new URL(page.url());
  if (finalUrl.origin !== targetUrl.origin) {
    throw new Error(`Unexpected navigation outside target origin: ${finalUrl.origin}`);
  }

  if (enableInteraction) {
    const matchApiResponse = (res, expectedPathname) => {
      try {
        const resUrl = new URL(res.url());
        return resUrl.origin === targetUrl.origin && resUrl.pathname === expectedPathname && res.status() === 200;
      } catch {
        return false;
      }
    };

    await Promise.all([
      page.waitForResponse((res) => matchApiResponse(res, "/api/doctor")),
      page.click("#doctorBtn"),
    ]);
    await Promise.all([
      page.waitForResponse((res) => matchApiResponse(res, "/api/selftest")),
      page.click("#selftestBtn"),
    ]);
    await Promise.all([
      page.waitForResponse((res) => matchApiResponse(res, "/api/evals")),
      page.click("#evalsBtn"),
    ]);

    const readinessPromise = page
      .waitForResponse((res) => matchApiResponse(res, "/api/readiness"), { timeout: 30000 })
      .catch(() => {
        console.warn("Readiness check did not succeed within 30s.");
        return null;
      });
    await page.click("#readinessBtn");
    await readinessPromise;

    await page.fill("#topicInput", "SearXNG");
    const networkToggle = page.locator("#networkToggle");
    if (await networkToggle.isEnabled()) {
      await networkToggle.check();
    }
    await Promise.all([
      page.waitForResponse((res) => matchApiResponse(res, "/api/search")),
      page.click("#runBtn"),
    ]);
    await page.fill("#urlInput", "https://docs.searxng.org/\nhttps://github.com/shmsw25/factscore");
    await Promise.all([
      page.waitForResponse((res) => matchApiResponse(res, "/api/ingest")),
      page.click("#ingestBtn"),
    ]);
    await page.fill("#evidenceUrl", "https://docs.searxng.org/");
    await page.fill(
      "#evidenceText",
      "SearXNG is a free internet metasearch engine which aggregates results from many search services."
    );
    await Promise.all([
      page.waitForResponse((res) => matchApiResponse(res, "/api/evidence")),
      page.click("#addEvidenceBtn"),
    ]);
    await Promise.all([
      page.waitForResponse((res) => matchApiResponse(res, "/api/run")),
      page.click("#fullRunBtn"),
    ]);
    await page.waitForTimeout(800);
  }

  await page.screenshot({ path: output, fullPage: true });
  console.log(output);
} finally {
  if (browser) {
    try {
      await browser.close();
    } catch (closeErr) {
      console.error("Warning: Failed to close browser cleanly:", closeErr);
    }
  }
}
