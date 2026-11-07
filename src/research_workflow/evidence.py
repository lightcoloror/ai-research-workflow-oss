from __future__ import annotations

import html
import gzip
import importlib
import importlib.util
import json
import os
import re
import ssl
import sys
import zlib
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

from .models import Evidence, Source, Verdict
from .search import infer_publisher, infer_source_type
from .store import stable_id


@dataclass(slots=True)
class ParsedPage:
    title: str = ""
    description: str = ""
    published_at: str = ""
    text: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    final_url: str = ""
    content_type: str = ""
    fetch_method: str = ""
    fetch_errors: list[str] = field(default_factory=list)


def sources_from_url_file(path: Path) -> list[Source]:
    urls = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return [source_from_url(url) for url in urls]


def source_from_url(
    url: str,
    *,
    search_queries: list[str] | None = None,
    search_buckets: list[str] | None = None,
    search_purposes: list[str] | None = None,
    search_stages: list[str] | None = None,
) -> Source:
    publisher = infer_publisher(url)
    return Source(
        id=stable_id(url, prefix="src_"),
        url=url,
        title=publisher or url,
        publisher=publisher,
        source_type=infer_source_type(url),
        search_engine="manual",
        search_queries=_clean_labels(search_queries),
        search_buckets=_clean_labels(search_buckets),
        search_purposes=_clean_labels(search_purposes),
        search_stages=_clean_labels(search_stages),
    )


def _clean_labels(items: list[str] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        label = " ".join(str(item or "").split())
        key = label.lower()
        if not label or key in seen:
            continue
        seen.add(key)
        cleaned.append(label)
    return cleaned


def extract_evidence_from_sources(sources: list[Source], *, fetch: bool = False) -> list[Evidence]:
    evidence: list[Evidence] = []
    for source in sources:
        text = source.snippet
        snippets: list[str] = []
        extraction_method = "search_snippet" if source.snippet else "manual_placeholder"
        quote_type = "snippet" if source.snippet else "manual_note"
        passage_urls: dict[str, str] = {}
        provider_passages = source.provider_metadata.get("passages", []) if isinstance(source.provider_metadata, dict) else []
        if not fetch and isinstance(provider_passages, list):
            for passage in provider_passages:
                if not isinstance(passage, dict):
                    continue
                passage_text = clean_text(str(passage.get("text") or ""))[:1200]
                if not passage_text:
                    continue
                snippets.append(passage_text)
                passage_urls[passage_text] = str(passage.get("citation_url") or source.url)
            if snippets:
                extraction_method = "provider_matched_passage"
                quote_type = "matched_passage"
        if fetch and source.url:
            page = fetch_page(source.url)
            if page:
                enrich_source_from_page(source, page)
                snippets = evidence_snippets(page)
                text = page.text or page.description or source.snippet
                extraction_method = page.fetch_method or "fetched_html"
                quote_type = "reader_text" if page.fetch_method.startswith(("web_content_fetcher", "jina_reader", "firecrawl", "personal_intelligence_desk", "web_access")) else "fetched_text"
        if not text:
            text = f"Manual source recorded for later review: {source.url}"
        snippets = snippets or [clean_text(text)[:1200]]
        for snippet in snippets:
            item = Evidence(
                id=stable_id(source.id, snippet, prefix="ev_"),
                source_id=source.id,
                quote_or_paraphrase=clean_text(snippet)[:1200],
                claim_relevance="page excerpt" if fetch else "general source evidence",
                support_direction=Verdict.PARTIAL,
                confidence=0.25 if "Manual source recorded" in snippet else 0.65 if fetch else 0.5,
                extraction_method=extraction_method,
                text_length=len(clean_text(snippet)),
                source_quote_type=quote_type,
                evidence_url=passage_urls.get(clean_text(snippet)[:1200], source.url),
            )
            evidence.append(item)
    return evidence


def fetch_page(url: str) -> ParsedPage | None:
    errors: list[str] = []
    reader_provider = os.environ.get("RESEARCH_READER_PROVIDER", "").strip().lower()
    strict_reader = reader_provider in {"wcf", "web_content_fetcher", "web-content-fetcher", "pid", "personal_intelligence_desk", "personal-intelligence-desk"}
    if reader_provider in {"wcf", "web_content_fetcher", "web-content-fetcher", "pid", "personal_intelligence_desk", "personal-intelligence-desk", "jina", "jina_reader", "firecrawl"}:
        try:
            if reader_provider in {"wcf", "web_content_fetcher", "web-content-fetcher"}:
                raw, content_type, final_url, method = fetch_via_web_content_fetcher(url)
            elif reader_provider in {"pid", "personal_intelligence_desk", "personal-intelligence-desk"}:
                raw, content_type, final_url, method = fetch_via_personal_intelligence_desk(url)
            elif reader_provider in {"jina", "jina_reader"}:
                raw, content_type, final_url, method = fetch_via_jina_reader(url)
            else:
                raw, content_type, final_url, method = fetch_via_firecrawl(url)
            page = parse_page(raw)
            page.final_url = final_url
            page.content_type = content_type
            page.fetch_method = method
            page.fetch_errors = errors
            return page
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{reader_provider}: {type(exc).__name__}: {str(exc)[:160]}")
            if strict_reader:
                raise RuntimeError(f"strict_reader_failed:{errors[-1]}") from exc
    try:
        raw, content_type, final_url, method = fetch_page_bytes(url)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"direct: {type(exc).__name__}: {str(exc)[:160]}")
        try:
            raw, content_type, final_url, method = fetch_via_web_access(url)
            errors.append(f"web_access: ok via {method}")
        except Exception as web_exc:  # noqa: BLE001
            errors.append(f"web_access: {type(web_exc).__name__}: {str(web_exc)[:160]}")
            return _fetch_feishu_with_jina_fallback(url, errors)
    if "text" not in content_type and "html" not in content_type and "json" not in content_type and "markdown" not in content_type:
        return _fetch_feishu_with_jina_fallback(url, errors)
    page = parse_page(raw)
    if _should_try_feishu_jina_fallback(url, page):
        fallback = _fetch_feishu_with_jina_fallback(url, errors)
        if fallback:
            return fallback
    page.final_url = final_url
    page.content_type = content_type
    page.fetch_method = method
    page.fetch_errors = errors
    return page


