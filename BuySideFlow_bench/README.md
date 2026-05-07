# BuySideFlow

BuySideFlow is a benchmark for executable SQL and SQL--Python agents in Chinese buy-side investment research. Each task asks an agent to answer a research-style question under a stated time point, so a valid solution must retrieve data that would have been available at that date, apply the correct financial definition, and return the requested table or artifact.

This repository contains the public audit materials for the benchmark. The underlying financial database is licensed and is not redistributed here.

## Dataset at a Glance

| Item | Count |
| --- | ---: |
| Tasks | 404 |
| Stock / fund / bond / macro | 166 / 122 / 94 / 22 |
| SQL-centered / Python-mediated workflows | 312 / 92 |
| CSV / mixed / figure-oriented tasks | 392 / 5 / 7 |
| Documented schema tables / reference-exercised tables | 108 / 78 |

The benchmark is evaluated against a fixed controlled database snapshot. Public files support inspection of task text, metadata, output contracts, audit rules, reports, tool-use logs, and provenance records. Full execution requires authorized access to the controlled snapshot or use of the official evaluation service.

## Repository Contents

```text
README.md
LICENSE.md
DATA_USE_AGREEMENT.md
CONTROLLED_ACCESS.md
croissant_metadata.json

data/
  dataset.jsonl
  manifest.generated.jsonl
  manifest_overrides.json
  reference_table_usage.md
  reference_table_usage.json
  public_schema_summary.md
  public_ddl_fragment.sql

prompts/
  business_background.txt
  fund_business_rules.txt
  selection_guidance.txt

reports/
  benchmark_*.md
  tool_use_*.csv

figures/
  evaluation_pipeline.pdf
  pit_diagnostics.pdf
  agent_behavior.pdf

Data Boundary
The public release does not include database dumps, raw licensed rows, unrestricted query outputs, credentials, or proprietary source documents used during task construction. Some schema summaries are included where licensing permits; restricted schema assets are available only in the controlled execution environment.

Reference programs and expected outputs follow the same boundary. Hashes and output contracts are provided for audit. Code or artifacts are released only when they do not expose licensed rows or restricted schema details.

Running Evaluation
Full evaluation requires a non-empty DB_SNAPSHOT_ID and access to the controlled database snapshot.
export DB_SNAPSHOT_ID=<controlled_snapshot_id>
python run_eval.py
python run_time_audit.py --data data/dataset
python run_manifest.py --data data/dataset

Evaluation reports record the database snapshot identifier and SHA256 hashes for benchmark sources, schema assets, prompt assets, reference files, sidecars, evaluator code, and judge caches.

Without database access, users can inspect the dataset metadata, run manifest checks, review time-audit rules, and reproduce public report summaries from the released artifacts.

Controlled Access
Authorized users can reproduce full execution in one of two ways:

read-only, query-only access to the fixed controlled database snapshot
official evaluation service that runs submitted workflows against the same snapshot
Access terms are described in CONTROLLED_ACCESS.md. The data-use agreement prohibits data export, redistribution, unrestricted row extraction, and credential sharing.

Versioning
The public audit package and the controlled database snapshot are versioned separately. Evaluation reports record DB_SNAPSHOT_ID and SHA256 hashes for benchmark sources, schema assets, prompt assets, reference files, sidecars, evaluator code, and judge caches.

Metadata
croissant_metadata.json provides machine-readable metadata for the public files, license boundary, responsible-use notes, and controlled-execution requirements.

Review Status
This package is prepared for anonymous review. Non-anonymous repository links, contact details, and organization-specific access information will be added after the review period.

Citation
If you use BuySideFlow, please cite:
@misc{buysideflow2026,
  title = {BuySideFlow: A Time-Anchored Benchmark for Query-and-Analysis Agents in Chinese Buy-Side Investment Research},
  author = {Anonymous Authors},
  year = {2026},
  note = {NeurIPS submission}
}
