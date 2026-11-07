from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .external_workers import ExternalWorkerRun, ExternalWorkerSpec, run_worker
from .models import Source, SourceType, Verdict, now_iso
from .store import stable_id


DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b", re.IGNORECASE)
ARXIV_RE = re.compile(r"\b(?:arxiv:)?(\d{4}\.\d{4,5})(v\d+)?\b", re.IGNORECASE)


@dataclass(slots=True)
class AcademicReference:
    raw: str
    title: str = ""
    doi: str = ""
    arxiv_id: str = ""
    urls: list[str] = field(default_factory=list)
    status: str = "unverified"
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "title": self.title,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "urls": self.urls,
            "status": self.status,
            "confidence": self.confidence,
            "notes": self.notes,
        }


@dataclass(slots=True)
class AcademicReferenceCheckResult:
    references: list[AcademicReference]
    sources: list[Source]
    worker: ExternalWorkerRun | None = None
    warnings: list[str] = field(default_factory=list)
    validation_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "references": [item.to_dict() for item in self.references],
            "sources": [source.to_dict() for source in self.sources],
            "worker": self.worker.to_dict() if self.worker else None,
            "warnings": self.warnings,
            "validation_summary": self.validation_summary,
        }


def extract_academic_references(text: str) -> list[AcademicReference]:
    references: list[AcademicReference] = []
    seen: set[str] = set()
    doi_spans: list[tuple[int, int]] = []
    for match in DOI_RE.finditer(text):
        doi = match.group(1)
        doi_spans.append(match.span(1))
        normalized = normalize_doi(doi)
        key = f"doi:{normalized.lower()}"
        if key in seen:
            continue
        seen.add(key)
        references.append(
            AcademicReference(
                raw=doi,
                doi=normalized,
                urls=[
                    construct_doi_url(normalized),
                    construct_crossref_url(normalized),
                    construct_openalex_doi_url(normalized),
                    construct_semantic_scholar_doi_url(normalized),
                ],
                status="identifier_found",
                confidence=0.75,
                notes=["DOI pattern extracted locally; database/API verification not yet run."],
            )
        )
    for match in ARXIV_RE.finditer(text):
        if _inside_any_span(match.span(1), doi_spans):
            continue
        arxiv_id = f"{match.group(1)}{match.group(2) or ''}"
        key = f"arxiv:{arxiv_id.lower()}"
        if key in seen:
            continue
        seen.add(key)
        references.append(
            AcademicReference(
                raw=match.group(0),
                arxiv_id=arxiv_id,
                urls=[
                    construct_arxiv_url(arxiv_id),
                    construct_semantic_scholar_arxiv_url(arxiv_id),
                ],
                status="identifier_found",
                confidence=0.75,
                notes=["arXiv identifier extracted locally; database/API verification not yet run."],
            )
        )
    return references


def _inside_any_span(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start >= existing_start and end <= existing_end for existing_start, existing_end in spans)


def check_academic_references(
    text: str,
    *,
    worker_spec: ExternalWorkerSpec | None = None,
) -> AcademicReferenceCheckResult:
    local_refs = extract_academic_references(text)
    _apply_local_validation(local_refs, text)
    sources = [_source_from_reference(item) for item in local_refs for _ in [0] if item.urls]
    worker_run: ExternalWorkerRun | None = None
    warnings: list[str] = []
    if worker_spec:
        worker_run = run_worker(worker_spec, topic="academic reference check", text=text)
        if worker_run.status == "ok":
            sources.extend(worker_run.records.sources)
        else:
            warnings.append(worker_run.warning)
    return AcademicReferenceCheckResult(references=local_refs, sources=sources, worker=worker_run, warnings=warnings, validation_summary=_validation_summary(local_refs, text))


def references_check_to_markdown(result: dict[str, Any]) -> str:
    summary = result.get("validation_summary", {})
    lines = [
        "# References Check",
        "",
        f"Overall: `{summary.get('overall', 'unknown')}`",
        "",
        "| Reference | Status | Confidence | Notes |",
        "| --- | --- | --- | --- |",
    ]
    for item in result.get("references", []):
        identifier = item.get("doi") or item.get("arxiv_id") or item.get("title") or item.get("raw") or "reference"
        notes = "; ".join(item.get("notes") or [])
        lines.append(f"| {_cell(identifier)} | {item.get('status', '')} | {float(item.get('confidence') or 0):.2f} | {_cell(notes)} |")
    if not result.get("references"):
        lines.append("| none | not_found | 0.00 | No DOI or arXiv identifiers found. |")
    return "\n".join(lines) + "\n"