def _is_feishu_doc_url(url: str) -> bool:
    lowered = url.lower()
    if not any(host in lowered for host in ("feishu.cn/", "larksuite.com/")):
        return False
    return any(path in lowered for path in ("/docx/", "/doc/", "/wiki/"))


def _should_try_feishu_jina_fallback(url: str, page: ParsedPage) -> bool:
    if not _is_feishu_doc_url(url):
        return False
    text = clean_text(page.text)
    title = clean_text(page.title)
    return len(text) < 200 or ("feishu" in title.lower() and "docs" in title.lower() and len(text) < 800)


def _fetch_feishu_with_jina_fallback(url: str, errors: list[str]) -> ParsedPage | None:
    if not _is_feishu_doc_url(url):
        return None
    try:
        raw, content_type, final_url, method = fetch_via_jina_reader(url)
        page = parse_page(raw)
        page.final_url = final_url
        page.content_type = content_type
        page.fetch_method = f"{method}:feishu_fallback"
        page.fetch_errors = [*errors, "feishu_jina_fallback: ok"]
        return page
    except Exception as exc:  # noqa: BLE001
        errors.append(f"feishu_jina_fallback: {type(exc).__name__}: {str(exc)[:160]}")
        return None


def fetch_via_jina_reader(url: str) -> tuple[str, str, str, str]:
    token = os.environ.get("JINA_API_KEY", "").strip()
    reader_url = "https://r.jina.ai/http://" + url.removeprefix("http://").removeprefix("https://")
    headers = {"Accept": "text/markdown", "User-Agent": "ai-research-workflow/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(reader_url, headers=headers)
    with urlopen(req, timeout=30, context=ssl.create_default_context()) as response:
        body = response.read(2_500_000).decode("utf-8", errors="replace")
        final_url = response.geturl()
    if not clean_text(body):
        raise RuntimeError("jina_reader_empty_content")
    return body, "text/markdown", final_url or url, "jina_reader"


def fetch_via_web_content_fetcher(url: str) -> tuple[str, str, str, str]:
    package, restore = _load_web_content_fetcher_package()
    try:
        request = package.FetchRequest(
            url=url,
            timeout_seconds=int(os.environ.get("WEB_CONTENT_FETCHER_TIMEOUT", "20")),
            use_web_access=bool(os.environ.get("WEB_ACCESS_API_URL") or os.environ.get("RESEARCH_WEB_ACCESS_URL")),
            web_access_base_url=str(os.environ.get("WEB_ACCESS_API_URL") or os.environ.get("RESEARCH_WEB_ACCESS_URL") or "http://127.0.0.1:8787"),
            prefer_browser=os.environ.get("WEB_CONTENT_FETCHER_PREFER_BROWSER", "").strip().lower() in {"1", "true", "yes", "on"},
            archive_dir=str(os.environ.get("WEB_CONTENT_FETCHER_ARCHIVE_DIR") or ""),
        )
        result = package.fetch_content(request)
        if getattr(result, "status", "") != "ok":
            errors = "; ".join(str(item) for item in getattr(result, "errors", []) or [])
            raise RuntimeError(f"web_content_fetcher_failed:{errors or result.status}")
        text = str(getattr(result, "extracted_text", "") or getattr(result, "raw_snapshot", "") or "")
        if not clean_text(text):
            raise RuntimeError("web_content_fetcher_empty_content")
        content_type = str(getattr(result, "content_type", "") or "text/plain")
        final_url = str(getattr(result, "final_url", "") or url)
        transport = str(getattr(result, "transport", "") or "unknown")
        return text, content_type, final_url, f"web_content_fetcher:{transport}"
    finally:
        restore()


def _load_web_content_fetcher_package():
    configured_path = os.environ.get("WEB_CONTENT_FETCHER_PATH", "").strip()
    if not configured_path:
        try:
            return importlib.import_module("web_content_fetcher"), lambda: None
        except ModuleNotFoundError:
            raise RuntimeError(
                "web_content_fetcher_not_found:install the reader extra or set WEB_CONTENT_FETCHER_PATH"
            ) from None
    root = Path(configured_path)
    package_dir = root / "web_content_fetcher"
    if not (package_dir / "__init__.py").exists():
        raise RuntimeError(f"web_content_fetcher_not_found:{root}")
    old_path = list(sys.path)
    for name in list(sys.modules):
        if name == "web_content_fetcher" or name.startswith("web_content_fetcher."):
            sys.modules.pop(name, None)
    try:
        sys.path.insert(0, str(root))
        package = importlib.import_module("web_content_fetcher")
        module_path = Path(getattr(package, "__file__", "")).resolve()
        if root.resolve() not in module_path.parents:
            raise RuntimeError(f"web_content_fetcher_wrong_module:{module_path}")
        return package, lambda: _restore_sys_path(old_path)
    except Exception:
        sys.path = old_path
        raise


def _restore_sys_path(old_path: list[str]) -> None:
    sys.path = old_path


def fetch_via_personal_intelligence_desk(url: str) -> tuple[str, str, str, str]:
    configured_path = os.environ.get("PERSONAL_INTELLIGENCE_DESK_PATH", "").strip()
    if not configured_path:
        raise RuntimeError("personal_intelligence_desk_not_configured")
    root = Path(configured_path)
    scripts_dir = root / "scripts"
    monitor_path = scripts_dir / "monitor_sites.py"
    if not monitor_path.exists():
        raise RuntimeError(f"personal_intelligence_desk_not_found:{root}")
    old_workspace = os.environ.get("OPENCLAW_WORKSPACE")
    os.environ["OPENCLAW_WORKSPACE"] = str(root)
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        module_name = f"research_workflow_pid_monitor_sites_{stable_id(str(monitor_path), prefix='')}"
        spec = importlib.util.spec_from_file_location(module_name, monitor_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"personal_intelligence_desk_import_failed:{monitor_path}")
        pid_monitor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pid_monitor)

        errors: list[str] = []
        monitor = {
            "name": "research_workflow_page_fetch",
            "web_access": {"enabled": True, "formats": ["html", "markdown", "links"], "intent": "read", "policy_mode": "permissive"},
        }
        data, content_type, final_url, transport = pid_monitor.fetch_page_for_monitor(url, monitor, errors, timeout=20)
        text = pid_monitor.decode(data)
        if not clean_text(text):
            raise RuntimeError("personal_intelligence_desk_empty_content")
        return text, content_type, final_url, f"personal_intelligence_desk:{transport}"
    finally:
        if old_workspace is None:
            os.environ.pop("OPENCLAW_WORKSPACE", None)
        else:
            os.environ["OPENCLAW_WORKSPACE"] = old_workspace


