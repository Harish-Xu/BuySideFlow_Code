# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

This is **BuySideFlow**, a SWE-agent-based benchmark framework tailored for time-anchored SQL / SQL-Python tasks over the JYDB (聚源) financial database. It extends the SWE-agent codebase with custom tools, prompts, and evaluation logic for generating executable finance research workflows from natural-language questions.

The repo has three main parts:

1. **SWE-agent core** (`sweagent/`) – Agent loop, model interfaces, environment abstractions.
2. **Text2SQL extensions** (`sweagent/text2sql/`) – Schema rendering, code execution, evaluation, DB connector, and benchmark hooks.
3. **Schema filter submodule** (`text2sql-schema-filter-main_v8/`) – Standalone schema-filtering utilities and benchmark datasets.

## Common Commands

### Run the benchmark
```bash
# Run all questions with default model (deepseek/deepseek-chat)
python run_text2sql.py

# Run a slice of questions
python run_text2sql.py --slice :10
python run_text2sql.py --slice 3:8

# Use a different model (presets: deepseek, qwen, kimi)
python run_text2sql.py --model qwen
python run_text2sql.py --model kimi-k2.5

# Filter specific question IDs
python run_text2sql.py --filter 'q1|q2|q3'

# Specify a different benchmark markdown file
python run_text2sql.py --markdown text2sql-schema-filter-main_v8/results/text2sql_fof.markdown
```

### Evaluate predictions independently
```bash
# Evaluate the latest preds.json against the corresponding markdown reference
python run_eval.py

# Evaluate a specific preds.json and markdown pair
python run_eval.py --preds trajectories/.../preds.json --markdown text2sql-schema-filter-main_v8/results/text2sql.markdown

# Evaluate only selected question IDs
python run_eval.py --qids q1,q3,q5
```

### Load environment variables
The project reads API keys and DB credentials from `sweagent/.env`. Most entry scripts already call `load_dotenv(PROJECT_ROOT / "sweagent" / ".env")` automatically.

## High-Level Architecture

### Agent loop & Text2SQL integration
- `sweagent/agent/agents.py` – The main agent loop. Text2SQL-specific logic intercepts actions before they are sent to the environment:
  - Schema tools (`describe_tables`, `search_tables`, `get_columns`, `search_columns`, `request_schema`) are handled **in-process** (no container round-trip) and cached per instance to avoid duplicate LLM context.
  - `run_code` is also handled in-process: it executes SQL/Python via `sweagent/text2sql/evaluator.py` and returns the result as an observation.
  - `submit` validates that at least one `run_code` was called; if not, the submission is rejected with a warning.
- `sweagent/agent/models.py` – LiteLLM wrapper with retry logic, cost tracking, and special handling for reasoning models (DeepSeek, Kimi K2.5, MiniMax, Gemini).

### Tools & prompts
- `sweagent/tools/text2sql/config.yaml` – Declares the Text2SQL tools (`describe_tables`, `search_tables`, `get_columns`, `search_columns`, `request_schema`, `run_code`, `submit`).
- `sweagent/config/text2sql_default.yaml` – Default agent configuration. The `system_template` is written in Chinese and defines a strict 5-step workflow (选表 → 确认列名 → 验证列语义 → 编写代码 → 提交前自查) plus mandatory `run_code` before `submit`.
- `sweagent/config/text2sql_qwen_latest.yaml` and `text2sql_kimi_latest.yaml` – Model-specific overrides.

### Benchmark loading & evaluation
- `sweagent/run/batch_instances.py` – `Text2SQLInstances` loads tasks from a markdown file (or JSON), builds focused schema catalogs, and injects business metadata (`fund_rules`, `business_background`, `table_name_list`) into each problem statement.
- `sweagent/run/hooks/text2sql_evaluate.py` – Post-batch hook that compares every generated submission against the reference answer and writes a markdown report (`benchmark_<model>_<timestamp>.md`).
- `sweagent/text2sql/markdown.py` – Parses the rich markdown benchmark format (questions, reference SQL/Python, schema tables, evaluation contracts).

### Code execution & result comparison
- `sweagent/text2sql/evaluator.py` –
  - `execute_code()` runs SQL through `db_connector.query_to_dataframe` or executes Python in a restricted globals namespace (pre-loaded with `pandas`, `numpy`, `pymysql`, `matplotlib`).
  - Python reference code often lacks explicit `result_vars`; the system first asks an LLM (`get_result_vars_ai`) to extract them, then falls back to AST heuristics (`_fallback_result_vars`).
  - `_compare_result_lists()` compares outputs with DataFrame tolerance (±2% numeric tolerance, scale-factor normalization for 万/百万/亿), image comparison via vision LLM, and free-text judgment via an LLM judge.
- `sweagent/text2sql/db_connector.py` – MySQL 8.0 connector for JYDB. It auto-backtick-quotes MySQL reserved words used as aliases/CTE names and respects `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` from the environment.

### Schema assets
- `sweagent/text2sql/assets/` contains:
  - `gildata_schema.json` – Full schema (used by `request_schema`).
  - `gildata_schema_catalog.json` – Slim catalog (shown upfront to the model; core tables have full columns, others are truncated).
  - `business_background.txt`, `fund_business_rules.txt`, `selection_guidance.txt` – Prompt background injections.

## Important Constraints & Behaviors

- **No formal unit-test suite** exists in this repo. Correctness is verified by running the benchmark and inspecting the evaluation report.
- **Windows compatibility**: `run_text2sql.py` patches `pexpect.spawn` with a subprocess-based implementation on Windows (`swerex.runtime._windows_spawn`).
- **Model presets**: `run_text2sql.py` maps aliases like `qwen` → `qwen3-max` and `kimi` → `kimi-k2.5`, and automatically appends the litellm model registry path argument when needed.
- **Run-code guardrails**: The agent enforces that `run_code` must be called at least once before `submit`. Identical repeated `run_code` payloads are detected and warned after 3 occurrences.
- **Trajectory outputs**: Batch runs write per-instance trajectories under `trajectories/<user>/<config>__<model>___<source>/`, plus a merged `preds.json`.
