"""Load version-locked human evaluation decisions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ADJUDICATIONS_PATH = PROJECT_ROOT / "data" / "evaluation" / "manual_adjudications.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_prediction_sha256(prediction: Any) -> str:
    """Hash one parsed model patch independently of its enclosing preds file."""
    if isinstance(prediction, str):
        try:
            prediction = json.loads(prediction)
        except json.JSONDecodeError:
            pass
    payload = json.dumps(
        prediction,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _prediction_hashes(preds_path: Path) -> dict[str, str]:
    payload = json.loads(preds_path.read_text(encoding="utf-8-sig"))
    predictions: dict[str, Any] = {}
    if isinstance(payload, dict):
        for qid, raw in payload.items():
            prediction = raw.get("model_patch", raw) if isinstance(raw, dict) else raw
            predictions[str(qid)] = prediction
    elif isinstance(payload, list):
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            qid = str(raw.get("instance_id") or "").strip()
            if qid:
                predictions[qid] = raw.get("model_patch", {})
    return {
        qid: canonical_prediction_sha256(prediction)
        for qid, prediction in predictions.items()
    }


def load_run_adjudications(
    preds_path: Path,
    *,
    config_path: Path = DEFAULT_ADJUDICATIONS_PATH,
    project_root: Path = PROJECT_ROOT,
) -> dict[str, dict[str, Any]]:
    """Return decisions matching an exact run or task prediction and reference version."""
    if not config_path.is_file():
        return {}
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("adjudications"), list):
        raise ValueError(f"Invalid manual adjudication config: {config_path}")

    preds_sha256 = sha256_file(preds_path)
    prediction_hashes: dict[str, str] | None = None
    matched: dict[str, dict[str, Any]] = {}
    for raw in payload["adjudications"]:
        if not isinstance(raw, dict):
            continue
        qid = str(raw.get("qid") or "").strip()
        expected_prediction_sha = str(raw.get("prediction_sha256") or "").strip()
        if expected_prediction_sha:
            if prediction_hashes is None:
                prediction_hashes = _prediction_hashes(preds_path)
            if not qid or prediction_hashes.get(qid) != expected_prediction_sha:
                continue
        elif raw.get("preds_sha256") != preds_sha256:
            continue
        if not qid or raw.get("decision") not in {"pass", "fail"}:
            raise ValueError(f"Invalid manual adjudication entry: {raw!r}")
        if qid in matched:
            raise ValueError(f"Duplicate manual adjudication for {qid}: {config_path}")

        artifact = project_root / str(raw.get("reference_artifact") or "")
        expected_artifact_sha = str(raw.get("reference_artifact_sha256") or "")
        if not artifact.is_file() or sha256_file(artifact) != expected_artifact_sha:
            raise ValueError(f"Reference artifact changed for manual adjudication {qid}: {artifact}")
        matched[qid] = dict(raw)
    return matched


def apply_adjudication_to_record(record: dict[str, Any], adjudication: dict[str, Any]) -> None:
    """Apply one validated decision to an evaluation record in place."""
    passed = adjudication["decision"] == "pass"
    record["passed"] = passed
    record["score"] = float(adjudication["score"])
    record["max_score"] = float(adjudication["max_score"])