def normalize_doi(value: str) -> str:
    cleaned = value.strip().rstrip(".,;)")
    cleaned = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", cleaned, flags=re.IGNORECASE)
    return cleaned


def _apply_local_validation(references: list[AcademicReference], text: str) -> None:
    for reference in references:
        if reference.arxiv_id:
            if _valid_arxiv_id(reference.arxiv_id):
                reference.status = "valid"
                reference.confidence = max(reference.confidence, 0.85)
                reference.notes.append("arXiv identifier shape is valid locally.")
            else:
                reference.status = "not_found"
                reference.confidence = 0.2
                reference.notes.append("arXiv identifier has an invalid date/version shape.")
        elif reference.doi:
            reference.status = "partial"
            reference.confidence = max(reference.confidence, 0.7)
            reference.notes.append("DOI syntax is valid locally; resolver metadata was not fetched.")
        if _metadata_mismatch_marker(reference, text):
            reference.status = "metadata_mismatch"
            reference.confidence = min(reference.confidence, 0.4)
            reference.notes.append("Local bibliography text indicates identifier/title metadata mismatch.")


def _validation_summary(references: list[AcademicReference], text: str) -> dict[str, Any]:
    counts = {status: 0 for status in ["valid", "partial", "not_found", "metadata_mismatch"]}
    for reference in references:
        if reference.status in counts:
            counts[reference.status] += 1
    no_identifier_lines = _unidentified_reference_lines(text)
    counts["not_found"] += len(no_identifier_lines)
    overall = "ok"
    if counts["metadata_mismatch"] or counts["not_found"]:
        overall = "warn"
    return {
        "overall": overall,
        "counts": counts,
        "unidentified_references": no_identifier_lines,
    }


def _valid_arxiv_id(value: str) -> bool:
    match = re.match(r"^(\d{2})(\d{2})\.\d{4,5}(v\d+)?$", value, flags=re.IGNORECASE)
    if not match:
        return False
    month = int(match.group(2))
    return 1 <= month <= 12


def _metadata_mismatch_marker(reference: AcademicReference, text: str) -> bool:
    identifier = reference.doi or reference.arxiv_id or reference.raw
    for line in text.splitlines():
        if identifier and identifier.lower() in line.lower() and re.search(r"\b(mismatch|wrong title|wrong author|伪引用|不匹配)\b", line, flags=re.IGNORECASE):
            return True
    return False


def _unidentified_reference_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if len(line) < 24:
            continue
        if DOI_RE.search(line) or ARXIV_RE.search(line):
            continue
        if re.match(r"^\[?\d+\]?[\).]\s+", line) or re.search(r"\(\d{4}\)", line):
            lines.append(line[:240])
    return lines[:20]


def _cell(value: object) -> str:
    return " ".join(str(value).replace("|", "\\|").split())


def construct_doi_url(doi: str) -> str:
    return f"https://doi.org/{normalize_doi(doi)}"


def construct_crossref_url(doi: str) -> str:
    return f"https://api.crossref.org/works/{normalize_doi(doi)}"


def construct_openalex_doi_url(doi: str) -> str:
    return f"https://api.openalex.org/works/https://doi.org/{normalize_doi(doi)}"


def construct_semantic_scholar_doi_url(doi: str) -> str:
    return f"https://api.semanticscholar.org/graph/v1/paper/DOI:{normalize_doi(doi)}"


def construct_arxiv_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/abs/{arxiv_id}"


def construct_semantic_scholar_arxiv_url(arxiv_id: str) -> str:
    return f"https://api.semanticscholar.org/graph/v1/paper/ARXIV:{arxiv_id}"


def _source_from_reference(reference: AcademicReference) -> Source:
    primary_url = reference.urls[0]
    identifier = reference.doi or reference.arxiv_id or reference.raw
    return Source(
        id=stable_id("academic-reference", identifier, prefix="src_ref_"),
        url=primary_url,
        title=reference.title or identifier,
        source_type=SourceType.ACADEMIC,
        publisher="academic reference resolver",
        retrieved_at=now_iso(),
        search_engine="academic_reference_check",
        snippet="; ".join(reference.notes),
        quality_score=80 if reference.status in {"valid", "partial"} else 40,
        quality_reasons=[f"status:{reference.status}", "identifier extracted"],
    )
