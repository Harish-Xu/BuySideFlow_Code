from __future__ import annotations

import hashlib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

SHARED_ASSETS: dict[str, Path] = {
    "schema": PROJECT_ROOT / "sweagent/text2sql/assets/gildata_schema.json",
    "table_catalog": PROJECT_ROOT / "sweagent/text2sql/assets/gildata_table_catalog.json",
    "business_background": PROJECT_ROOT / "sweagent/text2sql/assets/business_background.txt",
    "selection_guidance": PROJECT_ROOT / "sweagent/text2sql/assets/selection_guidance.txt",
    "fund_rules": PROJECT_ROOT / "sweagent/text2sql/assets/fund_business_rules.txt",
    "bond_rules": PROJECT_ROOT / "sweagent/text2sql/assets/bond_business_rules.txt",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def shared_asset_manifest() -> dict[str, dict[str, str]]:
    """Return the resolved paths and hashes of the shared benchmark inputs."""
    missing = [str(path) for path in SHARED_ASSETS.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing shared assets: " + ", ".join(missing))
    return {
        name: {
            "path": str(path.relative_to(PROJECT_ROOT)),
            "sha256": sha256_file(path),
        }
        for name, path in SHARED_ASSETS.items()
    }

