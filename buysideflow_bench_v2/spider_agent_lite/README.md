# Spider-Agent-Lite integration

This directory contains a pinned copy of the official Spider-Agent-Lite method
and the BuySideFlow-specific integration boundary.

## Layout

- `upstream/`: byte-for-byte source files copied from the official Spider 2.0
  repository. Do not edit these files locally.
- `adapter/`: BuySideFlow task, asset, JYDB runtime, and trajectory adapters.
- `config/text2sql.yaml`: integration contract and shared resource paths.
- `tests/`: offline checks for provenance, shared inputs, and adapter contracts.

The canonical benchmark remains `data/dataset/`. Financial rules and JYDB
schema files are shared from `sweagent/text2sql/assets/`; they are not copied
into this directory. The existing evaluator remains the authority for scores.

The BuySideFlow environment uses the existing host-side JYDB runtime. It
exposes the same schema helpers, trading calendar, `run_code`, and `submit`
contracts while retaining the upstream `PromptAgent.run` and `predict`
decision loop. The submit gate matches the formal runtime: at least one
`run_code` call is required and the latest call must have succeeded. Raw Spider
trajectories and the standard submission JSON are both retained.

Run the offline integration check with:

```bash
python spider_agent_lite/run_text2sql.py --check
python -m unittest discover -s spider_agent_lite/tests -v
```

No model or database call is made by either command.

The pinned upstream source imports all supported model-provider SDKs at module
load time. Install those runtime-only packages before an agent run:

Use a separate Spider benchmark environment with the project's base dependencies
available, so installing these SDKs does not upgrade the original SWE-agent
environment. The launcher changes themselves install or update no dependencies.

```bash
python -m pip install -r spider_agent_lite/requirements-runtime.txt
```

Run one task after loading the normal benchmark environment and immutable
snapshot identity:

```bash
export DB_SNAPSHOT_ID=jydb_YYYYMMDD_v1
python spider_agent_lite/run_text2sql.py \
  --task bond_001 \
  --model deepseek \
  --run-name pilot-deepseek \
  --output-dir trajectories/spider_agent_lite
```

`--model deepseek` resolves to the benchmark's current
`deepseek/deepseek-v4-flash` preset and uses the same LiteLLM registry,
`DEEPSEEK_API_KEY`, and optional `DEEPSEEK_API_BASE` loaded by the existing
benchmark environment. `deepseek-v4-pro` selects the registered pro preset.
The DeepSeek defaults also match the formal SWE-agent baseline request budget:
`temperature=0.0`, omitted `top_p`, `max_tokens=65536`, `max_steps=300`, and
20 model-call attempts with 10-to-120-second backoff. A step is defined by its
agent loop, so cross-workflow reports must still include actual model and tool
calls rather than treating equal step ceilings as equal work.

The runner writes `result.json` with the raw decision trajectory and, after an
accepted submission, `submission.json`. New runs use
`<output-dir>/<run-name>/spider_agent_lite__<model>/<task-id>/`, with model slashes
replaced by `--`. Omitting `--run-name` creates a timestamped run. Existing raw
run directories remain readable without renaming.

## Full canonical batch

Use `--all` explicitly to run every task loaded from `data/dataset/`. The current
dataset contains 408 tasks. `--num_workers` and `--num-workers` are equivalent;
the default is one worker. They control spawned task processes, not parallel
actions inside one Agent. The official `PromptAgent.run` and `predict` loops,
request budgets, runtime tools and scoring implementation remain unchanged.

```bash
export DB_SNAPSHOT_ID=jydb_YYYYMMDD_v1
python spider_agent_lite/run_text2sql.py \
  --all \
  --model deepseek \
  --num_workers 5 \
  --run-name pilot-deepseek-20260920 \
  --output-dir trajectories/spider_agent_lite
```

The coordinator resolves the run name once, loads the canonical task list and
uses a `spawn` process pool. Workers share one model directory but write only
their own task artifacts. Relative candidate files and temporary files are
isolated under `<model-dir>/.workspaces/<task-id>/`. After workers stop, the
coordinator performs one export of the unified predictions, generation report
and tool-use CSV. Single-task `--task` runs keep their previous behavior.

Each batch reserves a new model directory. An existing directory is an error,
not an instruction to overwrite or resume. Use a new run name for a new run;
there is no automatic retry based on results or scores. Missing optional SDKs
fail before batch directories or workers are created. Install the runtime
requirements above before launching a real batch.