def fetch_via_firecrawl(url: str) -> tuple[str, str, str, str]:
    endpoint = os.environ.get("FIRECRAWL_API_URL", "https://api.firecrawl.dev/v1/scrape").strip()
    token = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not token:
        raise RuntimeError("firecrawl_not_configured")
    payload = {"url": url, "formats": ["markdown", "html"], "onlyMainContent": True}
    req = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json", "Authorization": f"Bearer {token}", "User-Agent": "ai-research-workflow/0.1"},
        method="POST",
    )
    with urlopen(req, timeout=45, context=ssl.create_default_context()) as response:
        body = json.loads(response.read().decode("utf-8", errors="replace"))
    data = body.get("data", {}) if isinstance(body, dict) else {}
    content = str(data.get("markdown") or data.get("html") or "")
    if not clean_text(content):
        raise RuntimeError("firecrawl_empty_content")
    final_url = str(data.get("metadata", {}).get("sourceURL") or url)
    content_type = "text/markdown" if data.get("markdown") else "text/html"
    return content, content_type, final_url, "firecrawl"


def fetch_text(url: str) -> str:
    page = fetch_page(url)
    return page.text if page else ""


def parse_page(raw: str) -> ParsedPage:
    parser = _PageParser()
    parser.feed(raw)
    meta = {normalize_meta_key(key): clean_text(value) for key, value in parser.meta.items() if clean_text(value)}
    title = clean_text(parser.title) or meta.get("og:title", "") or meta.get("twitter:title", "")
    description = meta.get("description", "") or meta.get("og:description", "") or meta.get("twitter:description", "")
    published_at = first_present(
        meta,
        [
            "article:published_time",
            "article:modified_time",
            "date",
            "dc.date",
            "dc.date.issued",
            "pubdate",
            "publishdate",
            "timestamp",
        ],
    )
    return ParsedPage(
        title=title,
        description=description,
        published_at=published_at,
        text=extract_main_text(raw),
        meta=meta,
    )


