const state = {
  mode: "daily",
  sources: [],
  evidence: [],
  claims: [],
  revisionSuggestions: [],
  savedRuns: [],
  status: null,
  currentRunId: "",
  generatedDraft: "",
  pendingManualEvidence: [],
  researchBuckets: {},
  coverageGaps: [],
  providerHealth: [],
  canonicalGroups: [],
  toolMatrix: [],
  sourceQuality: null,
  evidenceQuality: null,
  claimQuality: [],
  deepenSuggestions: [],
  referenceChecks: null,
  providerPreset: "default",
  autoRoute: true,
  searchProfile: "default",
  routingPlan: null,
  routingTrace: [],
  coverageGate: null,
  providerBudget: null,
  externalAcquisition: [],
  stopReason: "",
  currentJob: null,
  jobEvents: [],
  recentJobs: [],
  selectedClaimId: "",
};

const el = {
  topic: document.querySelector("#topicInput"),
  run: document.querySelector("#runBtn"),
  deepen: document.querySelector("#deepenBtn"),
  fullRun: document.querySelector("#fullRunBtn"),
  verify: document.querySelector("#verifyBtn"),
  audit: document.querySelector("#auditBtn"),
  export: document.querySelector("#exportBtn"),
  obsidian: document.querySelector("#obsidianBtn"),
  ingest: document.querySelector("#ingestBtn"),
  addEvidence: document.querySelector("#addEvidenceBtn"),
  doctor: document.querySelector("#doctorBtn"),
  selftest: document.querySelector("#selftestBtn"),
  evals: document.querySelector("#evalsBtn"),
  readiness: document.querySelector("#readinessBtn"),
  networkToggle: document.querySelector("#networkToggle"),
  autoRouteToggle: document.querySelector("#autoRouteToggle"),
  searchProfile: document.querySelector("#searchProfile"),
  manualProviderGrid: document.querySelector("#manualProviderGrid"),
  fetchToggle: document.querySelector("#fetchToggle"),
  autoVerifyToggle: document.querySelector("#autoVerifyToggle"),
  maxResults: document.querySelector("#maxResults"),
  urls: document.querySelector("#urlInput"),
  evidenceUrl: document.querySelector("#evidenceUrl"),
  evidenceText: document.querySelector("#evidenceText"),
  notice: document.querySelector("#notice"),
  jobStatus: document.querySelector("#jobStatus"),
  jobEvents: document.querySelector("#jobEvents"),
  recentJobs: document.querySelector("#recentJobs"),
  jobsRefresh: document.querySelector("#jobsRefreshBtn"),
  jobRefresh: document.querySelector("#jobRefreshBtn"),
  jobRetry: document.querySelector("#jobRetryBtn"),
  claimDrawer: document.querySelector("#claimDrawer"),
  claimDrawerClose: document.querySelector("#claimDrawerCloseBtn"),
  claimDrawerBody: document.querySelector("#claimDrawerBody"),
  searxngDot: document.querySelector("#searxngDot"),
  searxngStatus: document.querySelector("#searxngStatus"),
  planMode: document.querySelector("#planMode"),
  planContent: document.querySelector("#planContent"),
  sourceCount: document.querySelector("#sourceCount"),
  sourceList: document.querySelector("#sourceList"),
  claimCount: document.querySelector("#claimCount"),
  claimTable: document.querySelector("#claimTable"),
  metricSources: document.querySelector("#metricSources"),
  metricEvidence: document.querySelector("#metricEvidence"),
  metricClaims: document.querySelector("#metricClaims"),
  metricOpen: document.querySelector("#metricOpen"),
  serviceStatus: document.querySelector("#serviceStatus"),
  serviceDashboard: document.querySelector("#serviceDashboard"),
  routingStatus: document.querySelector("#routingStatus"),
  routingContent: document.querySelector("#routingContent"),
  coverageCount: document.querySelector("#coverageCount"),
  coverageContent: document.querySelector("#coverageContent"),
  providerHealthCount: document.querySelector("#providerHealthCount"),
  providerHealth: document.querySelector("#providerHealth"),
  qualityStatus: document.querySelector("#qualityStatus"),
  qualityContent: document.querySelector("#qualityContent"),
  qualityReview: document.querySelector("#qualityReviewBtn"),
  claimSuggest: document.querySelector("#claimSuggestBtn"),
  referenceCheck: document.querySelector("#referenceCheckBtn"),
  canonicalCount: document.querySelector("#canonicalCount"),
  canonicalContent: document.querySelector("#canonicalContent"),
  toolMatrixCount: document.querySelector("#toolMatrixCount"),
  toolMatrix: document.querySelector("#toolMatrix"),
  savedRuns: document.querySelector("#savedRuns"),
  clearRuns: document.querySelector("#clearRunsBtn"),
  doctorSummary: document.querySelector("#doctorSummary"),
  doctorChecks: document.querySelector("#doctorChecks"),
  providerToggles: Array.from(document.querySelectorAll(".providerToggle")),
  providerPresets: Array.from(document.querySelectorAll(".preset-btn")),
  report: document.querySelector("#reportText"),
  segments: Array.from(document.querySelectorAll(".segment")),
};

const PROVIDER_PRESETS = {
  default: ["searxng", "tavily", "serpapi", "brave", "you"],
  zh: ["searxng", "metaso", "you"],
  tech: ["firecrawl_developer", "github_cli", "exa", "searxng", "brave"],
  research: ["searxng", "tavily", "you", "serpapi", "brave", "metaso", "exa", "jina"],
  verify: ["searxng", "serpapi", "brave", "metaso", "you"],
};

const ROUTING_PROFILE_BY_PRESET = {
  default: "default",
  zh: "default",
  tech: "technical_primary",
  research: "anti_seo",
  verify: "policy_primary",
};

const PROVIDER_PRESET_LABELS = {
  default: "Default",
  zh: "中文",
  tech: "Tech",
  research: "Research",
  verify: "Verify",
};

let busyTimer = null;
let jobPollTimer = null;

function setNotice(message, kind = "") {
  el.notice.textContent = message;
  el.notice.className = `notice ${kind}`.trim();
}

async function postJSON(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `Request failed: ${response.status}`);
  }
  return data;
}

async function getJSON(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `Request failed: ${response.status}`);
  }
  return data;
}

async function hydrate() {
  try {
    const [status, store, runs, jobs] = await Promise.all([
      getJSON("/api/status"),
      getJSON("/api/export"),
      getJSON("/api/runs?limit=12"),
      getJSON("/v1/jobs?limit=20"),
    ]);
    state.status = status;
    state.sources = store.data?.sources || [];
    state.evidence = store.data?.evidence || [];
    state.claims = store.data?.claims || [];
    state.revisionSuggestions = [];
    state.savedRuns = runs.runs || [];
    state.recentJobs = jobs.jobs || [];
    renderStatus();
    renderSources();
    renderClaims();
    renderCoverage();
    renderSourceQuality();
    renderProviderHealth();
    renderCanonicalGroups();
    renderToolMatrix();
    renderSavedRuns();
    renderRecentJobs();
    renderMetrics();
    renderServiceDashboard();
    await recoverLatestJob();
    runDoctor({ quiet: true });
  } catch (error) {
    setNotice(`Startup status failed: ${error.message}`, "error");
  }
}

async function runDoctor(options = {}) {
  const quiet = Boolean(options.quiet);
  if (el.doctor) {
    el.doctor.disabled = true;
  }
  if (!quiet) {
    setNotice("Running local diagnostics.");
  }
  renderDoctorLoading();
  try {
    const data = await getJSON("/api/doctor");
    renderDoctor(data);
    if (!quiet) {
      setNotice(`Doctor complete: ${data.overall}.`, data.overall === "error" ? "error" : "ok");
    }
  } catch (error) {
    renderDoctorError(error);
    if (!quiet) {
      setNotice(`Doctor failed: ${error.message}`, "error");
    }
  } finally {
    if (el.doctor) {
      el.doctor.disabled = false;
    }
  }
}

async function runSelftest() {
  if (el.selftest) {
    el.selftest.disabled = true;
  }
  setNotice("Running end-to-end selftest: pipeline, audit, history, MCP, Obsidian export.");
  renderDoctorLoading("Running local selftest...");
  try {
    const data = await postJSON("/api/selftest", {});
    renderDoctor(data);
    const runId = data.run_id ? ` Run: ${data.run_id}.` : "";
    setNotice(`Selftest complete: ${data.overall}.${runId}`, data.overall === "error" ? "error" : "ok");
    await refreshSavedRuns();
  } catch (error) {
    renderDoctorError(error);
    setNotice(`Selftest failed: ${error.message}`, "error");
  } finally {
    if (el.selftest) {
      el.selftest.disabled = false;
    }
  }
}

async function runEvals() {
  if (el.evals) {
    el.evals.disabled = true;
  }
  setNotice("Running acceptance evals: daily tools, academic methods, hallucination handling.");
  renderDoctorLoading("Running acceptance evals...");
  try {
    const data = await postJSON("/api/evals", {});
    renderEvals(data);
    setNotice(`Evals complete: ${data.overall}. ${data.case_count || 0} cases checked.`, data.overall === "error" ? "error" : "ok");
  } catch (error) {
    renderDoctorError(error);
    setNotice(`Evals failed: ${error.message}`, "error");
  } finally {
    if (el.evals) {
      el.evals.disabled = false;
    }
  }
}

