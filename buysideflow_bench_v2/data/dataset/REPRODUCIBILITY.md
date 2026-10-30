# Reproducing task construction and result evaluation

This guide describes the canonical task files and evaluator inspected on 24 September 2026. It connects the paper's construction example and evaluation table to existing implementation and evidence. The paper's model results and human submission audit still describe the 18 September version; running today's code on today's tasks does not reproduce that historical score table.

## What is supplied

Run commands from the repository root, in the project's configured Python environment. Database execution requires authorized read-only access to the MySQL 8.0 `jydb` sample. Credentials are loaded through the existing environment path; they are not part of the task package. Model-assisted output extraction and image/text comparison additionally require their configured model endpoints. Ordinary explicit-output table comparison does not itself require an LLM judge.

| Material | Repository location | Use |
|---|---|---|
| Canonical questions | `data/dataset/question/dataset.jsonl` | Instructions, optional structured inputs and `strict_output_schema` |
| Reference package | `data/dataset/results/<instance_id>/` | Reference SQL/Python and saved CSV/image/text artifacts; auxiliary inputs where present |
| Schema and shared rules | `sweagent/text2sql/assets/gildata_schema.json`, `business_background.txt` | Field meanings, join keys, financial and time conventions |
| Domain rules | `sweagent/text2sql/assets/fund_business_rules.txt`, `bond_business_rules.txt` | Additional conventions injected for the relevant task domain |
| Loader | `sweagent/text2sql/markdown.py`, `_parse_folder_text2sql_tasks` | Join a task record to its reference directory and infer output kind from artifacts |
| Reference replay | `run_reference_replay.py` | Regenerate outputs in a temporary copy with saved primary outputs removed |
| Result evaluation | `run_eval.py`; `sweagent/text2sql/evaluator.py`, `compare_items` | Execute submissions, extract outputs and compare with references |
| Historical-data evidence | [Validation record](../../output/paper/historical-data-validation/README.md) | Retained-record checks, separate recomputations and unresolved coverage |

References are evaluator-side materials and are hidden from the agent during execution. The derived `manifest.generated.jsonl` describes the release; it is not the task loader's source of truth. The presence of a CSV header alone does not set a strict output contract.

## Agent interaction and execution settings

The agent receives the question, structured inputs, task rules and table-name index. It chooses schema tools as needed, executes candidate SQL/Python through `run_code`, inspects its own outputs or errors, and submits a final program. `run_code` provides no hidden-reference comparison. The evaluator re-executes the submitted program for scoring.

The normal submission handler requires at least one execution and a successful latest execution. It does not compare the submitted payload with the last successful payload. The instructions against SQL comments, fabricated data and silent exceptions are prompt requirements, not comprehensive static checks. `get_trading_days` wraps a query to `qt_tradingdaynew`; the agent can also query the calendar directly. These interaction rules are independent of `strict_output_schema`.

The following is a read-only inventory of the 2,040 per-task `.config.yaml` files currently retained under `trajectories/bod`, inspected on 24 September. Each model has 408 records. All specify temperature `0`, `top_p: null`, and `parallel_tool_calls: false`. Some YAML files contain a JSON string and require decoding that string to read the configuration.

| Model | Explicit `max_steps=300` | No stored `max_steps` | Stored per-instance cost limit |
|---|---:|---:|---|
| Claude Opus 4.7 | 408 | 0 | `0` for all 408 |
| DeepSeek V4 Flash | 308 | 100 | `0` for 308; `3` for 100 |
| GLM-5.1 | 392 | 16 | `0` for all 408 |
| GPT-5.5 | 408 | 0 | `0` for all 408 |
| Qwen3.6 Plus | 352 | 56 | `0` for all 408 |

A zero cost-limit setting disables that cap; it is not a measured zero cost. An omitted step limit does not establish an effective historical limit without the matching runtime. The generic tool timeout stored in these files does not determine the SQL/Python subprocess timeout. Process-level overrides, execution-call limits, and judge settings must be recovered from the corresponding run environment. These retained files include later targeted reruns and are not a complete reconstruction of the frozen 18 September experiment. Do not substitute today's defaults for missing historical settings.

### Runtime budget semantics (implementation inspected on 28 September 2026)

