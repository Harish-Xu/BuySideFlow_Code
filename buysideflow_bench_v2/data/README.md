`data/dataset/` 是唯一正式 benchmark，结构为 `question/*.jsonl` 与 `results/<instance_id>/`。

- Dataset card：`data/dataset/DATASET_CARD.md`
- 构建实例与评测复现：[REPRODUCIBILITY.md](dataset/REPRODUCIBILITY.md)，对应当前 `stock_002` 的数据依赖、时间规则、验收证据与结果比较实现。
- 派生 manifest：`data/dataset/manifest.generated.jsonl` 由 `python run_manifest.py --data data/dataset` 生成，不作为评测真源；少量人工修正只放在 `data/dataset/manifest_overrides.json`
- 发布前检查：`python run_dataset_sanity.py --data data/dataset`，用于发现极宽/异常 CSV、空结果说明缺失，以及 `.cache`、`__pycache__`、`.pyc` 等不应进入公开包的污染物
- 旧 `.md`/`.markdown` 任务文件只能通过命令行显式传入，不属于正式数据集，也不会被自动发现