def enrich_source_from_page(source: Source, page: ParsedPage) -> None:
    if page.title and (not source.title or source.title == source.publisher or source.title == source.url):
        source.title = page.title
    if page.published_at and not source.published_at:
        source.published_at = page.published_at
    if page.description:
        source.snippet = page.description
    elif page.text and not source.snippet:
        source.snippet = page.text[:360]


def evidence_snippets(page: ParsedPage, *, max_snippets: int = 3) -> list[str]:
    candidates: list[str] = []
    if page.description:
        candidates.append(page.description)
    for chunk in paragraph_chunks(page.text):
        candidates.append(chunk)
        if len(candidates) >= max_snippets:
            break
    for sentence in split_sentences(page.text):
        if not _is_evidence_sentence(sentence):
            continue
        candidates.append(sentence)
        if len(candidates) >= max_snippets:
            break
    return _dedupe(candidates)[:max_snippets]


def paragraph_chunks(text: str, *, min_len: int = 180, max_len: int = 900) -> list[str]:
    cleaned = clean_text(text)
    if len(cleaned) < min_len:
        return []
    sentences = split_sentences(cleaned)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for sentence in sentences:
        if not _is_evidence_sentence(sentence):
            continue
        next_len = current_len + len(sentence) + 1
        if current and next_len > max_len:
            chunks.append(clean_text(" ".join(current)))
            current = []
            current_len = 0
        current.append(sentence)
        current_len += len(sentence) + 1
        if current_len >= min_len:
            chunks.append(clean_text(" ".join(current)))
            current = []
            current_len = 0
    if current and current_len >= min_len:
        chunks.append(clean_text(" ".join(current)))
    return _dedupe(chunks)


