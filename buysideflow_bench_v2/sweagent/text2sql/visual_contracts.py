"""Question-bound image requirements, independent of reference styling."""
import hashlib
import json
import math
from pathlib import Path


CONTRACT_PATH = Path(__file__).resolve().parents[1] / "config/text2sql_visual_contracts.json"


class VisualContractError(ValueError):
    pass


class VisualJudgeError(RuntimeError):
    pass


def validate_items(items, question):
    if not isinstance(items, list) or not items:
        raise VisualContractError("Missing visual requirements")
    seen = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or item["id"] in seen:
            raise VisualContractError("Invalid or duplicate visual requirement")
        seen.add(item["id"])
        quote = item.get("requirement_quote")
        if not isinstance(quote, str) or not quote.strip() or quote not in question:
            raise VisualContractError(f"Requirement {item['id']} has no exact question evidence")
        if not item.get("expected") or item.get("op") in {"table_ref_equal", "text_semantic_match"}:
            raise VisualContractError(f"Invalid visual requirement {item['id']}")
        if not isinstance(item.get("mandatory"), bool):
            raise VisualContractError("mandatory must be boolean")
        weight = item.get("weight", 1.0)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0:
            raise VisualContractError("Visual requirement weight must be positive and finite")
    return items


def question_contract(qid, question):
    document = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    contract = document["contracts"].get(str(qid))
    if contract is None:
        return None
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()
    accepted = [contract["question_sha256"], *contract.get("historical_question_sha256", [])]
    if digest not in accepted:
        raise VisualContractError(f"Visual requirements for {qid} do not match this question version")
    return validate_items(contract["items"], question)
