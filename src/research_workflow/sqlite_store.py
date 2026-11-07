from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .store import ensure_data_dir, read_jsonl


SCHEMA_VERSION = 1


def sqlite_path(data_dir: Path) -> Path:
    return ensure_data_dir(data_dir) / "research.sqlite"


def connect_sqlite(data_dir: Path) -> sqlite3.Connection:
    db_path = sqlite_path(data_dir)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    initialize_sqlite(conn)
    return conn


def initialize_sqlite(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sources (
          id TEXT PRIMARY KEY,
          url TEXT NOT NULL,
          canonical_url TEXT,
          url_hash TEXT NOT NULL,
          title TEXT,
          source_type TEXT,
          publisher TEXT,
          published_at TEXT,
          retrieved_at TEXT,
          search_engine TEXT,
          snippet TEXT,
          payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_url_hash ON sources(url_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_canonical ON sources(canonical_url)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence (
          id TEXT PRIMARY KEY,
          source_id TEXT,
          content_hash TEXT NOT NULL,
          quote_or_paraphrase TEXT,
          claim_relevance TEXT,
          support_direction TEXT,
          confidence REAL,
          extraction_method TEXT,
          text_length INTEGER,
          source_quote_type TEXT,
          payload_json TEXT NOT NULL,
          FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE SET NULL
        )
        """
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_content_hash ON evidence(content_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence(source_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS claims (
          id TEXT PRIMARY KEY,
          text TEXT NOT NULL,
          claim_type TEXT,
          risk_level TEXT,
          verdict TEXT,
          confidence REAL,
          evidence_ids_json TEXT NOT NULL,
          payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_claims_verdict ON claims(verdict)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS claim_evidence (
          claim_id TEXT NOT NULL,
          evidence_id TEXT NOT NULL,
          PRIMARY KEY (claim_id, evidence_id),
          FOREIGN KEY(claim_id) REFERENCES claims(id) ON DELETE CASCADE,
          FOREIGN KEY(evidence_id) REFERENCES evidence(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY,
          topic TEXT,
          mode TEXT,
          created_at TEXT,
          report_path TEXT,
          payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_stats (
          provider TEXT PRIMARY KEY,
          payload_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fetch_cache (
          url_hash TEXT PRIMARY KEY,
          url TEXT NOT NULL,
          content_hash TEXT,
          fetched_at TEXT,
          expires_at TEXT,
          payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()


def sync_jsonl_to_sqlite(data_dir: Path) -> dict[str, Any]:
    root = ensure_data_dir(data_dir)
    conn = connect_sqlite(root)
    try:
        sources = dedupe_rows(read_jsonl(root / "sources.jsonl"))
        evidence = dedupe_rows(read_jsonl(root / "evidence.jsonl"))
        claims = dedupe_rows(read_jsonl(root / "claims.jsonl"))
        runs = dedupe_rows(read_jsonl(root / "runs.jsonl"))
        provider_stats = _read_json(root / "provider_stats.json")
        with conn:
            for row in sources:
                upsert_source(conn, row)
            for row in evidence:
                upsert_evidence(conn, row)
            for row in claims:
                upsert_claim(conn, row)
            for row in runs:
                upsert_run(conn, row)
            for provider, payload in provider_stats.items():
                upsert_provider_stats(conn, provider, payload)
        return sqlite_summary(root)
    finally:
        conn.close()


def sync_jsonl_to_sqlite_quietly(data_dir: Path) -> dict[str, Any]:
    try:
        return sync_jsonl_to_sqlite(data_dir)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc), "path": str(sqlite_path(data_dir))}


def sqlite_summary(data_dir: Path) -> dict[str, Any]:
    conn = connect_sqlite(data_dir)
    try:
        counts = {
            name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            for name in ("sources", "evidence", "claims", "runs", "provider_stats", "fetch_cache")
        }
        return {
            "path": str(sqlite_path(data_dir).resolve()),
            "schema_version": SCHEMA_VERSION,
            "counts": counts,
        }
    finally:
        conn.close()


def upsert_source(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    url = str(row.get("url") or "")
    conn.execute(
        """
        INSERT INTO sources(id, url, canonical_url, url_hash, title, source_type, publisher, published_at, retrieved_at, search_engine, snippet, payload_json)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url_hash) DO UPDATE SET
          url=excluded.url, canonical_url=excluded.canonical_url, url_hash=excluded.url_hash, title=excluded.title,
          source_type=excluded.source_type, publisher=excluded.publisher, published_at=excluded.published_at,
          retrieved_at=excluded.retrieved_at, search_engine=excluded.search_engine, snippet=excluded.snippet, payload_json=excluded.payload_json
        """,
        (
            row.get("id") or _hash(url),
            url,
            row.get("canonical_url") or url,
            _hash((row.get("canonical_url") or url).lower()),
            row.get("title", ""),
            row.get("source_type", ""),
            row.get("publisher", ""),
            row.get("published_at", ""),
            row.get("retrieved_at", ""),
            row.get("search_engine", ""),
            row.get("snippet", ""),
            _json(row),
        ),
    )


def upsert_evidence(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    text = str(row.get("quote_or_paraphrase") or "")
    conn.execute(
        """
        INSERT INTO evidence(id, source_id, content_hash, quote_or_paraphrase, claim_relevance, support_direction, confidence, extraction_method, text_length, source_quote_type, payload_json)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(content_hash) DO UPDATE SET
          source_id=excluded.source_id, content_hash=excluded.content_hash, quote_or_paraphrase=excluded.quote_or_paraphrase,
          claim_relevance=excluded.claim_relevance, support_direction=excluded.support_direction, confidence=excluded.confidence,
          extraction_method=excluded.extraction_method, text_length=excluded.text_length, source_quote_type=excluded.source_quote_type,
          payload_json=excluded.payload_json
        """,
        (
            row.get("id") or _hash(text),
            row.get("source_id", ""),
            _hash(text),
            text,
            row.get("claim_relevance", ""),
            row.get("support_direction", ""),
            float(row.get("confidence") or 0),
            row.get("extraction_method", ""),
            int(row.get("text_length") or len(text)),
            row.get("source_quote_type", ""),
            _json(row),
        ),
    )


def upsert_claim(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    claim_id = str(row.get("id") or _hash(str(row.get("text") or "")))
    evidence_ids = list(row.get("evidence_ids") or [])
    conn.execute(
        """
        INSERT INTO claims(id, text, claim_type, risk_level, verdict, confidence, evidence_ids_json, payload_json)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          text=excluded.text, claim_type=excluded.claim_type, risk_level=excluded.risk_level,
          verdict=excluded.verdict, confidence=excluded.confidence, evidence_ids_json=excluded.evidence_ids_json,
          payload_json=excluded.payload_json
        """,
        (
            claim_id,
            row.get("text", ""),
            row.get("claim_type", ""),
            row.get("risk_level", ""),
            row.get("verdict", ""),
            float(row.get("confidence") or 0),
            _json(evidence_ids),
            _json(row),
        ),
    )
    conn.execute("DELETE FROM claim_evidence WHERE claim_id = ?", (claim_id,))
    for evidence_id in evidence_ids:
        conn.execute("INSERT OR IGNORE INTO claim_evidence(claim_id, evidence_id) VALUES(?, ?)", (claim_id, str(evidence_id)))


def upsert_run(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO runs(id, topic, mode, created_at, report_path, payload_json)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET topic=excluded.topic, mode=excluded.mode, created_at=excluded.created_at, report_path=excluded.report_path, payload_json=excluded.payload_json
        """,
        (row.get("id"), row.get("topic", ""), row.get("mode", ""), row.get("created_at", ""), row.get("report_path", ""), _json(row)),
    )


def upsert_provider_stats(conn: sqlite3.Connection, provider: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO provider_stats(provider, payload_json, updated_at) VALUES(?, ?, ?)",
        (provider, _json(payload), _now_iso()),
    )


def find_source_by_url(data_dir: Path, url: str) -> dict[str, Any] | None:
    conn = connect_sqlite(data_dir)
    try:
        row = conn.execute("SELECT payload_json FROM sources WHERE url_hash = ?", (_hash(url.lower()),)).fetchone()
        return json.loads(row["payload_json"]) if row else None
    finally:
        conn.close()


def find_evidence_by_hash(data_dir: Path, text: str) -> dict[str, Any] | None:
    conn = connect_sqlite(data_dir)
    try:
        row = conn.execute("SELECT payload_json FROM evidence WHERE content_hash = ?", (_hash(text),)).fetchone()
        return json.loads(row["payload_json"]) if row else None
    finally:
        conn.close()


def evidence_for_claim(data_dir: Path, claim_id: str) -> list[dict[str, Any]]:
    conn = connect_sqlite(data_dir)
    try:
        rows = conn.execute(
            """
            SELECT evidence.payload_json
            FROM claim_evidence
            JOIN evidence ON evidence.id = claim_evidence.evidence_id
            WHERE claim_evidence.claim_id = ?
            """,
            (claim_id,),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]
    finally:
        conn.close()


def stale_sources(data_dir: Path, days: int = 90) -> list[dict[str, Any]]:
    threshold = datetime.now(timezone.utc) - timedelta(days=days)
    conn = connect_sqlite(data_dir)
    try:
        rows = conn.execute("SELECT payload_json, retrieved_at FROM sources").fetchall()
        stale = []
        for row in rows:
            retrieved_at = _parse_datetime(str(row["retrieved_at"] or ""))
            if retrieved_at and retrieved_at < threshold:
                stale.append(json.loads(row["payload_json"]))
        return stale
    finally:
        conn.close()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        key = str(row.get("id") or row.get("url") or row.get("text") or index)
        deduped[key] = row
    return list(deduped.values())


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
