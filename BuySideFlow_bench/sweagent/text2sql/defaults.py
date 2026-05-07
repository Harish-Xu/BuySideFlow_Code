from __future__ import annotations

from pathlib import Path


TEXT2SQL_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
DEFAULT_SCHEMA_PATH = TEXT2SQL_ASSETS_DIR / "gildata_schema.json"
DEFAULT_CATALOG_SCHEMA_PATH = TEXT2SQL_ASSETS_DIR / "gildata_table_catalog.json"
DEFAULT_BACKGROUND_PATH = TEXT2SQL_ASSETS_DIR / "business_background.txt"
DEFAULT_SELECTION_GUIDANCE_PATH = TEXT2SQL_ASSETS_DIR / "selection_guidance.txt"
DEFAULT_FUND_RULES_PATH = TEXT2SQL_ASSETS_DIR / "fund_business_rules.txt"