Interaction steps count the agent's action–feedback loop, not internal reasoning tokens. The default template sets `agent.max_steps=300`; the loop uses the resolved agent setting unless `TEXT2SQL_ABLATION_MAX_STEPS` overrides it. Before a new step, the runtime checks `len(trajectory) + 1 >= max_steps`. At the threshold, it ends the loop and appends a termination record instead of taking another ordinary agent step. Thus, this setting is not a promise of 300 model reasoning calls. A termination record alone does not establish a valid submitted program.

The separate `TEXT2SQL_RUN_CODE_TOTAL_LIMIT` defaults to `100` counted executions per task. Positive values enable the cap; zero or negative values disable this particular cap. The counter increments after execution for both successful runs and execution errors. Timeouts, invalid payloads, and requests rejected before execution do not increment it. Once the counter reaches the limit, further `run_code` requests are refused; schema inspection and submission remain available within the remaining interaction budget. Consequently, the number of attempted `run_code` requests can exceed the configured execution count.

Implementation entry points are `DefaultAgent.run` and `_handle_text2sql_run_code` in `sweagent/agent/agents.py`, and `build_run_code_observation` in `sweagent/text2sql/runtime.py`. These are current runtime rules, not recovered settings for every historical submission. The configuration inventory above retains its 24 September scope.

## Worked construction example: stock_002

The following explains the current task's formalization. It is not a verbatim quotation from an archived practitioner request, and it does not reconstruct the chronology of every earlier revision.

| Decision | Concrete implementation | Artifact or acceptance evidence |
|---|---|---|
| Research objective | Find listed machinery companies with accelerating single-quarter revenue growth | Current `stock_002` instruction in the canonical JSONL |
| Fix scope and reporting conventions | Shanghai/Shenzhen main-board A-shares; end of 2023-10-31; consolidated revenue; Shenwan level-1 machinery | Instruction plus shared rules and authoritative schema comments |
| Fix output contract | Six columns; growth in percent and difference in percentage points; two displayed decimals; filter/sort on unrounded values | `strict_output_schema=true`, instruction and `results/stock_002/result.csv` |
| Map dependencies | `secumain` security/company keys; `lc_liststatus` events; `lc_exgindustry` history; `lc_maindatanew` revenue | [`refer.sql`](results/stock_002/refer.sql) CTEs and schema definitions |
| Apply temporal selection | Exclusive cutoff `2023-11-01`; filter availability/validity before ranking records | `listed_ranked`, `industry_ranked`, `revenue_ranked` |
| Calculate and serialize | Derive Q2/Q3 from cumulative revenues, calculate growth, filter and sort in SQL; Python writes CSV | [`refer.py`](results/stock_002/refer.py) and saved output |
| Check data and arithmetic | Enumerate candidates, verify six required periods per company, retain selected record IDs, recompute from extracted rows | [Validation evidence](../../output/paper/historical-data-validation/README.md) |

The evidence files below are relative to `output/paper/historical-data-validation/` at the repository root.

Listing events join by `INNERCODE`. The reference restricts `CHANGEDATE` and `INSERTTIME` to before the cutoff, orders by `CHANGEDATE, INSERTTIME, JSID, ID` descending, and retains eligible latest event codes `(1,3,6)`. `INSERTTIME` is the background-permitted availability proxy for this source, not an asserted public-release date. The base query uses markets `(83,90)`, A-share category `1`, sectors `(1,2)`, a pre-cutoff listing date, a company key, and non-placeholder security codes.

Industry history joins by `COMPANYCODE`, uses `STANDARD=38`, `INFOPUBLDATE < cutoff`, and `CANCELDATE IS NULL OR CANCELDATE >= cutoff`. Eligible rows are ordered by publication time, insertion time, `JSID` and `ID`, all descending. The reference then keeps machinery companies, resolving multiple security records per company by security code and inner code.

Revenue is needed for March, June and September 2022 and 2023. After `INFOPUBLDATE < cutoff`, rows are ranked within company and period by `MARK` priority `1 > 8 > 7 > 6 > 2`, then descending publication time, insertion time, `JSID` and `ID`. `MARK` values 4/5 are quarter-only inputs and are excluded from the cumulative series. Q2 is half-year minus Q1 revenue; Q3 is nine-month minus half-year revenue. Growth is `100 * (current_quarter / prior_year_quarter - 1)`, with positive prior-year denominators and nonmissing current values. The final conditions are `Q3_growth > 20` and `Q3_growth - Q2_growth > 10`.

