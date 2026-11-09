from __future__ import annotations
import json, shutil, sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]; CORE=ROOT.parent/"artifact-lifecycle-core"; sys.path.insert(0,str(CORE))
import artifact_lifecycle_core as core
import research_evidence_adapter as adapter
CONFIG=ROOT/"artifact-lifecycle/research-evidence-lifecycle.v1.json"; FIXTURE=ROOT/"tests/fixtures/artifact-lifecycle/research-evidence.v1.json"
def _root(tmp_path):
    repo=tmp_path/"repo"; config=repo/"artifact-lifecycle"/CONFIG.name; config.parent.mkdir(parents=True); shutil.copy2(CONFIG,config)
    root=repo/"tests/fixtures/artifact-lifecycle/research-evidence"
    for item in json.loads(FIXTURE.read_text(encoding="utf-8"))["files"]:
        path=root/item["path"]; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(item["content"],encoding="utf-8")
    return config,root
def test_arw_lifecycle_scan_plan_and_negative_gates(tmp_path):
    config,root=_root(tmp_path); quarantine=tmp_path/"quarantine"; quarantine.mkdir()
    first=adapter.build_manifest(config,root,"arw-lifecycle-001",tmp_path/"checkpoint.sqlite3"); second=adapter.build_manifest(config,root,"arw-lifecycle-001",tmp_path/"checkpoint.sqlite3")
    assert first["manifest_sha256"]==second["manifest_sha256"]
    plan=core.plan_operation(first,root,quarantine,"quarantine",owner_state="idle_confirmed",process_state="idle_confirmed",dispatch_state="none_active")
    assert next(item for item in plan["entries"] if item["source_path"].endswith("failed-retrieval.tmp"))["eligible"] is True
    assert "artifact_pinned" in next(item for item in plan["entries"] if item["source_path"].endswith("guarded-answer.json"))["blocking_gates"]
    (root/"cache/failed-retrieval.tmp").write_text("drift",encoding="utf-8")
    assert core.verify_plan(plan,first,root,quarantine)["ok"] is False
    with pytest.raises(adapter.AdapterError): adapter.build_manifest(config,tmp_path,"arw-lifecycle-001",tmp_path/"escape.sqlite3")