`batch_manifest.json` records the expected task IDs, settings, worker limit,
database snapshot identity, public input/source fingerprints and worker errors.
No credentials or environment file contents are copied. Fingerprints are checked
before each task, by the parent at most every five seconds while waiting, and
again before export; a source inventory change, snapshot
identity change or expired snapshot cutoff prevents a comparable export.
The source boundary covers canonical data, shared active assets, the model
registry, the adapter, pinned upstream, and the shared Python files reached by
static imports (including imports inside tool functions). Unused SWE configs,
alternate ablation assets and generated images are not batch inputs. This remains
file-level SHA256 checking: editing a real dependency such as `defaults.py`
still stops the run, even if the edit only adds an unused constant. There is no
semantic-diff exemption and historical manifests retain their original scope.
Optional no-PIT path constants live in the existing top-level `run_text2sql.py`
entry point, not in the shared default module used by Spider.
The coordinator keeps at most `num_workers` futures in flight, with no full-batch
queued backlog. On the first source drift it persists `status=stopping` and the
reason, cancels cancellable futures, and starts no further tasks. Already active
tasks may finish; final provenance failure blocks report export even if the file
was subsequently restored. Progress and errors are persisted after each event;
`active_task_ids` and `not_started_task_ids` distinguish ongoing from undispatched
work. `completed_task_ids` tracks ended worker futures, not accepted submissions.
File fingerprints and snapshot labels do not certify a physically immutable
database, and the isolated working directories are not a filesystem sandbox.

The generation report distinguishes completed loops, accepted submissions,
unsubmitted records, worker errors and missing/unreadable evidence. It lists
every expected task, even if a process failed before saving a result. Missing
predictions have an empty `model_patch`, not a synthetic submission or trajectory;
their unknown tool counts remain blank. A nonzero batch exit signals worker,
pool, interruption or provenance failure. Recorded submissions still require
independent scoring and are never labelled Pass by generation.
Ctrl-C cancels jobs not yet started; already running calls may finish before
the coordinator exports partial coverage. It does not silently drain or retry
the remaining full benchmark. Keep SDKs and environment settings fixed too;
the raw results record the runtime package versions actually used.

After each single task, or once after a batch, the model directory also receives:

- `benchmark_spider_agent_lite__<model>_<run-name>_generation.md`: generation
  status, task coverage, runtime evidence, and an independent evaluation command.
- `tool_use_spider_agent_lite__<model>_<run-name>_generation.csv`: per-task tool
  attempts in the shared benchmark format, with score fields left blank.
- `preds.json` and `<task-id>/<task-id>.traj`: inputs for the existing `run_eval.py`.

These summaries cover the available results, including incomplete tasks. They
do not invoke evaluation or claim a passing score. The evaluation command in
the report writes separate scored `benchmark_*.md` and `tool_use_*.csv` files.
Use the recorded database snapshot when evaluating. Do not label a smoke run
`current408` or reuse a historical baseline's date or scores.

For a batch, the evaluation command in the generation report first runs the
read-only `--report-only` source/coverage check and then invokes the existing
`run_eval.py`. A failed check prevents evaluation. Scoring remains a separate
step, defaults to one evaluation worker and writes one scored report and
tool-use CSV for the unified predictions; no scoring rules are changed.

Backfill reports for an existing model directory without model or database calls:

```bash
python spider_agent_lite/run_text2sql.py --report-only \
  trajectories/spider_agent_lite/pilot-deepseek/spider_agent_lite__deepseek--deepseek-v4-flash
```

Do not call `--report-only` while batch workers are running. Completed batches
automatically reuse their recorded expected-task list for backfill; raw results
and existing scored reports are preserved. Separate standalone processes still
need distinct run names because their default per-task exports are not coordinated.
Use `--all --num_workers N` for a single logical concurrent batch instead.

### Safe continuation after source drift

An incomplete stopped batch can retain compatible saved tasks (including loops
that never submitted) and generate only untouched missing tasks:

```bash
python spider_agent_lite/run_text2sql.py --all --model deepseek --num_workers 5 \
  --run-name pilot-deepseek-20260920 --output-dir trajectories/spider_agent_lite \
  --resume-from trajectories/spider_agent_lite/pilot-deepseek-20260920/spider_agent_lite__deepseek--deepseek-v4-flash \
  --resume-rules-archive outputs/evaluation/reference8_semantics_20261006/before/data/dataset/comparison_rules.json
```