The 24 September retained-record checks establish the following:

| Acceptance check | Observed result | Evidence file under the validation directory |
|---|---|---|
| Candidate accounting before final thresholds | 3,391 base records; 206 excluded by listing state and 2,946 by industry; 239 machinery candidates | `build/stock002_candidate_trace.json` |
| Initial listing state | All 3,391 candidates have an initial listing date agreeing with the master; no same-day event ties in the extract | `build/case_results.json`, `build/raw/stock002_listing_events.json` |
| Required report-period grid | 1,434/1,434 company-period combinations have eligible, nonmissing revenue; 712 have multiple eligible versions | `build/stock002_financial_grid.json` |
| Financial selection | No selected financial row has a post-cutoff publication date | Selected IDs in the grid and `build/raw/stock002_revenue_versions.json` |
| Independent arithmetic | Separate Python/Decimal computation matches all 32 rows, values and order | `source/analyze_cases.py`, `build/stock002_calculations.json`, `build/stock002_output.json` |

For security `002529`, the stored output gives Q2 growth `-24.66`, Q3 growth `192.62`, and an increase of `217.28`. The first two values are percentages; the last is percentage points. The six-column CSV has 32 data rows.

These checks do not resolve 13 non-placeholder master records with missing listing dates and no pre-cutoff listing event. External universe completeness, historical stability of master attributes, and retention of overwritten versions remain unestablished. The detailed validation record preserves these boundaries. Its ledger covers 408 tasks, but only three paths received the documented supplementary database checks.

### Replay without replacing the saved answer

Use the existing environment settings for database access and snapshot identity. With authorized access to the documented snapshot:

```bash
python run_reference_replay.py --data data/dataset --qids stock_002 --workers 1 --output-dir outputs/reference_replay/reproduction
```

Inspect the replay report for execution status, generated artifacts, exact/equivalent comparison and differences. This verifies reference regeneration against the saved answer; candidate and historical-version coverage require the evidence checks above. To recompute the three existing data-validation examples from their locally retained extracts without accessing the database:

```bash
python output/paper/historical-data-validation/source/analyze_cases.py
```

This rewrites derived analysis files in the validation directory, not canonical reference answers. Raw extracts are licensed data and remain subject to controlled access. `source/queries/` and the two query manifests record the actual bounded SELECT queries, extraction counts and source binding. They are validation queries, not the original production-to-sample export procedure.

## Result-evaluation rules and entry points

The current loader yields 400 CSV tasks (374 strict, 26 non-strict), six image-oriented tasks and two mixed tasks. Across all output kinds, 379 records have `strict_output_schema=true`; five of those are image/mixed tasks. Figure 3 in the paper retains the earlier composition used for the 18 September evaluation (401 CSV, six image-oriented, one mixed, 380 strict flags). Table 8 documents the current implementation. These scopes must remain explicit when comparing or reproducing scores.

