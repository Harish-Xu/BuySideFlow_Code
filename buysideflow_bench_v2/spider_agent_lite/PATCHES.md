# Local changes

No file under `upstream/` has been modified. BuySideFlow-specific behavior is
kept under `adapter/` and `config/` so the Spider-Agent-Lite decision loop can
be compared with its official implementation.

DeepSeek calls are routed outside the upstream source through the current
benchmark LiteLLM registry. This is required because the pinned upstream code
hard-codes the public DeepSeek endpoint and does not understand the project's
`deepseek/deepseek-v4-flash` model identifier. `PromptAgent.run` and
`PromptAgent.predict` remain unchanged.

The adapter defaults for DeepSeek request sampling, output ceiling, step
ceiling, and retry budget match the formal SWE-agent configuration. These are
runtime comparison controls; the pinned upstream decision-loop source remains
unchanged.

Full-benchmark concurrency is implemented only in the BuySideFlow launcher.
`--all --num_workers N` uses spawned task processes with one logical run name,
per-task working/temporary directories and a single coordinator-owned export.
`run_task(export_reports=False)` disables only the shared export, while its
default single-task behavior remains unchanged. Coverage-aware reporting keeps
missing predictions and worker failures visible without inventing submissions,
trajectories, tool counts or scores. Batch input/source provenance is checked
before dispatch and export, and the generated independent evaluation command
rechecks it before using the unchanged project evaluator.