def fetch_page_bytes(url: str, *, timeout: int = 20) -> tuple[str, str, str, str]:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/json,text/plain,*/*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    ctx = ssl.create_default_context()
    with urlopen(req, timeout=timeout, context=ctx) as response:
        raw = response.read(2_500_000)
        content_type = response.headers.get("content-type", "")
        encoding = (response.headers.get("content-encoding", "") or "").lower()
        final_url = response.geturl()
    if "gzip" in encoding:
        raw = gzip.decompress(raw)
    elif "deflate" in encoding:
        raw = zlib.decompress(raw)
    return decode_bytes(raw), content_type, final_url, "direct"


def fetch_via_web_access(url: str) -> tuple[str, str, str, str]:
    base_url = str(os.environ.get("RESEARCH_WEB_ACCESS_URL") or os.environ.get("WEB_ACCESS_API_URL") or "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("web_access_not_configured")
    payload = {
        "url": url,
        "formats": ["markdown", "html", "links"],
        "intent": "read",
        "policyMode": "permissive",
        "timeoutMs": 45_000,
    }
    req = Request(
        f"{base_url}/v1/fetch",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "ai-research-workflow/0.1"},
        method="POST",
    )
    with urlopen(req, timeout=45, context=ssl.create_default_context()) as response:
        body = json.loads(response.read().decode("utf-8", errors="replace"))
    if not body.get("success"):
        raise RuntimeError(f"web_access_failed:{body.get('errorType') or 'unknown'}")
    data = body.get("data", {}) or {}
    markdown = str(data.get("markdown") or "")
    page_html = str(data.get("html") or "")
    content = markdown or page_html
    if not content:
        raise RuntimeError("web_access_empty_content")
    final_url = str(body.get("finalUrl") or body.get("url") or url)
    method = f"web_access:{body.get('levelUsed') or 'default'}"
    content_type = "text/markdown" if markdown else "text/html"
    return content, content_type, final_url, method


def decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "gbk", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_main_text(raw: str) -> str:
    if not raw:
        return ""
    if _looks_like_markdown(raw):
        return clean_text(raw)
    candidates = selector_text_candidates(raw)
    if candidates:
        return clean_text(max(candidates, key=len))
    structured = structured_html_to_text(raw)
    if structured:
        return structured
    return html_to_text(raw)


def structured_html_to_text(raw: str) -> str:
    parser = _TreeBuilder()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # noqa: BLE001
        return ""
    return clean_text(_node_text(parser.root))


def selector_text_candidates(raw: str) -> list[str]:
    parser = _TreeBuilder()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:  # noqa: BLE001
        return []
    selectors = ["article", "main", "div.content", "div.markdown-body", "div.post", "section"]
    candidates: list[str] = []
    for selector in selectors:
        for node in _select_nodes(parser.root, selector)[:5]:
            text = _node_text(node)
            if len(text) >= 240:
                candidates.append(text)
    return _dedupe(candidates)


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", raw)
    raw = re.sub(r"(?is)<head.*?</head>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    return clean_text(raw)


def _looks_like_markdown(raw: str) -> bool:
    sample = raw[:2000]
    tag_count = len(re.findall(r"<[a-zA-Z][^>]*>", sample))
    return tag_count < 3 and bool(re.search(r"(^|\n)#{1,3}\s+|```|\[[^\]]+\]\(https?://", sample))


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    normalized = clean_text(text)
    if not normalized:
        return []
    parts = re.split(r"(?<=[。！？.!?])\s+", normalized)
    return [part.strip(" -") for part in parts if part.strip(" -")]


def normalize_meta_key(key: str) -> str:
    return key.strip().lower()


def first_present(meta: dict[str, str], keys: list[str]) -> str:
    for key in keys:
        value = meta.get(key)
        if value:
            return value
    return ""


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._in_title = True
            return
        if tag.lower() != "meta":
            return
        attr_map = {key.lower(): value or "" for key, value in attrs}
        key = attr_map.get("name") or attr_map.get("property") or attr_map.get("itemprop")
        content = attr_map.get("content", "")
        if key and content:
            self.meta[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["_Node"] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    parent: "_Node | None" = None

    def classes(self) -> set[str]:
        return {part for part in self.attrs.get("class", "").split() if part}

    def attr(self, name: str, default: str = "") -> str:
        return self.attrs.get(name, default)


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag.lower(), {key.lower(): value or "" for key, value in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == lowered:
                del self.stack[index:]
                break

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag.lower(), {key.lower(): value or "" for key, value in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].texts.append(data)


def _select_nodes(root: _Node, selector: str) -> list[_Node]:
    tokens = [_parse_selector_token(part) for part in selector.split() if part.strip()]
    current = [root]
    for token in tokens:
        next_nodes: list[_Node] = []
        for node in current:
            for candidate in _walk_descendants(node):
                if _node_matches(candidate, token):
                    next_nodes.append(candidate)
        current = next_nodes
        if not current:
            break
    return current


def _parse_selector_token(token: str) -> dict:
    tag_match = re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*", token)
    id_match = re.search(r"#([a-zA-Z0-9_-]+)", token)
    return {
        "tag": tag_match.group(0).lower() if tag_match else None,
        "id": id_match.group(1) if id_match else None,
        "classes": re.findall(r"\.([a-zA-Z0-9_-]+)", token),
    }


def _node_matches(node: _Node, token: dict) -> bool:
    if token["tag"] and node.tag != token["tag"]:
        return False
    if token["id"] and node.attr("id") != token["id"]:
        return False
    node_classes = node.classes()
    return all(cls in node_classes for cls in token["classes"])


def _walk_descendants(node: _Node):
    for child in node.children:
        yield child
        yield from _walk_descendants(child)


def _node_text(node: _Node) -> str:
    if node.tag in {"script", "style", "noscript", "nav", "footer", "header", "aside", "form", "button"}:
        return ""
    labels = " ".join([node.attr("id"), node.attr("class"), node.attr("role")]).lower()
    if any(marker in labels for marker in ["nav", "menu", "footer", "sidebar", "breadcrumb", "cookie", "subscribe", "comment"]):
        return ""
    parts = [*node.texts]
    for child in node.children:
        text = _node_text(child)
        if text:
            parts.append(text)
    return clean_text(" ".join(parts))


def _is_evidence_sentence(sentence: str) -> bool:
    if len(sentence) < 40 or len(sentence) > 420:
        return False
    if sentence.lower().startswith(("cookie", "javascript", "enable ", "menu", "skip to")):
        return False
    markers = [
        r"\b(is|are|was|were|has|have|supports|uses|aggregates|provides|includes|released|published|forked)\b",
        r"(是|为|有|支持|使用|聚合|提供|包含|发布|来自|属于)",
        r"\b\d{4}\b",
    ]
    return any(re.search(pattern, sentence, flags=re.I) for pattern in markers)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        cleaned = clean_text(item)
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            out.append(cleaned)
    return out
