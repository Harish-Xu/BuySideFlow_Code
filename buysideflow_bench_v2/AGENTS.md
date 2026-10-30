# AGENTS.md

## Purpose

This repository runs and evaluates a SWE-agent-based Text-to-SQL/SQL+Python benchmark for the JYDB financial database.

## Read by task

- Start with `README.md` for entry points, the canonical dataset, and reproducibility commands.
- Agent loop or tool dispatch: `sweagent/agent/agents.py`.
- Text-to-SQL execution and comparison: `sweagent/text2sql/`.
- Runtime prompt and tool contracts: `sweagent/config/text2sql_default.yaml` and `sweagent/tools/text2sql/config.yaml`.
- Financial semantics: `sweagent/text2sql/assets/`; treat these rules and schema comments as domain data, not generic coding guidance.
- Benchmark loading and report generation: `sweagent/run/batch_instances.py` and `sweagent/run/hooks/text2sql_evaluate.py`.

Read further only when the task reaches that surface. Trace the effective load path before changing a prompt or configuration; similarly named legacy files and generated run configs may not be active.

## Invariants

- `data/dataset/` is the canonical benchmark. Do not silently fall back to legacy task sources.
- Preserve point-in-time correctness, no-look-ahead behavior, output contracts, and database-snapshot provenance when changing data, prompts, execution, or evaluation.
- Treat the JYDB schema and table/column comments as authoritative. Keep large database queries bounded by date and candidate universe.
- A Text-to-SQL answer must be exercised through `run_code` before `submit`; the runtime enforces this gate.
- Keep credentials in the existing environment-loading path. Never print, commit, or copy secrets into configs or artifacts.
- `outputs/`, `trajectories/`, generated manifests, and caches are derived artifacts; change them only when the task explicitly concerns those artifacts.
- Preserve unrelated user changes.

## Completion and verification

Implement the requested outcome completely, including directly affected documentation or tests. Choose checks in proportion to the change: start with the closest existing test or parser/load check, and broaden only when failures or cross-cutting risk justify it.

After changing any canonical question, comparison rules, reference contract, or manifest record, run `python3 -B scripts/verify_dataset_consistency.py` before reporting completion. Questions, reviewed-rule fingerprints, and manifest metadata must describe the same version. Review semantic changes before rebinding hashes; keep stale-binding failures enabled. A successful single-task check does not replace this full-loader consistency gate.

Full benchmark runs, database replays, and publication audits are slow or credential-dependent. Run them only when the changed surface requires them or the user asks; `README.md` lists the relevant commands. If an external dependency prevents validation, report the exact unverified boundary.
