const DEFAULT_BASE_URL = "http://127.0.0.1:9082";
export const EXPECTED_CONTRACT_VERSION = "2026-06-06.v1";
export const SDK_ERROR_CATEGORIES = [
  "service_unavailable",
  "contract_mismatch",
  "job_failed",
  "timeout",
  "http_error",
  "invalid_response",
];

export class ResearchWorkflowError extends Error {
  constructor(category, message, details = null) {
    super(message);
    this.name = "ResearchWorkflowError";
    this.category = category;
    this.details = details;
  }
}

export async function health(baseUrl = DEFAULT_BASE_URL) {
  return requestJSON(`${baseUrl.replace(/\/$/, "")}/v1/health`);
}

export async function contractInfo(baseUrl = DEFAULT_BASE_URL) {
  const data = await health(baseUrl);
  return {
    contract_version: data.contract_version || "",
    config_hash: data.config_hash || "",
    base_url: data.base_url || baseUrl.replace(/\/$/, ""),
    status: data.status || "",
  };
}

export async function assertContract(expectedContractVersion = EXPECTED_CONTRACT_VERSION, baseUrl = DEFAULT_BASE_URL) {
  const info = await contractInfo(baseUrl);
  if (info.contract_version !== expectedContractVersion) {
    throw new ResearchWorkflowError("contract_mismatch", `Expected contract ${expectedContractVersion}, got ${info.contract_version}`, info);
  }
  return info;
}

export async function route(query, options = {}, baseUrl = DEFAULT_BASE_URL) {
  return postJSON(baseUrl, "/v1/route", { topic: query, ...options });
}

export async function agentResearch(query, options = {}, baseUrl = DEFAULT_BASE_URL) {
  return postJSON(baseUrl, "/v1/agent/research", {
    query,
    mode: "auto",
    max_results: 10,
    no_network: false,
    providers: [],
    urls: [],
    fetch: false,
    auto_verify: true,
    client_request_id: "",
    profile: "default",
    routing_strategy: "adaptive_staged",
    external_payloads: [],
    ...options,
  });
}

export async function agentVerify(text, options = {}, baseUrl = DEFAULT_BASE_URL) {
  return postJSON(baseUrl, "/v1/agent/verify", { text, topic: "Research Report", ...options });
}

export async function agentFetch(urls, options = {}, baseUrl = DEFAULT_BASE_URL) {
  return postJSON(baseUrl, "/v1/agent/fetch", { urls, fetch: true, ...options });
}

export async function getAgentJob(jobId, baseUrl = DEFAULT_BASE_URL) {
  return requestJSON(`${baseUrl.replace(/\/$/, "")}/v1/agent/jobs/${encodeURIComponent(jobId)}`);
}

export async function getAgentJobEvents(jobId, baseUrl = DEFAULT_BASE_URL) {
  return requestJSON(`${baseUrl.replace(/\/$/, "")}/v1/agent/jobs/${encodeURIComponent(jobId)}/events`);
}

export async function retryAgentJob(jobId, options = {}, baseUrl = DEFAULT_BASE_URL) {
  return postJSON(baseUrl, `/v1/agent/jobs/${encodeURIComponent(jobId)}/retry`, {
    payload: {},
    background: true,
    ...options,
  });
}

export async function waitForAgentJob(jobId, options = {}, baseUrl = DEFAULT_BASE_URL) {
  const intervalMs = options.intervalMs ?? 1500;
  const timeoutMs = options.timeoutMs ?? 300000;
  const startedAt = Date.now();
  while (true) {
    const job = await getAgentJob(jobId, baseUrl);
    if (job.status === "succeeded") return job;
    if (job.status === "failed") {
      throw new ResearchWorkflowError("job_failed", `Research Workflow job ${jobId} failed.`, await failureDetails(jobId, job, baseUrl));
    }
    if (Date.now() - startedAt >= timeoutMs) {
      throw new ResearchWorkflowError("timeout", `Research Workflow job ${jobId} timed out.`, await failureDetails(jobId, job, baseUrl));
    }
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }
}

export async function failureDetails(jobId, job = null, baseUrl = DEFAULT_BASE_URL) {
  const current = job || await getAgentJob(jobId, baseUrl);
  const events = await getAgentJobEvents(jobId, baseUrl);
  return {
    job: current,
    recent_events: (events.events || []).slice(-5),
    error: current.error || "",
  };
}

export async function agentResearchAndWait(query, options = {}, baseUrl = DEFAULT_BASE_URL) {
  const accepted = await agentResearch(query, options, baseUrl);
  return waitForAgentJob(accepted.job_id, options, baseUrl);
}

export async function runResearchWorkflow(options, baseUrl = DEFAULT_BASE_URL) {
  return postJSON(baseUrl, "/api/run", {
    mode: "daily",
    no_network: true,
    max_results: 10,
    urls: [],
    manual_evidence: [],
    ...options,
  });
}

export async function runResearchSearch(options, baseUrl = DEFAULT_BASE_URL) {
  return postJSON(baseUrl, "/api/search", {
    mode: "daily",
    no_network: true,
    max_results: 10,
    urls: [],
    ...options,
  });
}

export async function addResearchEvidence(evidence, baseUrl = DEFAULT_BASE_URL) {
  return postJSON(baseUrl, "/api/evidence", evidence);
}

export async function runResearchSelftest(baseUrl = DEFAULT_BASE_URL) {
  return postJSON(baseUrl, "/api/selftest", {});
}

export async function listResearchRuns(limit = 20, baseUrl = DEFAULT_BASE_URL) {
  return requestJSON(`${baseUrl.replace(/\/$/, "")}/api/runs?limit=${encodeURIComponent(limit)}`);
}

export async function auditResearchRun(runId, baseUrl = DEFAULT_BASE_URL) {
  return requestJSON(`${baseUrl.replace(/\/$/, "")}/api/audit?id=${encodeURIComponent(runId)}`);
}

async function postJSON(baseUrl, path, payload) {
  return requestJSON(baseUrl.replace(/\/$/, "") + path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function requestJSON(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    throw new ResearchWorkflowError("service_unavailable", `Research Workflow service is unavailable: ${error.message}`, { url });
  }
  return parseResponse(response);
}

async function parseResponse(response) {
  let data;
  try {
    data = await response.json();
  } catch (error) {
    throw new ResearchWorkflowError("invalid_response", `Invalid JSON response: ${error.message}`, { status: response.status });
  }
  if (!response.ok) {
    throw new ResearchWorkflowError("http_error", data.error || `Research Workflow request failed: ${response.status}`, { status: response.status, data });
  }
  return data;
}