Use the original model, budgets, run name and output directory. The explicit
historical rules archive must match the SHA256 in the original batch manifest;
it is compatibility evidence, never an alternative benchmark data source.
Canonical tasks are still loaded only from `data/dataset/`.

Continuation checks every saved task's full task/system prompt, shared assets,
runtime package/Python/platform identity, model and budgets, database snapshot,
reference file inventory and bytes, and comparison rule entry. Changes to the
per-task Spider loop, tools or shared runtime fail closed. Only coordinator/export
changes, the unused SWE-agent loop, global dataset containers (verified per saved
task), and references for untouched pending tasks can differ. A missing task with
partial artifacts/workspace is rejected; saved errors/corrupt records are rejected.
There is no score-based retry or automatic rerun of unsubmitted saved loops.

The original `batch_manifest.json` and all existing saved-task files remain
byte-identical. The first continuation reserves `resume_manifest.json`; subsequent
continuations append `resume_manifest.002.json`, `.003.json`, and so on. Previous
ledgers are never renamed, deleted or rewritten. Each segment records SHA256 links
to all earlier ledgers, compatibility proof, current frozen source hashes,
differences by generation segment, per-task comparison-rule fingerprints, final
new raw-result hashes and each task's actual generation origin. The result spans
multiple source segments, not one historical global dataset version. Workers run
only pending tasks; one final parent export covers
the complete expected universe and retains missing/error status explicitly.
The report creates evaluator `.traj` files when absent, but never overwrites a
reused task's existing normalized trajectory or `.traj` file. Scoring stays separate.
Reuse the same resume command after a stopped continuation: every saved result is
verified against the segment that generated it, not against the original global
reference version. Completed-but-unsubmitted loops are retained without retry.
The original rules archive is still required; older legacy segments whose rules
are not archived can be reused only when their recorded full rules SHA256 matches
the current file (or that archive). An unavailable historical contract fails closed.
Legacy `resume_manifest.json` remains readable without migration or rewriting.
Older broad source scopes may be reduced only for excluded files whose historical
bytes still match; generation-critical changes are not waived.

`--report-only` selects the latest segment, rejects a running/invalid continuation,
and rechecks current sources, historical ledger links and saved bytes. Gaps in the
ledger sequence, unexplained result ownership, changed settings/snapshot or prior
bytes prevent continuation. An active segment and exclusive creation of the next
ledger prevent duplicate coordinators. This resumes untouched missing tasks, not
an agent's interrupted inner loop: a partial workspace with no final result still
requires an explicit separate recovery decision. No automatic model run is started
merely by installing this support.

After an abrupt process exit that leaves the latest ledger marked `running`, add
`--recover-crashed` to the same resume command. Recovery first refuses any possible
active generation process, then appends a hash-bound `.recovery.json` receipt;
the abandoned ledger is unchanged. If unfinished tasks have residual directories,
explicitly choose `--restart-incomplete` (fresh original per-task budgets) or
`--defer-incomplete` (run untouched tasks only). Partial artifacts are moved, not
deleted, under `.recovery/<segment>/<task>/` and their hashes are preserved. Lost
inner-loop steps cannot be reconstructed and are recorded as unknown. If recovery
was recorded but startup later failed compatibility checks, reuse the normal resume
command without `--recover-crashed`; no second recovery receipt is written.

A narrowly verified GPT-only gateway addition can be compatible with a DeepSeek
continuation: removing the inactive GPT function/dispatch and restoring the routing
docstring must reconstruct the exact historical file SHA256. Any other change to
the DeepSeek gateway remains an error; the proof is recorded in source changes.

For the historical scalar-packing comparison change, a resume can explicitly use
`--preserve-historical-comparison`: replacing the new scalar block must reconstruct
the exact recorded historical comparator SHA256. Only that historical `_record`
function is installed in each task process; the public comparator file and other
running experiments remain untouched. The ledger records the historical execution
SHA256 as well as the current physical-source hashes. Other comparator changes fail
closed. Independent scoring still uses the installed current comparator unless
separately arranged; generation and evaluation versions must not be conflated.

Offline batch/compatibility checks use fake task processes and temporary outputs:

```bash
python -m unittest discover -s spider_agent_lite/tests -v
```

These checks make no model or database calls. The upstream-loop bridge test is
skipped when its optional SDK imports are unavailable; this is not a verified
real-provider or database run.