async function runReadiness() {
  if (el.readiness) {
    el.readiness.disabled = true;
  }
  setNotice("Running readiness report: doctor, integrations, Web launcher, and quick gates.");
  renderDoctorLoading("Running readiness report...");
  try {
    const data = await postJSON("/api/readiness", { skip_slow: true });
    renderReadiness(data);
    const ready = data.ready ? "ready" : "not ready";
    setNotice(`Readiness ${data.overall}: ${ready}.`, data.overall === "error" ? "error" : "ok");
  } catch (error) {
    renderDoctorError(error);
    setNotice(`Readiness failed: ${error.message}`, "error");
  } finally {
    if (el.readiness) {
      el.readiness.disabled = false;
    }
  }
}

function renderDoctorLoading(message = "Checking local chain...") {
  el.doctorSummary.className = "doctor-summary";
  el.doctorSummary.textContent = message;
  el.doctorChecks.innerHTML = "";
}

function renderDoctor(data) {
  const overall = data.overall || "unknown";
  el.doctorSummary.className = `doctor-summary ${escapeAttr(overall)}`;
  el.doctorSummary.textContent = `Overall: ${overall}`;
  el.doctorChecks.innerHTML = (data.checks || [])
    .map(
      (check) => `
        <div class="doctor-check ${escapeAttr(check.status)}" title="${escapeAttr(check.message)}">
          <span>${escapeHTML(check.name)}</span>
          <strong>${escapeHTML(check.status)}</strong>
        </div>
      `
    )
    .join("");
}

function renderEvals(data) {
  const overall = data.overall || "unknown";
  el.doctorSummary.className = `doctor-summary ${escapeAttr(overall)}`;
  el.doctorSummary.textContent = `Evals: ${overall}`;
  el.doctorChecks.innerHTML = (data.cases || [])
    .map((item) => {
      const message = `${item.claim_count}/${item.expected_fact_count} claims, coverage ${item.claim_coverage}, audit ${item.audit_overall}`;
      return `
        <div class="doctor-check ${escapeAttr(item.status)}" title="${escapeAttr(message)}">
          <span>${escapeHTML(item.name)}</span>
          <strong>${escapeHTML(item.status)}</strong>
        </div>
      `;
    })
    .join("");
}

function renderReadiness(data) {
  const overall = data.overall || "unknown";
  el.doctorSummary.className = `doctor-summary ${escapeAttr(overall)}`;
  el.doctorSummary.textContent = `Ready: ${data.ready ? "true" : "false"} (${overall})`;
  el.doctorChecks.innerHTML = (data.checks || [])
    .map(
      (check) => `
        <div class="doctor-check ${escapeAttr(check.status)}" title="${escapeAttr(check.message)}">
          <span>${escapeHTML(check.name)}</span>
          <strong>${escapeHTML(check.status)}</strong>
        </div>
      `
    )
    .join("");
}

function renderDoctorError(error) {
  el.doctorSummary.className = "doctor-summary error";
  el.doctorSummary.textContent = "Overall: error";
  el.doctorChecks.innerHTML = `
    <div class="doctor-check error">
      <span>${escapeHTML(error.message)}</span>
      <strong>error</strong>
    </div>
  `;
}

function renderStatus() {
  if (!state.status) return;
  if (state.status.searxng_configured) {
    el.searxngDot.className = "dot ok";
    el.searxngStatus.textContent = `SearXNG ${state.status.resolved_searxng_url || "configured"}`;
    el.networkToggle.disabled = false;
    el.networkToggle.checked = true;
  } else {
    el.searxngDot.className = "dot muted";
    el.searxngStatus.textContent = "SearXNG not configured";
    el.networkToggle.checked = false;
    el.networkToggle.disabled = true;
  }
  const configuredProviders = state.status.available_providers || {};
  const enabledProviders = new Set(state.status.search_providers || ["searxng"]);
  el.providerToggles.forEach((toggle) => {
    const details = configuredProviders[toggle.value] || {};
    toggle.checked = enabledProviders.has(toggle.value);
    toggle.dataset.providerConfigured = String(toggle.value === "searxng" || Boolean(details.configured));
    toggle.closest("label").title = details.detail || "";
  });
  applyProviderPreset(state.providerPreset, { silent: true });
  setAutoRoute(state.autoRoute, { silent: true });
  state.providerHealth = state.providerHealth.length ? state.providerHealth : state.status.provider_health || [];
  renderProviderHealth();
}

function renderPlan(plan) {
  if (!plan) {
    el.planContent.className = "empty-state";
    el.planContent.textContent = "Run a query to generate subquestions, keywords, and source targets.";
    return;
  }
  el.planMode.textContent = plan.mode;
  el.planContent.className = "plan-list";
  el.planContent.innerHTML = `
    ${planBlock("Core question", [plan.core_question])}
    ${planBlock("Subquestions", plan.subquestions)}
    ${planBlock("Chinese keywords", plan.zh_keywords)}
    ${planBlock("English keywords", plan.en_keywords)}
    ${planBlock("Source targets", plan.source_targets)}
    ${planBlock("High-risk watchlist", plan.high_risk_watchlist)}
  `;
}

function renderRouting() {
  const plan = state.routingPlan;
  const trace = state.routingTrace || [];
  const gate = state.coverageGate;
  if (!plan) {
    el.routingStatus.textContent = state.autoRoute ? "auto" : "manual";
    el.routingContent.className = "empty-state";
    el.routingContent.textContent = "Run a query to see planned stages, provider calls, coverage checks, and the stop reason.";
    return;
  }
  const stages = plan.stages || [];
  const budget = state.providerBudget || plan.provider_budget || {};
  const observed = gate?.observed || {};
  const required = gate?.required || plan.stop_conditions || {};
  const traceByStage = new Map(trace.map((item) => [String(item.stage || "").replace(/^adaptive_/, ""), item]));
  el.routingStatus.textContent = `${plan.route_kind || "unknown"} · ${state.stopReason || "planned"}`;
  el.routingContent.className = "routing-review";
  el.routingContent.innerHTML = `
    <div class="routing-summary">
      <div><strong>${escapeHTML(plan.profile || "default")}</strong><span>profile</span></div>
      <div><strong>${escapeHTML(plan.routing_strategy || "unknown")}</strong><span>strategy</span></div>
      <div><strong>${Number(budget.max_provider_calls || 0)}</strong><span>max calls</span></div>
      <div><strong>${gate?.passed ? "passed" : "open"}</strong><span>coverage gate</span></div>
    </div>
    <div class="routing-stages">
      ${stages.length ? stages.map((stage) => {
        const execution = traceByStage.get(stage.id) || {};
        const status = execution.status || "planned";
        return `<div class="routing-stage ${escapeAttr(status)}">
          <strong>${escapeHTML(stage.id)}</strong>
          <span>${escapeHTML((stage.providers || []).join(" + ") || "external")}</span>
          <small>${escapeHTML(status)} · ${escapeHTML(execution.reason || stage.reason || "")}</small>
        </div>`;
      }).join("") : `<div class="routing-stage planned"><strong>External acquisition</strong><span>${escapeHTML((state.externalAcquisition || []).map((item) => item.adapter).join(" + ") || "none")}</span></div>`}
    </div>
    <div class="routing-gate">
      <span>Results ${Number(observed.result_count || 0)}/${Number(required.min_results || 0)}</span>
      <span>Domains ${Number(observed.domain_count || 0)}/${Number(required.min_domains || 0)}</span>
      <span>Providers ${Number(observed.provider_count || 0)}/${Number(required.min_providers || 0)}</span>
      <span>Primary ${Number(observed.primary_count || 0)}/${Number(required.min_primary || 0)}</span>
    </div>
  `;
}

function planBlock(title, items) {
  return `
    <div class="plan-block">
      <strong>${escapeHTML(title)}</strong>
      <ul>${items.map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>
    </div>
  `;
}

function renderSources() {
  el.sourceCount.textContent = String(state.sources.length);
  renderMetrics();
  if (!state.sources.length) {
    el.sourceList.className = "source-list empty-state";
    el.sourceList.textContent = "No sources yet. Ingest URLs or configure SearXNG.";
    return;
  }
  el.sourceList.className = "source-list";
  el.sourceList.innerHTML = state.sources
    .map((source) => {
      const sourceEvidence = state.evidence.filter((item) => item.source_id === source.id);
      const evidenceCount = sourceEvidence.length;
      return `
        <article class="source-item">
          <a class="source-title" href="${escapeAttr(source.url)}" target="_blank" rel="noreferrer">${escapeHTML(source.title || source.url)}</a>
          <div class="source-meta">
            <span>${escapeHTML(source.source_type || "unknown")}</span>
            <span>score ${Number(source.quality_score || 0)}</span>
            <span>${escapeHTML(source.publisher || "unknown publisher")}</span>
            ${source.published_at ? `<span>${escapeHTML(source.published_at)}</span>` : ""}
            <span>${evidenceCount} evidence</span>
            ${source.duplicate_count > 1 ? `<span>${source.duplicate_count} merged</span>` : ""}
          </div>
          ${evidenceTypeHTML(sourceEvidence)}
          ${sourceProvenanceHTML(source)}
          <div class="source-snippet">${escapeHTML(source.snippet || "Manual source recorded for evidence review.")}</div>
          ${source.quality_reasons?.length ? `<div class="source-snippet">${escapeHTML(source.quality_reasons.slice(0, 3).join(" · "))}</div>` : ""}
        </article>
      `;
    })
    .join("");
}

