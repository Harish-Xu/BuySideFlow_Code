将 benchmark markdown、额外数据文件和后续公开数据集统一放在这个目录下。

- 默认 benchmark：`data/dataset/`（`question/*.jsonl` + `results/<instance_id>/`）
- Dataset card：`data/dataset/DATASET_CARD.md`
- 派生 manifest：`data/dataset/manifest.generated.jsonl` 由 `python run_manifest.py --data data/dataset` 生成，不作为评测真源；少量人工修正只放在 `data/dataset/manifest_overrides.json`
- 发布前检查：`python run_dataset_sanity.py --data data/dataset`，用于发现极宽/异常 CSV、空结果说明缺失，以及 `.cache`、`__pycache__`、`.pyc` 等不应进入公开包的污染物
- 旧 markdown benchmark 仍可放在：`data/benchmarks/`
- 如需兼容历史路径，脚本仍会回退到旧的 `text2sql-schema-filter-main_v8/results/`
