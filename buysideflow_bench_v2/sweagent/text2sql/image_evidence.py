"""Keep immutable model/submission-bound images for later visual review."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid


def persist_image_evidence(result, ref_item, gen_item, evaluator_path, contract_path):
    digest = lambda data: hashlib.sha256(data).hexdigest()
    program = {k: gen_item.get(k) for k in ("mode", "sql_code", "python_code", "result_vars")}
    program_hash = digest(json.dumps(program, sort_keys=True, ensure_ascii=False).encode())
    model = str(gen_item.get("model_name") or gen_item.get("model_name_or_path") or "unknown_model")
    safe = lambda text: re.sub(r"[^\w.-]+", "_", str(text))[:100] or "unknown"
    root = Path(os.getenv("TEXT2SQL_IMAGE_ARTIFACT_DIR", "trajectories/text2sql_image_artifacts")).resolve()
    folder = root / safe(model) / safe(result.qid) / program_hash / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    result.image_artifacts = []
    for value in result.gen_result or []:
        if isinstance(value, bytes):
            image_hash = digest(value)
            path = folder / f"image_{len(result.image_artifacts) + 1}_{image_hash[:16]}.png"
            path.write_bytes(value)
            result.image_artifacts.append({"path": str(path), "sha256": image_hash, "bytes": len(value)})
    result.evidence_manifest = str(folder / "manifest.json")
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(), "model": model, "qid": result.qid,
        "submission_program_sha256": program_hash,
        "question_sha256": digest(str(ref_item.get("question") or "").encode()),
        "evaluator_sha256": digest(Path(evaluator_path).read_bytes()),
        "visual_contracts_sha256": digest(Path(contract_path).read_bytes()),
        "judge_model": os.getenv("VISION_JUDGE_MODEL") or os.getenv("VISION_MODEL", "gpt-5.5"),
        "reference_image_sha256": [digest(v) for v in result.ref_result or [] if isinstance(v, bytes)],
        "images": result.image_artifacts,
        "evaluation_complete": result.evaluation_complete,
        "data_passed": result.data_passed, "image_passed": result.image_passed,
        "data_score": result.data_score, "image_score": result.image_score,
        "image_status": result.image_status, "error_type": result.error_type,
        "score_details": result.score_details,
    }
    Path(result.evidence_manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