function evidenceTypeHTML(evidence) {
  if (!evidence.length) return "";
  const counts = new Map();
  evidence.forEach((item) => {
    const type = item.source_quote_type || "unknown";
    const method = item.extraction_method || "unknown";
    const key = `${type} · ${method}`;
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  return `
    <div class="evidence-types">
      ${Array.from(counts.entries())
        .map(([label, count]) => `<span title="${escapeAttr(label)}">${escapeHTML(label)} (${count})</span>`)
        .join("")}
    </div>
  `;
}

function sourceProvenanceHTML(source) {
  const buckets = Array.isArray(source.search_buckets) ? source.search_buckets.slice(0, 3).join(" · ") : "";
  const query = Array.isArray(source.search_queries) ? source.search_queries[0] : "";
  if (!buckets && !query) return "";
  return `
    <div class="source-provenance">
      ${buckets ? `<span>${escapeHTML(buckets)}</span>` : ""}
      ${query ? `<span title="${escapeAttr(query)}">${escapeHTML(query)}</span>` : ""}
    </div>
  `;
}

function renderSourceQuality() {
  const sourceQuality = state.sourceQuality;
  const evidenceQuality = state.evidenceQuality;
  const suggestions = state.deepenSuggestions || [];
  const referenceChecks = state.referenceChecks || null;
  if (!sourceQuality && !evidenceQuality && !suggestions.length && !referenceChecks) {
    el.qualityStatus.textContent = "unknown";
    el.qualityContent.className = "empty-state";
    el.qualityContent.textContent = "Run Search or restore a run, then use Quality, Deepen, or Refs for review.";
    return;
  }
  el.qualityStatus.textContent = evidenceQuality?.overall || sourceQuality?.overall || "review";
  el.qualityContent.className = "quality-review";
  el.qualityContent.innerHTML = `
    ${renderSourceQualityBlock(sourceQuality)}
    ${renderEvidenceQualityBlock(evidenceQuality)}
    ${renderDeepenSuggestionBlock(suggestions)}
    ${renderReferenceCheckBlock(referenceChecks)}
  `;
}

function renderSourceQualityBlock(quality) {
  if (!quality) {
    return `<div class="quality-section empty-state">Source quality not available for this run.</div>`;
  }
  const warnings = quality.warnings || [];
  return `
    <div class="quality-section">
      <div class="quality-section-title"><strong>Source Quality</strong><span>${escapeHTML(quality.overall || "unknown")}</span></div>
      <div class="quality-grid compact">
        <div class="quality-tile"><strong>${Number(quality.average_score || 0)}</strong><span>avg score</span></div>
        <div class="quality-tile"><strong>${Number(quality.primary_source_count || 0)}/${Number(quality.source_count || 0)}</strong><span>primary</span></div>
        <div class="quality-tile"><strong>${Number(quality.sources_with_evidence || 0)}/${Number(quality.source_count || 0)}</strong><span>with evidence</span></div>
        <div class="quality-tile"><strong>${Number(quality.low_quality_count || 0)}</strong><span>low score</span></div>
      </div>
      ${warnings.length ? `<ul class="quality-list">${warnings.slice(0, 4).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>` : ""}
    </div>
  `;
}

function renderEvidenceQualityBlock(quality) {
  if (!quality) {
    return `<div class="quality-section empty-state">Run Quality to review citation coverage and evidence strength.</div>`;
  }
  const recommendations = quality.recommendations || [];
  return `
    <div class="quality-section">
      <div class="quality-section-title"><strong>Conclusion Evidence</strong><span>${escapeHTML(quality.overall || "unknown")}</span></div>
      <div class="quality-grid compact">
        <div class="quality-tile"><strong>${Number(quality.finding_citation_coverage || 0).toFixed(2)}</strong><span>finding coverage</span></div>
        <div class="quality-tile"><strong>${Number(quality.weak_claims || 0)}</strong><span>weak claims</span></div>
        <div class="quality-tile"><strong>${Number(quality.single_source_claims || 0)}</strong><span>single source</span></div>
        <div class="quality-tile"><strong>${Number(quality.weak_evidence?.length || 0)}</strong><span>weak evidence</span></div>
      </div>
      ${recommendations.length ? `<ul class="quality-list">${recommendations.slice(0, 4).map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>` : ""}
    </div>
  `;
}

function renderDeepenSuggestionBlock(suggestions) {
  if (!suggestions.length) {
    return `<div class="quality-section empty-state">No claim deepen suggestions loaded.</div>`;
  }
  return `
    <div class="quality-section">
      <div class="quality-section-title"><strong>Claim Deepen Suggestions</strong><span>${suggestions.length}</span></div>
      <ul class="quality-list">
        ${suggestions.slice(0, 4).map((item) => `<li><strong>${escapeHTML(item.verdict || "review")}</strong>: ${escapeHTML(item.claim || "")}<br><small>${escapeHTML((item.next_search_queries || [])[0] || item.missing_evidence_reason || "")}</small></li>`).join("")}
      </ul>
    </div>
  `;
}

function renderReferenceCheckBlock(result) {
  if (!result) {
    return `<div class="quality-section empty-state">No reference check loaded.</div>`;
  }
  const summary = result.validation_summary || {};
  const counts = summary.counts || {};
  return `
    <div class="quality-section">
      <div class="quality-section-title"><strong>References Check</strong><span>${escapeHTML(summary.overall || "unknown")}</span></div>
      <div class="quality-grid compact">
        <div class="quality-tile"><strong>${Number(counts.valid || 0)}</strong><span>valid</span></div>
        <div class="quality-tile"><strong>${Number(counts.partial || 0)}</strong><span>partial</span></div>
        <div class="quality-tile"><strong>${Number(counts.not_found || 0)}</strong><span>not found</span></div>
        <div class="quality-tile"><strong>${Number(counts.metadata_mismatch || 0)}</strong><span>mismatch</span></div>
      </div>
    </div>
  `;
}

function renderProviderHealth() {
  const rows = state.providerHealth || [];
  el.providerHealthCount.textContent = `${rows.length} providers`;
  if (!rows.length) {
    el.providerHealth.className = "provider-health empty-state";
    el.providerHealth.textContent = "Run Search to see provider quota, timeout, and cost status.";
    return;
  }
  el.providerHealth.className = "provider-health";
  el.providerHealth.innerHTML = rows
    .map((item) => {
      const overall = item.overall || (item.configured ? "configured" : "missing");
      const label = providerStatusLabel(overall);
      const counts =
        item.ok_count !== undefined
          ? providerHealthCounts(item)
          : item.configured
            ? "configured"
            : "missing key";
      const warnings = Array.isArray(item.warnings) ? item.warnings.slice(0, 2) : [];
      const warningHTML = warnings.length
        ? `<ul class="provider-warnings">${warnings.map((warning) => `<li>${escapeHTML(warning)}</li>`).join("")}</ul>`
        : "";
      return `
        <div class="provider-card ${escapeAttr(overall)}" title="${escapeAttr(item.limit_note || item.detail || "")}">
          <strong>${escapeHTML(item.provider)}</strong>
          <span>${escapeHTML(label)} · ${escapeHTML(item.cost_tier || "unknown")}</span>
          <small>${escapeHTML(counts)}</small>
          ${warningHTML}
        </div>
      `;
    })
    .join("");
}

function providerHealthCounts(item) {
  const parts = [
    `${item.ok_count || 0} ok`,
    `${item.limited_count || 0} limited`,
    `${item.not_configured_count || 0} not configured`,
    `${item.cooldown_count || 0} cooldown`,
    `${item.auth_error_count || 0} auth`,
    `${item.network_error_count || 0} network`,
    `${item.error_count || 0} other errors`,
    `${item.recent_status || "unknown"} recent`,
    `${item.rank_reason || "no rank note"}`,
    `${formatSeconds(item.avg_seconds || 0)} avg`,
    `${formatSeconds(item.max_seconds || 0)} max`,
  ];
  return parts.join(" · ");
}

function formatSeconds(value) {
  const seconds = Number(value) || 0;
  return `${seconds.toFixed(seconds < 10 ? 2 : 1)}s`;
}

function providerStatusLabel(status) {
  const labels = {
    key_present: "key present",
    configured: "key present",
    not_configured: "not configured",
    missing: "not configured",
    ok: "working",
    partial: "partial",
    empty: "no results",
    limited: "quota/rate limited",
    auth_error: "auth/key error",
    network_error: "network error",
    cooldown: "cooldown",
    error: "error",
  };
  return labels[status] || status || "unknown";
}

function renderCanonicalGroups() {
  const groups = state.canonicalGroups || [];
  el.canonicalCount.textContent = `${groups.length} groups`;
  if (!groups.length) {
    el.canonicalContent.className = "empty-state";
    el.canonicalContent.textContent = "No duplicate/canonical groups detected yet.";
    return;
  }
  el.canonicalContent.className = "canonical-list";
  el.canonicalContent.innerHTML = groups
    .map(
      (group) => `
        <div class="canonical-item">
          <a href="${escapeAttr(group.canonical_url)}" target="_blank" rel="noreferrer">${escapeHTML(group.title || group.canonical_url)}</a>
          <span>${Number(group.duplicate_count || 0)} URL hits merged · ${escapeHTML(group.publisher || "unknown")}</span>
        </div>
      `
    )
    .join("");
}

function renderToolMatrix() {
  const rows = state.toolMatrix || [];
  el.toolMatrixCount.textContent = `${rows.length} tools`;
  if (!rows.length) {
    el.toolMatrix.className = "empty-state";
    el.toolMatrix.textContent = "No tool/project matrix inferred yet.";
    return;
  }
  el.toolMatrix.className = "tool-table-wrap";
  el.toolMatrix.innerHTML = `
    <table class="tool-table">
      <thead>
        <tr>
          <th>Tool / Project</th>
          <th>Type</th>
          <th>Open</th>
          <th>Maintenance</th>
          <th>Use</th>
          <th>Evidence</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map((item) => {
            const maintenance = String(item.maintenance_status || "unknown");
            const maintenanceClass = maintenance.split(/\s+|\(/)[0] || "unknown";
            return `
              <tr>
                <td>${escapeHTML(item.name || "")}</td>
                <td>${escapeHTML(item.type || "")}</td>
                <td>${escapeHTML(item.open_source || "unknown")}</td>
                <td><span class="maintenance ${escapeAttr(maintenanceClass)}">${escapeHTML(maintenance)}</span></td>
                <td>${escapeHTML(item.recommendation || item.scenario || "")}</td>
                <td><a href="${escapeAttr(item.evidence_url || "#")}" target="_blank" rel="noreferrer">source</a></td>
              </tr>
            `;
          })
          .join("")}
      </tbody>
    </table>
  `;
}

function renderCoverage() {
  const buckets = state.researchBuckets || {};
  const entries = Object.entries(buckets).filter(([, sources]) => Array.isArray(sources) && sources.length);
  const gaps = state.coverageGaps || [];
  el.coverageCount.textContent = `${entries.length} buckets`;
  if (!entries.length && !gaps.length) {
    el.coverageContent.className = "empty-state";
    el.coverageContent.textContent = "Run Search to see source buckets and missing research lanes.";
    return;
  }
  el.coverageContent.className = "coverage-map";
  const bucketHTML = entries
    .map(
      ([bucket, sources]) => `
        <div class="coverage-bucket">
          <strong>${escapeHTML(bucket)}</strong>
          <span>${sources.length} source${sources.length === 1 ? "" : "s"}</span>
        </div>
      `
    )
    .join("");
  const gapHTML = gaps.length
    ? `
      <div class="coverage-gaps">
        <strong>Gaps</strong>
        <ul>${gaps.map((gap) => `<li>${escapeHTML(gap)}</li>`).join("")}</ul>
      </div>
    `
    : `<div class="coverage-gaps ok"><strong>Gaps</strong><span>Primary buckets have candidate sources.</span></div>`;
  el.coverageContent.innerHTML = `${bucketHTML}${gapHTML}`;
}

function renderSearchPending() {
  el.planMode.textContent = state.mode;
  el.planContent.className = "empty-state";
  el.planContent.textContent = "Planning query lanes and seed coverage.";
  el.sourceCount.textContent = "...";
  el.sourceList.className = "source-list empty-state";
  el.sourceList.textContent = "Searching providers. If live search returns empty, curated seed URLs will be used as candidates.";
  el.claimCount.textContent = "0";
  el.claimTable.innerHTML = `<tr><td colspan="7" class="empty-cell">Search-only run in progress. Claims will stay empty until verification.</td></tr>`;
  el.coverageCount.textContent = "...";
  el.coverageContent.className = "empty-state";
  el.coverageContent.textContent = "Coverage map will update after sources are selected.";
  el.qualityStatus.textContent = "...";
  el.qualityContent.className = "empty-state";
  el.qualityContent.textContent = "Scoring selected sources and evidence coverage.";
  el.providerHealthCount.textContent = "...";
  el.providerHealth.className = "provider-health empty-state";
  el.providerHealth.textContent = "Providers are running with per-provider timeout and quota fallback.";
  el.canonicalCount.textContent = "...";
  el.canonicalContent.className = "empty-state";
  el.canonicalContent.textContent = "Canonical merge will run after provider results return.";
  el.toolMatrixCount.textContent = "...";
  el.toolMatrix.className = "empty-state";
  el.toolMatrix.textContent = "Tool matrix will update after canonical source selection.";
  el.routingStatus.textContent = state.autoRoute ? "planning" : "manual";
  el.routingContent.className = "empty-state";
  el.routingContent.textContent = state.autoRoute
    ? "Classifying intent and planning the first provider stage."
    : "Running the exact providers selected in manual mode.";
}

function renderClaims() {
  el.claimCount.textContent = String(state.claims.length);
  renderMetrics();
  if (!state.claims.length) {
    el.claimTable.innerHTML = `<tr><td colspan="7" class="empty-cell">Paste or generate a report, then verify.</td></tr>`;
    return;
  }
  const revisionsByClaimId = new Map((state.revisionSuggestions || []).map((item) => [item.claim_id, item]));
  const revisionsByClaimText = new Map((state.revisionSuggestions || []).map((item) => [item.claim_text, item]));
  el.claimTable.innerHTML = state.claims
    .map((claim) => {
      const confidence = Number(claim.confidence || 0);
      const percent = Math.round(confidence * 100);
      const verdict = claim.verdict || "not_found";
      const risk = claim.risk_level || "normal";
      const evidenceStats = evidenceStatsForClaim(claim);
      const quality = qualityForClaim(claim);
      const evidenceClass = quality?.evidence_strength === "weak" || (risk === "high" && evidenceStats.sources < 2) ? "weak" : "ok";
      const revision = revisionsByClaimId.get(claim.id) || revisionsByClaimText.get(claim.text);
      const revisionAction = revision?.action || "none";
      return `
        <tr>
          <td>${escapeHTML(claim.text)}</td>
          <td><span class="risk-badge ${escapeAttr(risk)}">${escapeHTML(risk)}</span></td>
          <td>
            <span class="evidence-badge ${evidenceClass}" title="${escapeAttr(evidenceStats.title)}">
              <strong>${evidenceStats.refs}</strong> refs<br><strong>${evidenceStats.sources}</strong> src
            </span>
            ${quality ? `<small class="quality-mini">${escapeHTML(quality.evidence_strength || "unknown")} · cov ${Number(quality.citation_coverage || 0).toFixed(2)}</small>` : ""}
          </td>
          <td><span class="verdict ${escapeAttr(verdict)}">${escapeHTML(verdict)}</span></td>
          <td>
            <div class="revision-cell ${escapeAttr(revisionAction)}">
              <strong>${escapeHTML(revisionAction)}</strong>
              ${revision ? `<small title="${escapeAttr(revision.suggested_replacement || "")}">${escapeHTML(revision.failure_reason || revision.suggested_replacement || "")}</small>` : "<small>No revision needed.</small>"}
            </div>
          </td>
          <td>
            <div class="confidence">
              <span>${percent}%</span>
              <div class="bar"><span style="width:${percent}%"></span></div>
            </div>
          </td>
          <td>
            <div class="claim-actions">
              <button class="mini-btn claim-detail-btn" type="button" data-claim-id="${escapeAttr(claim.id)}">Details</button>
              <button class="mini-btn claim-deepen-btn" type="button" data-claim-id="${escapeAttr(claim.id)}">Deepen</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
}

function openClaimDrawer(claimId) {
  const claim = state.claims.find((item) => item.id === claimId);
  if (!claim || !el.claimDrawer || !el.claimDrawerBody) return;
  state.selectedClaimId = claimId;
  const revision = revisionForClaim(claim);
  const evidenceItems = evidenceForClaim(claim);
  const reason = claimReason(claim, evidenceItems, revision);
  const quality = qualityForClaim(claim);
  const suggestion = deepenSuggestionForClaim(claim);
  el.claimDrawerBody.innerHTML = `
    <section class="claim-drawer-section">
      <span class="drawer-label">Claim</span>
      <h3>${escapeHTML(claim.text || "")}</h3>
      <div class="drawer-badges">
        <span class="risk-badge ${escapeAttr(claim.risk_level || "normal")}">${escapeHTML(claim.risk_level || "normal")}</span>
        <span class="verdict ${escapeAttr(claim.verdict || "not_found")}">${escapeHTML(claim.verdict || "not_found")}</span>
        <span class="tiny-chip">${Math.round(Number(claim.confidence || 0) * 100)}%</span>
      </div>
    </section>
    <section class="claim-drawer-section">
      <span class="drawer-label">Reason</span>
      <p>${escapeHTML(reason)}</p>
    </section>
    <section class="claim-drawer-section">
      <span class="drawer-label">Evidence Quality</span>
      ${quality ? renderClaimQualityDetail(quality) : `<div class="empty-state">Run Quality to calculate citation coverage, source diversity, and evidence strength.</div>`}
    </section>
    <section class="claim-drawer-section">
      <span class="drawer-label">Deepen Suggestion</span>
      ${suggestion ? renderClaimDeepenSuggestion(suggestion) : `<div class="empty-state">Run Deepen to generate follow-up queries without network search.</div>`}
    </section>
    <section class="claim-drawer-section">
      <div class="drawer-title-row">
        <span class="drawer-label">Evidence</span>
        <button class="mini-btn claim-deepen-btn" type="button" data-claim-id="${escapeAttr(claim.id)}">Deepen this claim</button>
      </div>
      ${evidenceItems.length ? evidenceItems.map(renderClaimEvidence).join("") : `<div class="empty-state">No evidence is bound to this claim yet.</div>`}
    </section>
    ${revision ? `
      <section class="claim-drawer-section">
        <span class="drawer-label">Revision</span>
        <p><strong>${escapeHTML(revision.action || "review")}</strong>: ${escapeHTML(revision.failure_reason || "")}</p>
        <p>${escapeHTML(revision.suggested_replacement || "")}</p>
      </section>
    ` : ""}
  `;
  el.claimDrawer.hidden = false;
  el.claimDrawer.classList.add("open");
}

function closeClaimDrawer() {
  state.selectedClaimId = "";
  if (!el.claimDrawer) return;
  el.claimDrawer.classList.remove("open");
  el.claimDrawer.hidden = true;
}

function revisionForClaim(claim) {
  return (state.revisionSuggestions || []).find((item) => item.claim_id === claim.id || item.claim_text === claim.text) || null;
}

function evidenceForClaim(claim) {
  const ids = new Set(Array.isArray(claim.evidence_ids) ? claim.evidence_ids : []);
  return state.evidence.filter((item) => ids.has(item.id));
}

function qualityForClaim(claim) {
  return (state.claimQuality || []).find((item) => item.claim_id === claim.id || item.claim === claim.text) || null;
}

function renderClaimQualityDetail(quality) {
  const weakReasons = quality.weak_reasons || [];
  return `
    <div class="claim-quality-detail">
      <span><strong>${Number(quality.citation_coverage || 0).toFixed(2)}</strong> citation coverage</span>
      <span><strong>${Number(quality.source_diversity || 0)}</strong> source diversity</span>
      <span><strong>${escapeHTML(quality.evidence_strength || "unknown")}</strong> strength</span>
      ${weakReasons.length ? `<ul>${weakReasons.map((item) => `<li>${escapeHTML(item)}</li>`).join("")}</ul>` : `<small>No weak reasons recorded.</small>`}
    </div>
  `;
}

function deepenSuggestionForClaim(claim) {
  return (state.deepenSuggestions || []).find((item) => item.claim_id === claim.id || item.claim === claim.text) || null;
}

function renderClaimDeepenSuggestion(suggestion) {
  const queries = suggestion.next_search_queries || [];
  return `
    <div class="claim-quality-detail">
      <span><strong>${escapeHTML(suggestion.verdict || "review")}</strong> verdict</span>
      <span>${escapeHTML(suggestion.missing_evidence_reason || "more evidence needed")}</span>
      ${queries.length ? `<ul>${queries.slice(0, 5).map((query) => `<li>${escapeHTML(query)}</li>`).join("")}</ul>` : `<small>No query variants recorded.</small>`}
    </div>
  `;
}

function sourceForEvidence(evidence) {
  return state.sources.find((item) => item.id === evidence.source_id) || null;
}

function claimReason(claim, evidenceItems, revision) {
  const verdict = claim.verdict || "not_found";
  const sourceIds = new Set(evidenceItems.map((item) => item.source_id).filter(Boolean));
  if (revision?.failure_reason) return revision.failure_reason;
  if (verdict === "supported") return `Supported by ${evidenceItems.length} evidence item(s) from ${sourceIds.size || evidenceItems.length} source(s).`;
  if (verdict === "partial") return "Only partial or insufficiently independent evidence is available.";
  if (verdict === "contradicted") return "Available evidence appears to contradict the claim.";
  return "No local evidence met the verification threshold.";
}

function renderClaimEvidence(evidence) {
  const source = sourceForEvidence(evidence);
  const sourceLabel = source?.title || source?.publisher || source?.url || evidence.source_id || "unknown source";
  const sourceUrl = source?.url || "";
  const meta = [
    evidence.source_quote_type || "",
    evidence.extraction_method || "",
    evidence.support_direction || "",
    evidence.confidence !== undefined ? `confidence ${evidence.confidence}` : "",
  ].filter(Boolean).join(" · ");
  return `
    <article class="claim-evidence-card">
      <blockquote>${escapeHTML(evidence.quote_or_paraphrase || "")}</blockquote>
      <div>
        ${sourceUrl ? `<a href="${escapeAttr(sourceUrl)}" target="_blank" rel="noreferrer">${escapeHTML(sourceLabel)}</a>` : `<strong>${escapeHTML(sourceLabel)}</strong>`}
        <small>${escapeHTML(meta || "evidence")}</small>
      </div>
    </article>
  `;
}

async function deepenClaim(claimId) {
  const claim = state.claims.find((item) => item.id === claimId);
  if (!claim) return;
  state.selectedClaimId = claimId;
  await reviewDeepenSuggestions();
  if (state.selectedClaimId) {
    openClaimDrawer(state.selectedClaimId);
  }
}

function evidenceStatsForClaim(claim) {
  const ids = Array.isArray(claim.evidence_ids) ? claim.evidence_ids : [];
  const evidenceById = new Map(state.evidence.map((item) => [item.id, item]));
  const sourceIds = new Set();
  ids.forEach((id) => {
    const item = evidenceById.get(id);
    if (item?.source_id) {
      sourceIds.add(item.source_id);
    }
  });
  const title =
    (claim.risk_level || "normal") === "high"
      ? "High-risk claims require at least two independent source IDs."
      : "Evidence references bound to this claim.";
  return {
    refs: ids.length,
    sources: sourceIds.size || ids.length,
    title,
  };
}

function renderMetrics() {
  const openClaims = state.claims.filter((claim) => claim.verdict === "not_found" || claim.verdict === "partial").length;
  el.metricSources.textContent = String(state.sources.length);
  el.metricEvidence.textContent = String(state.evidence.length);
  el.metricClaims.textContent = String(state.claims.length);
  el.metricOpen.textContent = String(openClaims);
}

function renderServiceDashboard() {
  if (!state.status) {
    el.serviceStatus.textContent = "unknown";
    el.serviceDashboard.className = "service-dashboard empty-state";
    el.serviceDashboard.textContent = "Local service status is not loaded.";
    return;
  }
  const providers = state.status.provider_health || state.providerHealth || [];
  const configured = providers.filter((item) => item.configured || item.overall === "key_present").length;
  const limited = providers.filter((item) => ["limited", "auth_error", "network_error", "error"].includes(item.overall)).length;
  const latestRun = state.savedRuns[0] || null;
  const localUrl = window.location.origin || "local";
  const reader = state.status.reader || {};
  const readerProvider = reader.provider || state.status.reader_provider || "local";
  const readerStatus = reader.status || "unknown";
  const readerMeta = [
    reader.version ? `v${reader.version}` : "",
    reader.available === false ? "unavailable" : reader.configured === false ? "not configured" : "",
  ].filter(Boolean).join(" · ");
  const readerDetail = [
    reader.message || "",
    reader.path || state.status.reader_path || "",
    reader.module_path || "",
  ].filter(Boolean).join(" · ");
  el.serviceStatus.textContent = "online";
  el.serviceDashboard.className = "service-dashboard";
  el.serviceDashboard.innerHTML = `
    ${serviceTile("URL", localUrl)}
    ${serviceTile("PID", state.status.pid || "unknown")}
    ${serviceTile("Data dir", state.status.data_dir || "unknown")}
    ${serviceTile("Runs", state.status.run_count || 0)}
    ${serviceTile("Providers", `${configured}/${providers.length || 0} configured${limited ? ` · ${limited} limited/error` : ""}`)}
    ${serviceTile("Reader", `${readerProvider} · ${readerStatus}${readerMeta ? ` · ${readerMeta}` : ""}`, readerDetail)}
    ${serviceTile("Latest", latestRun ? `${latestRun.topic || latestRun.id} · ${latestRun.created_at || ""}` : "none")}
  `;
}

function serviceTile(label, value, detail = "") {
  return `
    <div class="service-tile" title="${escapeAttr(detail || value)}">
      <span>${escapeHTML(label)}</span>
      <strong>${escapeHTML(value)}</strong>
      ${detail ? `<small>${escapeHTML(detail)}</small>` : ""}
    </div>
  `;
}

function renderJobProgress() {
  const job = state.currentJob;
  const events = state.jobEvents || [];
  if (!job) {
    el.jobStatus.textContent = "idle";
    el.jobRefresh.disabled = true;
    el.jobRetry.disabled = true;
    el.jobEvents.className = "job-events empty-state";
    el.jobEvents.textContent = "No active job yet.";
    return;
  }
  el.jobStatus.textContent = `${job.status || "unknown"} · ${job.id || ""}`;
  const hasRealJobId = Boolean(job.id && job.id !== "pending");
  const isTerminal = ["failed", "succeeded"].includes(String(job.status || "").toLowerCase());
  el.jobRefresh.disabled = !hasRealJobId;
  el.jobRetry.disabled = !hasRealJobId || !isTerminal;
  el.jobEvents.className = "job-events";
  el.jobEvents.innerHTML = events.length
    ? events
        .map((event) => {
          const detail = jobEventDetail(event);
          return `
            <div class="job-event ${escapeAttr(event.status || "")}">
              <span>${escapeHTML(event.stage || event.type || "stage")}</span>
              <strong>${escapeHTML(event.status || "unknown")}</strong>
              <small>${escapeHTML(event.message || "")}</small>
              ${detail ? `<em>${escapeHTML(detail)}</em>` : ""}
            </div>
          `;
        })
        .join("")
    : `<div class="job-event running"><span>${escapeHTML(job.stage || "queued")}</span><strong>${escapeHTML(job.status || "pending")}</strong><small>Waiting for stage events.</small></div>`;
}

function jobEventDetail(event) {
  const details = event.details || {};
  const parts = [];
  if (details.provider) parts.push(`provider ${details.provider}`);
  if (details.source_count !== undefined) parts.push(`${details.source_count} sources`);
  if (details.confidence !== undefined) parts.push(`confidence ${details.confidence}`);
  if (details.verdict) parts.push(`verdict ${details.verdict}`);
  if (details.error) parts.push(String(details.error));
  if (details.url) parts.push(String(details.url));
  if (details.query) parts.push(`query: ${details.query}`);
  if (details.age_seconds !== undefined) parts.push(`age ${formatDuration(details.age_seconds)}`);
  return parts.filter(Boolean).join(" · ");
}

function renderRecentJobs() {
  const jobs = state.recentJobs || [];
  if (!jobs.length) {
    el.recentJobs.className = "recent-jobs empty-state";
    el.recentJobs.textContent = "No recent jobs.";
    return;
  }
  el.recentJobs.className = "recent-jobs";
  el.recentJobs.innerHTML = jobs
    .map((job) => {
      const runId = job.result?.run_id || "";
      const active = state.currentJob?.id === job.id ? " active" : "";
      const loadButton = runId ? `<button class="job-load mini-btn" type="button" data-run-id="${escapeAttr(runId)}">Load</button>` : "";
      const errorText = job.error ? ` · ${job.error}` : "";
      return `
        <button class="recent-job${active}" type="button" data-job-id="${escapeAttr(job.id)}">
          <strong>${escapeHTML(job.kind || "job")} · ${escapeHTML(job.status || "unknown")}</strong>
          <span>${escapeHTML(job.stage || "queued")} · ${escapeHTML(job.updated_at || job.created_at || "")}${escapeHTML(errorText)}</span>
          <small>${escapeHTML(job.id || "")}${runId ? ` · ${escapeHTML(runId)}` : ""}</small>
        </button>
        ${loadButton}
      `;
    })
    .join("");
  el.recentJobs.querySelectorAll(".recent-job").forEach((button) => {
    button.addEventListener("click", () => selectJob(button.dataset.jobId));
  });
  el.recentJobs.querySelectorAll(".job-load").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      restoreRun(button.dataset.runId);
    });
  });
}

async function refreshRecentJobs(options = {}) {
  try {
    const data = await getJSON("/v1/jobs?limit=20");
    state.recentJobs = data.jobs || [];
    renderRecentJobs();
    if (!options.silent) {
      setNotice(`Loaded ${state.recentJobs.length} recent jobs.`, "ok");
    }
    return state.recentJobs;
  } catch (error) {
    setNotice(`Job list failed: ${error.message}`, "error");
    return [];
  }
}

function upsertRecentJob(job) {
  if (!job?.id) return;
  const existing = state.recentJobs || [];
  const index = existing.findIndex((item) => item.id === job.id);
  if (index >= 0) {
    state.recentJobs = existing.map((item, itemIndex) => (itemIndex === index ? { ...item, ...job } : item));
  } else {
    state.recentJobs = [job, ...existing].slice(0, 20);
  }
}

async function selectJob(jobId, options = {}) {
  if (!jobId) return;
  stopJobPolling();
  try {
    const [job, events] = await Promise.all([getJSON(`/v1/jobs/${encodeURIComponent(jobId)}`), getJSON(`/v1/jobs/${encodeURIComponent(jobId)}/events`)]);
    state.currentJob = job;
    state.jobEvents = events.events || [];
    upsertRecentJob(job);
    renderJobProgress();
    renderRecentJobs();
    const status = String(job.status || "").toLowerCase();
    if (["pending", "running"].includes(status) && options.poll !== false) {
      startJobPolling(job.id, { completionLabel: `${job.kind || "Job"}` });
      setNotice(`Resumed polling ${job.id}.`, "working");
      return job;
    }
    if (status === "failed") {
      setNotice(`Selected failed job ${job.id}. Retry is available.`, "error");
    } else if (status === "succeeded" && job.result?.run_id && options.loadResult) {
      await loadCompletedJob(job, { completionLabel: `${job.kind || "Job"}` });
    } else if (!options.silent) {
      setNotice(`Selected job ${job.id}: ${job.status || "unknown"}.`, "ok");
    }
    return job;
  } catch (error) {
    setNotice(`Job load failed: ${error.message}`, "error");
    return null;
  }
}

async function recoverLatestJob() {
  const latest = (state.recentJobs || [])[0];
  if (!latest) return;
  const status = String(latest.status || "").toLowerCase();
  if (["pending", "running"].includes(status)) {
    await selectJob(latest.id, { silent: true, poll: true });
  } else if (status === "failed") {
    await selectJob(latest.id, { silent: true, poll: false });
  }
}

async function refreshCurrentJob(options = {}) {
  const jobId = state.currentJob?.id;
  if (!jobId || jobId === "pending") {
    setNotice("No tracked job to refresh.", "warn");
    return;
  }
  try {
    const [job, events] = await Promise.all([getJSON(`/v1/jobs/${encodeURIComponent(jobId)}`), getJSON(`/v1/jobs/${encodeURIComponent(jobId)}/events`)]);
    state.currentJob = job;
    state.jobEvents = events.events || [];
    upsertRecentJob(job);
    renderJobProgress();
    renderRecentJobs();
    if (!options.silent) {
      setNotice(`Job ${jobId} refreshed: ${job.status || "unknown"} at ${job.stage || "unknown"}.`, "ok");
    }
    return job;
  } catch (error) {
    setNotice(error.message, "error");
    return null;
  }
}

async function retryCurrentJob() {
  const jobId = state.currentJob?.id;
  if (!jobId || jobId === "pending") {
    setNotice("No tracked job to retry.", "warn");
    return;
  }
  if (!["failed", "succeeded"].includes(String(state.currentJob?.status || "").toLowerCase())) {
    setNotice("Only completed or failed jobs can be retried.", "warn");
    return;
  }
  setBusy(true, el.jobRetry, "Retrying...");
  try {
    const data = await postJSON(`/v1/jobs/${encodeURIComponent(jobId)}/retry`, { background: true });
    state.currentJob = data.job || { id: data.job_id, status: data.job_status || "pending", stage: "queued" };
    state.jobEvents = data.job_events || [];
    renderJobProgress();
    await refreshRecentJobs({ silent: true });
    setNotice(`Retry queued as ${data.job_id}.`, "ok");
    startJobPolling(data.job_id, { completionLabel: "Retry" });
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(false);
  }
}

function stopJobPolling() {
  if (jobPollTimer) {
    clearInterval(jobPollTimer);
    jobPollTimer = null;
  }
}

function startJobPolling(jobId, options = {}) {
  if (!jobId) return;
  stopJobPolling();
  pollJob(jobId, options);
  jobPollTimer = setInterval(() => pollJob(jobId, options), Number(options.intervalMs || 1500));
}

async function pollJob(jobId, options = {}) {
  try {
    const [job, events] = await Promise.all([getJSON(`/v1/jobs/${encodeURIComponent(jobId)}`), getJSON(`/v1/jobs/${encodeURIComponent(jobId)}/events`)]);
    state.currentJob = job;
    state.jobEvents = events.events || [];
    upsertRecentJob(job);
    renderJobProgress();
    renderRecentJobs();
    const status = String(job.status || "").toLowerCase();
    if (status === "succeeded") {
      stopJobPolling();
      await loadCompletedJob(job, options);
      return;
    }
    if (status === "failed") {
      stopJobPolling();
      setNotice(`Job failed at ${job.stage || "unknown"}: ${job.error || "check job events"}`, "error");
    }
  } catch (error) {
    stopJobPolling();
    setNotice(`Job polling failed: ${error.message}`, "error");
  }
}

async function loadCompletedJob(job, options = {}) {
  const runId = job?.result?.run_id || "";
  const label = options.completionLabel || "Job";
  if (runId) {
    await restoreRun(runId, { silent: true });
    state.currentJob = job;
    renderJobProgress();
    await refreshStatus();
    await refreshSavedRuns();
    await refreshRecentJobs({ silent: true });
    setNotice(`${label} complete: restored run ${runId}.`, "ok");
    return;
  }
  setNotice(`${label} complete. No saved run was attached to this job.`, "ok");
}

async function submitBackgroundJob(kind, payload, options = {}) {
  const activeButton = options.activeButton || null;
  const busyText = options.busyText || "Starting...";
  setBusy(true, activeButton, busyText);
  try {
    const data = await postJSON("/v1/jobs", { kind, payload, background: true });
    state.currentJob = data.job || { id: data.job_id, status: data.job_status || "pending", stage: "queued" };
    state.jobEvents = data.job_events || [];
    renderJobProgress();
    await refreshRecentJobs({ silent: true });
    setNotice(`${options.startedLabel || "Job"} queued as ${data.job_id}. Polling progress every 1.5s.`, "working");
    startJobPolling(data.job_id, { completionLabel: options.completionLabel || options.startedLabel || "Job" });
    return data;
  } finally {
    setBusy(false);
  }
}

function renderSavedRuns() {
  if (!state.savedRuns.length) {
    el.savedRuns.className = "saved-runs empty-state";
    el.savedRuns.textContent = "No saved runs yet.";
    return;
  }
  el.savedRuns.className = "saved-runs";
  el.savedRuns.innerHTML = state.savedRuns
    .map(
      (run) => `
        <button class="saved-run" type="button" data-run-id="${escapeAttr(run.id)}">
          <strong>${escapeHTML(run.topic)}</strong>
          <span>${escapeHTML(run.mode)} · ${escapeHTML(run.created_at)} · ${run.supported_count || 0}/${run.claim_count || 0} supported · audit ${escapeHTML(run.audit?.overall || "n/a")}</span>
          <small>${escapeHTML(run.report_path || run.id)}</small>
        </button>
      `
    )
    .join("");
  el.savedRuns.querySelectorAll(".saved-run").forEach((button) => {
    button.addEventListener("click", () => restoreRun(button.dataset.runId));
  });
}

async function runQuery(options = {}) {
  const topic = String(options.topicOverride || el.topic.value).trim();
  if (!topic) {
    setNotice("Please enter a research topic.", "error");
    return;
  }
  const deep = Boolean(options.deep);
  const payload = {
    topic,
    mode: state.mode,
    max_results: Number(options.maxResultsOverride || el.maxResults.value || 10),
    no_network: !el.networkToggle.checked,
    urls: el.urls.value.trim(),
    fetch: el.fetchToggle.checked,
    profile: state.searchProfile,
    routing_strategy: state.autoRoute ? "adaptive_staged" : "manual",
    deep,
    auto_verify: el.autoVerifyToggle.checked,
  };
  if (!state.autoRoute) payload.providers = selectedProviders();
  if (deep && state.currentRunId) payload.previous_run_id = state.currentRunId;
  setNotice(deep ? "Deepening queued: expanding live queries and follow-up gaps." : "Search queued. Progress will update in the Job panel.", "working");
  renderSearchPending();
  try {
    await submitBackgroundJob(deep ? "deepen" : "search", payload, {
      activeButton: deep ? el.deepen : el.run,
      busyText: deep ? "Queueing..." : "Queueing...",
      startedLabel: deep ? "Deepen job" : "Search job",
      completionLabel: deep ? "Deepen" : "Search",
    });
  } catch (error) {
    setNotice(error.message, "error");
  }
}

async function runFullChain() {
  const topic = el.topic.value.trim();
  if (!topic) {
    setNotice("Please enter a research topic.", "error");
    return;
  }
  const evidenceText = el.evidenceText.value.trim();
  const inlineManualEvidence = evidenceText
    ? [{ url: el.evidenceUrl.value.trim(), text: evidenceText }]
    : [];
  const manualEvidence = mergeManualEvidence(state.pendingManualEvidence, inlineManualEvidence);
  const reportText = el.report.value.trim();
  const textToVerify = reportText && reportText !== state.generatedDraft.trim() ? reportText : "";
  const payload = {
    topic,
    mode: state.mode,
    max_results: Number(el.maxResults.value || 10),
    no_network: !el.networkToggle.checked,
    urls: el.urls.value.trim(),
    fetch: el.fetchToggle.checked,
    manual_evidence: manualEvidence,
    text_to_verify: textToVerify,
    profile: state.searchProfile,
    routing_strategy: state.autoRoute ? "adaptive_staged" : "manual",
  };
  if (!state.autoRoute) payload.providers = selectedProviders();
  setNotice("Full chain queued: plan, retrieve/ingest, evidence, verify, report.", "working");
  try {
    await submitBackgroundJob("run", payload, {
      activeButton: el.fullRun,
      busyText: "Queueing...",
      startedLabel: "Full-chain job",
      completionLabel: "Full chain",
    });
    if (manualEvidence.length) {
      el.evidenceText.value = "";
      state.pendingManualEvidence = [];
    }
  } catch (error) {
    setNotice(error.message, "error");
  }
}

async function ingestUrls() {
  const urls = el.urls.value.trim();
  if (!urls) {
    setNotice("Paste one URL per line before ingesting.", "error");
    return;
  }
  setBusy(true, el.ingest, "Ingesting...");
  try {
    const data = await postJSON("/api/ingest", { urls, fetch: el.fetchToggle.checked });
    state.sources = mergeById(state.sources, data.sources || []);
    state.evidence = mergeById(state.evidence, data.evidence || []);
    renderSources();
    await refreshStatus();
    setNotice(`Ingested ${(data.sources || []).length} sources.`, "ok");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function addManualEvidence() {
  const text = el.evidenceText.value.trim();
  if (!text) {
    setNotice("Paste a source-backed evidence note first.", "error");
    return;
  }
  setBusy(true, el.addEvidence, "Adding...");
  try {
    const data = await postJSON("/api/evidence", {
      url: el.evidenceUrl.value.trim(),
      text,
    });
    state.sources = mergeById(state.sources, [data.source]);
    state.evidence = mergeById(state.evidence, [data.evidence]);
    state.pendingManualEvidence = mergeManualEvidence(state.pendingManualEvidence, [
      {
        url: el.evidenceUrl.value.trim(),
        text,
      },
    ]);
    el.evidenceText.value = "";
    renderSources();
    await refreshStatus();
    setNotice("Manual evidence added to the local store.", "ok");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function verifyText() {
  const text = el.report.value.trim();
  if (!text) {
    setNotice("Paste or generate report text before verification.", "error");
    return;
  }
  setBusy(true, el.verify, "Verifying...");
  try {
    const data = await postJSON("/api/verify", {
      topic: el.topic.value.trim() || "Research Report",
      text,
    });
    state.claims = data.claims || [];
    state.revisionSuggestions = data.revision_suggestions || [];
    renderClaims();
    el.report.value = data.report_markdown || el.report.value;
    state.generatedDraft = "";
    await refreshStatus();
    setNotice(`Verified ${state.claims.length} atomic claims.`, "ok");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function refreshSavedRuns() {
  const data = await getJSON("/api/runs?limit=12");
  state.savedRuns = data.runs || [];
  renderSavedRuns();
  renderServiceDashboard();
}

async function refreshStatus() {
  const [status, runs, jobs] = await Promise.all([getJSON("/api/status"), getJSON("/api/runs?limit=12"), getJSON("/v1/jobs?limit=20")]);
  state.status = status;
  state.savedRuns = runs.runs || [];
  state.recentJobs = jobs.jobs || [];
  state.providerHealth = state.providerHealth.length ? state.providerHealth : status.provider_health || [];
  renderStatus();
  renderSavedRuns();
  renderRecentJobs();
  renderServiceDashboard();
}

async function restoreRun(runId, options = {}) {
  if (!runId) return;
  try {
    const run = await getJSON(`/api/run?id=${encodeURIComponent(runId)}`);
    const report = parseReportJson(run.report_json);
    el.topic.value = run.topic;
    el.report.value = run.report_markdown || "";
    state.currentRunId = run.id || runId;
    state.generatedDraft = "";
    state.claims = report.claim_table || [];
    state.revisionSuggestions = report.revision_suggestions || run.revision_suggestions || [];
    state.sources = report.sources || state.sources;
    state.evidence = report.evidence || state.evidence;
    state.researchBuckets = report.research_buckets || run.research_buckets || {};
    state.coverageGaps = report.coverage_gaps || run.coverage_gaps || [];
    state.providerHealth = report.provider_health || run.provider_health || state.providerHealth;
    state.canonicalGroups = report.canonical_groups || run.canonical_groups || [];
    state.toolMatrix = report.tool_matrix || run.tool_matrix || [];
    state.sourceQuality = report.source_quality || run.source_quality || null;
    state.evidenceQuality = report.quality_summary || run.quality_summary || null;
    state.claimQuality = report.claim_quality || run.claim_quality || [];
    state.deepenSuggestions = report.deepen_suggestions || run.deepen_suggestions || [];
    state.referenceChecks = report.reference_checks || run.reference_checks || null;
    state.routingPlan = report.routing_plan || run.routing_plan || null;
    state.routingTrace = report.routing_trace || run.routing_trace || [];
    state.coverageGate = report.coverage_gate || run.coverage_gate || null;
    state.providerBudget = report.provider_budget || run.provider_budget || null;
    state.externalAcquisition = report.external_acquisition || run.external_acquisition || [];
    state.stopReason = report.stop_reason || run.stop_reason || "";
    if (state.routingPlan?.profile) {
      state.searchProfile = state.routingPlan.profile;
      el.searchProfile.value = state.searchProfile;
    }
    if (run.plan) {
      renderPlan(run.plan);
    }
    renderSources();
    renderClaims();
    renderCoverage();
    renderSourceQuality();
    renderProviderHealth();
    renderCanonicalGroups();
    renderToolMatrix();
    renderRouting();
    if (!options.silent) {
      setNotice(`Restored saved run: ${run.topic}. Report: ${run.report_path || run.id}.`, "ok");
    }
  } catch (error) {
    setNotice(`Restore failed: ${error.message}`, "error");
  }
}

async function reviewQuality(runId = state.currentRunId) {
  if (!runId) {
    setNotice("Run the full chain or restore a saved run before reviewing quality.", "error");
    return;
  }
  setButtonBusy(el.qualityReview, true, "Checking...");
  try {
    const data = await postJSON(`/v1/runs/${encodeURIComponent(runId)}/quality`, {});
    state.evidenceQuality = data.quality_summary || null;
    state.claimQuality = data.claim_quality || [];
    renderClaims();
    renderSourceQuality();
    if (state.selectedClaimId) {
      openClaimDrawer(state.selectedClaimId);
    }
    setNotice(`Quality review ${data.overall || "complete"}: ${state.claimQuality.length} claims checked.`, data.overall === "error" ? "error" : "ok");
  } catch (error) {
    setNotice(`Quality review failed: ${error.message}`, "error");
  } finally {
    setButtonBusy(el.qualityReview, false);
  }
}

async function reviewDeepenSuggestions(runId = state.currentRunId) {
  if (!runId) {
    setNotice("Run the full chain or restore a saved run before generating deepen suggestions.", "error");
    return;
  }
  setButtonBusy(el.claimSuggest, true, "Suggesting...");
  try {
    const data = await postJSON(`/v1/runs/${encodeURIComponent(runId)}/deepen-claims`, { max_queries: 5 });
    state.deepenSuggestions = data.deepen_suggestions || [];
    renderSourceQuality();
    setNotice(`Generated ${state.deepenSuggestions.length} deepen suggestion(s). No network search was run.`, "ok");
  } catch (error) {
    setNotice(`Deepen suggestions failed: ${error.message}`, "error");
  } finally {
    setButtonBusy(el.claimSuggest, false);
  }
}

async function reviewReferences() {
  const text = el.report.value.trim();
  if (!text && !state.currentRunId) {
    setNotice("Paste or restore a report before checking references.", "error");
    return;
  }
  setButtonBusy(el.referenceCheck, true, "Checking...");
  try {
    const payload = text ? { text } : { run_id: state.currentRunId };
    const data = await postJSON("/v1/references/check", payload);
    state.referenceChecks = data;
    renderSourceQuality();
    const summary = data.validation_summary || {};
    setNotice(`References check ${summary.overall || "complete"}.`, summary.overall === "error" ? "error" : "ok");
  } catch (error) {
    setNotice(`References check failed: ${error.message}`, "error");
  } finally {
    setButtonBusy(el.referenceCheck, false);
  }
}

async function auditCurrentRun(runId = state.currentRunId) {
  if (!runId) {
    setNotice("Run the full chain or restore a saved run before auditing.", "error");
    return;
  }
  try {
    const data = await getJSON(`/api/audit?id=${encodeURIComponent(runId)}`);
    const failed = (data.checks || []).filter((check) => check.status === "error").length;
    const warned = (data.checks || []).filter((check) => check.status === "warn").length;
    setNotice(`Audit ${data.overall}: ${failed} errors, ${warned} warnings for ${data.topic || runId}.`, data.overall === "error" ? "error" : "ok");
  } catch (error) {
    setNotice(`Audit failed: ${error.message}`, "error");
  }
}

function parseReportJson(value) {
  if (!value) return {};
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
}

function clearSavedRuns() {
  state.savedRuns = [];
  renderSavedRuns();
  setNotice("Server-side run history is append-only. Start a fresh data directory to clear it.", "ok");
}

async function exportMarkdown() {
  try {
    const data = await postJSON("/api/export", { format: "md" });
    el.report.value = data.content || "";
    setNotice("Export loaded into report preview.", "ok");
  } catch (error) {
    setNotice(error.message, "error");
  }
}

async function exportObsidian() {
  if (!state.currentRunId) {
    setNotice("Run the full chain or restore a saved run before exporting to Obsidian.", "error");
    return;
  }
  if (!state.status?.obsidian_configured) {
    setNotice("Set RESEARCH_OBSIDIAN_VAULT or obsidian_vault in research.config.json first.", "error");
    return;
  }
  try {
    const data = await postJSON("/api/obsidian", { run_id: state.currentRunId });
    setNotice(`Obsidian note written: ${data.path}.`, "ok");
  } catch (error) {
    setNotice(error.message, "error");
  }
}

function mergeById(existing, incoming) {
  const map = new Map(existing.map((item) => [item.id || item.url || item.text, item]));
  for (const item of incoming) {
    map.set(item.id || item.url || item.text, item);
  }
  return Array.from(map.values());
}

function mergeManualEvidence(existing, incoming) {
  const map = new Map();
  [...(existing || []), ...(incoming || [])].forEach((item) => {
    const text = String(item.text || "").trim();
    if (!text) return;
    const url = String(item.url || "").trim();
    map.set(`${url}\n${text}`, { url, text });
  });
  return Array.from(map.values());
}

function selectedProviders() {
  const providers = el.providerToggles.filter((toggle) => toggle.checked && !toggle.disabled).map((toggle) => toggle.value);
  return providers.length ? providers : ["searxng"];
}

function providerSelectable(toggle) {
  return toggle.value === "searxng" || toggle.dataset.providerConfigured !== "false";
}

function setAutoRoute(enabled, options = {}) {
  state.autoRoute = Boolean(enabled);
  el.autoRouteToggle.checked = state.autoRoute;
  el.manualProviderGrid.classList.toggle("is-disabled", state.autoRoute);
  el.providerToggles.forEach((toggle) => {
    toggle.disabled = state.autoRoute || !providerSelectable(toggle);
  });
  if (!options.silent) {
    setNotice(
      state.autoRoute
        ? "Auto route enabled. Providers will be called in stages until the coverage gate passes."
        : "Manual routing enabled. Only checked providers will be called.",
      "ok"
    );
  }
  renderRouting();
}

function applyProviderPreset(name, options = {}) {
  const preset = PROVIDER_PRESETS[name] || PROVIDER_PRESETS.default;
  const wanted = new Set(preset);
  const active = [];
  el.providerToggles.forEach((toggle) => {
    const shouldUse = wanted.has(toggle.value) && providerSelectable(toggle);
    toggle.checked = shouldUse;
    if (shouldUse) active.push(toggle.value);
  });
  if (!active.length) {
    const searxng = el.providerToggles.find((toggle) => toggle.value === "searxng");
    if (searxng) {
      searxng.checked = true;
      active.push("searxng");
    }
  }
  state.providerPreset = name;
  state.searchProfile = ROUTING_PROFILE_BY_PRESET[name] || "default";
  el.searchProfile.value = state.searchProfile;
  el.providerPresets.forEach((button) => button.classList.toggle("active", button.dataset.providerPreset === name));
  if (!options.silent) {
    const description = state.autoRoute ? `profile ${state.searchProfile}` : active.join(", ");
    setNotice(`${PROVIDER_PRESET_LABELS[name] || "Provider"} preset: ${description}.`, "ok");
  }
}

function setBusy(isBusy, activeButton = null, busyText = "Working...") {
  if (busyTimer) {
    clearInterval(busyTimer);
    busyTimer = null;
  }
  const startedAt = Date.now();
  [el.run, el.deepen, el.fullRun, el.verify, el.audit, el.export, el.obsidian, el.ingest, el.addEvidence, el.selftest, el.evals, el.readiness, el.jobsRefresh, el.jobRefresh, el.jobRetry, el.qualityReview, el.claimSuggest, el.referenceCheck].forEach((button) => {
    if (!button) return;
    if (!button.dataset.idleLabel) {
      button.dataset.idleLabel = button.textContent;
    }
    button.disabled = isBusy;
    button.classList.toggle("is-busy", isBusy && button === activeButton);
    button.setAttribute("aria-busy", isBusy && button === activeButton ? "true" : "false");
    button.textContent = isBusy && button === activeButton ? busyText : button.dataset.idleLabel;
  });
  if (isBusy && activeButton) {
    state.currentJob = { id: "pending", status: "running", stage: busyText };
    state.jobEvents = [{ stage: "request", status: "running", message: "Request accepted by the UI. Waiting for server job id." }];
    renderJobProgress();
    busyTimer = setInterval(() => {
      const elapsedSeconds = Math.floor((Date.now() - startedAt) / 1000);
      activeButton.textContent = `${busyText} ${formatDuration(elapsedSeconds)}`;
    }, 1000);
  }
  if (!isBusy) {
    renderJobProgress();
  }
}

function setButtonBusy(button, isBusy, busyText = "Working...") {
  if (!button) return;
  if (!button.dataset.idleLabel) {
    button.dataset.idleLabel = button.textContent;
  }
  button.disabled = isBusy;
  button.classList.toggle("is-busy", isBusy);
  button.setAttribute("aria-busy", isBusy ? "true" : "false");
  button.textContent = isBusy ? busyText : button.dataset.idleLabel;
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}m ${String(rest).padStart(2, "0")}s`;
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHTML(value).replaceAll("`", "&#096;");
}

el.segments.forEach((segment) => {
  segment.addEventListener("click", () => {
    state.mode = segment.dataset.mode;
    el.segments.forEach((item) => item.classList.toggle("active", item === segment));
    setNotice(`Mode set to ${state.mode}.`, "ok");
  });
});

el.providerPresets.forEach((button) => {
  button.addEventListener("click", () => applyProviderPreset(button.dataset.providerPreset || "default"));
});

el.providerToggles.forEach((toggle) => {
  toggle.addEventListener("change", () => {
    state.providerPreset = "custom";
    el.providerPresets.forEach((button) => button.classList.remove("active"));
  });
});

el.autoRouteToggle.addEventListener("change", () => setAutoRoute(el.autoRouteToggle.checked));
el.searchProfile.addEventListener("change", () => {
  state.searchProfile = el.searchProfile.value || "default";
  state.providerPreset = "custom";
  el.providerPresets.forEach((button) => button.classList.remove("active"));
  setNotice(`Routing profile set to ${state.searchProfile}.`, "ok");
});

el.run.addEventListener("click", () => runQuery());
el.deepen.addEventListener("click", () => runQuery({ deep: true }));
el.fullRun.addEventListener("click", runFullChain);
el.ingest.addEventListener("click", ingestUrls);
el.addEvidence.addEventListener("click", addManualEvidence);
el.doctor.addEventListener("click", () => runDoctor());
el.selftest.addEventListener("click", runSelftest);
el.evals.addEventListener("click", runEvals);
el.readiness.addEventListener("click", runReadiness);
el.verify.addEventListener("click", verifyText);
el.audit.addEventListener("click", () => auditCurrentRun());
el.qualityReview.addEventListener("click", () => reviewQuality());
el.claimSuggest.addEventListener("click", () => reviewDeepenSuggestions());
el.referenceCheck.addEventListener("click", reviewReferences);
el.export.addEventListener("click", exportMarkdown);
el.obsidian.addEventListener("click", exportObsidian);
el.jobsRefresh.addEventListener("click", () => refreshRecentJobs());
el.jobRefresh.addEventListener("click", refreshCurrentJob);
el.jobRetry.addEventListener("click", retryCurrentJob);
el.clearRuns.addEventListener("click", clearSavedRuns);
el.claimTable.addEventListener("click", (event) => {
  const detailButton = event.target.closest(".claim-detail-btn");
  if (detailButton) {
    openClaimDrawer(detailButton.dataset.claimId);
    return;
  }
  const deepenButton = event.target.closest(".claim-deepen-btn");
  if (deepenButton) {
    deepenClaim(deepenButton.dataset.claimId);
  }
});
if (el.claimDrawerClose) {
  el.claimDrawerClose.addEventListener("click", closeClaimDrawer);
}
if (el.claimDrawer) {
  el.claimDrawer.addEventListener("click", (event) => {
    const deepenButton = event.target.closest(".claim-deepen-btn");
    if (deepenButton) {
      deepenClaim(deepenButton.dataset.claimId);
    }
  });
}

renderSources();
renderClaims();
renderCoverage();
renderSourceQuality();
renderRouting();
renderProviderHealth();
renderCanonicalGroups();
renderToolMatrix();
renderJobProgress();
renderRecentJobs();
hydrate();