| Rule | Current behavior | Implementation in `evaluator.py` |
|---|---|---|
| Reference source | Load saved CSV/image/text artifacts first, then direct result objects; execute code only when direct results are unavailable | `_get_direct_result_objects`, `_resolve_item_execution` |
| Submission extraction | SQL returns a DataFrame. Python uses explicit `result_vars`; figures are captured. Unresolved outputs may use an LLM extraction plan with append-only extraction code and re-execution, then heuristic variables/printed output | `_resolve_item_execution`, `execute_code`, `get_result_plan_ai` |
| Explicit outputs | Successfully resolved explicit outputs are compared as submitted, not replaced merely because they mismatch the reference | `_resolve_item_execution` |
| Strict tables | Require output count, column count, normalized column names and column order. Compare by position. Header normalization removes whitespace/BOM and ignores ASCII case | `_df_schema_matches`, `_dataframes_match`, `_compare_result_lists` |
| Loose tables | Match columns by values, allow extra columns, then try merged frames and flattened values. Later fallbacks may discard table boundaries and shape | `_df_values_match`, `_merge_results_for_comparison`, `_flat_values_match` |
| Numeric precision | First recognized decimal-place instruction; otherwise four places. Numeric rounding precedes comparison; this is not an arbitrary relative-error tolerance | `_extract_compare_decimals`, `_normalize_df`, `_numeric_values_match` |
| Numeric scales | Try factors 100, 10^4, 10^6, 10^8 and their reciprocals unless the task contains a recognized bp/bps/basis-point expression or is `bond_062`; percentage-scale comparison adjusts precision and uses Decimal half-up normalization | `_compare_result_lists`, `_cols_match`, `_scaled_compare_decimals` |
| Order and duplicates | Recognized ascending/descending keywords preserve output order. Otherwise rows are sorted by normalized keys. Frame comparisons retain row counts and duplicate multiplicities | `_question_has_order`, `_normalize_df`, `_df_values_match` |
| Missing values and display | Missing values and null-like strings normalize to empty; whitespace is normalized; recognized security-code suffixes are stripped. Timestamp objects become date strings. Task-specific aliases include `stock_035` labels and `bond_062` percent signs | `_normalized_text`, `_normalize_value`, `_normalize_equivalent_task_values` |
| Images/mixed | Use provided checklists or model-extracted checklists. Compare table components deterministically, text/images with model judges. Present categories use normalized weights CSV:image:text = 6:4:2 | `_build_visual_checklist`, `_score_visual_outputs` |
| Full pass | Tabular comparison returns true. Visual/mixed weighted score must be at least 0.999 and every mandatory score at least 0.999. A cosmetic item's lower item-level pass threshold does not override this final gate | `_compare_result_lists`, `_score_visual_outputs` |
| Manual decisions and PIT | Apply version-bound result adjudications separately. PIT diagnostic flags are inspection candidates, not automatic human PIT labels | `run_eval.py`, `manual_adjudications.py`, `compare_items` |

The documented scale and loose-comparison paths describe actual implementation behavior; they do not establish that every accepted transformation preserves every task's semantics. Changes to these rules require a new evaluator version and corresponding re-evaluation.

For a designated set of saved submissions, pass its exact prediction file explicitly:

```bash
python run_eval.py --data data/dataset --preds /path/to/run/preds.json --qids stock_002 --num-workers 1 --output outputs/evaluation/reproduction/report.md
```

Replace `/path/to/run/preds.json` with the intended run. Bind it to the matching task/reference/evaluator version and retain its runtime configuration and adjudications. The command evaluates current supplied files; it does not restore historical versions automatically. `--output` controls the report destination; inspect the run directory's adjudications and configuration when reproducing an existing decision.

Model-assisted behavior depends on `VAR_EXTRACT_MODEL`, `TEXT_JUDGE_MODEL`, `VISION_CHECKLIST_MODEL`, and `VISION_JUDGE_MODEL`/`VISION_MODEL`. Current code defaults are `deepseek/deepseek-v4-pro` for extraction/text and `kimi-k2.6` for vision. These defaults do not identify historical run settings. Judge prompts are in the corresponding functions; comparison/checklist caches use `TEXT2SQL_JUDGE_CACHE_DIR`. Preserve the actual configuration and saved judge responses when available; a fresh model call can differ from a cached decision.

## Database sample and retained records

The evaluation environment is the shared `jydb` sample on MySQL, identified as `jydb_20260430_v1`. Its table inventory contains 110 tables and 4,547 columns. The maintainer's statement is that this schema has not changed since 30 April 2026; it is a schema-level logical snapshot, not evidence that the entire shared MySQL instance is physically read-only. The paper describes licensed read-only access by application. This guide adds no credentials, public database dump or new access service.

The current records are not uniformly prefiltered to each task's research date. The `stock_002` revenue extract retains 1,554 financial rows published after its cutoff; the reference filters them during execution. Its historical candidate set also retains companies that were subsequently delisted. The macro example retains an August 2025 observation released after its September 5 cutoff. These are observed retention properties of the checked paths, not a complete description of the original sampling algorithm.

The inspected materials do not contain the original production-to-sample extraction manifest or complete table-by-table object/date/version retention rules. An independently identical sample cannot be reconstructed from schema definitions alone. Reusing this benchmark therefore requires access to the identified sample; rebuilding it from a licensed production source additionally requires the extraction rules from the data maintainer.

When extending the benchmark, keep the same task-package structure: specify the universe and dates, map source keys and time meanings, write a reference, retain output and validation evidence, then review unresolved conditions before release. Data selection must cover candidates before final ranking, initial states and subsequent changes, eligible competing versions, and required lookback/base/label periods. Treat these as explicit validation targets for the new task, not as evidence that every existing task has already passed them.
