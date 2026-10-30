# 图表与统计版本

## 当前论文图件

本目录只保留 2026-10-13 编译的投稿论文实际引用的图件。下列文件与论文源码中对应文件逐字节一致：

| 本目录文件 | 论文源码中的引用 |
|---|---|
| `f1_cropped.pdf` | `figures/buysideflow_figure1.pdf` |
| `f2_cropped.pdf` | `figures/buysideflow_figure2.pdf` |
| `buyside_f3.pdf` | `figures/buysideflow_figure3.pdf` |

论文另外两张图 `buysideflow_error_analysis.pdf`、`buysideflow_pit_guidance_joint.pdf` 在论文构建目录生成，不在本目录。上述三张图的可编辑源文件也不在本目录。

Figure 1 示例对应 `stock_002`：截至 2023-10-31，选取机械设备公司，要求 Q3 收入同比增速超过 20% 且比 Q2 提高超过 10 个百分点。人工 PIT 审计栏中 B（Industry）、C（Disclosure）两项标注为 Illustrative，为示意。

2026-10-26 起，历次设计稿、裁切前版本、中英文 evaluation protocol、domain accuracy、benchmark composition、controlled run performance、intro teaser、task construction 等图已移出本目录。

## 历史统计图与源表

2026-09-05 已同步以下六份 `source_data/` 源表：`benchmark_composition.csv`、`controlled_run_performance.csv`、`performance_by_pipeline.csv`、`performance_by_domain.csv`、`failure_categories.csv`、`pit_diagnostic_by_run.csv`。

六份源表采用冻结的 2026-09-05 口径：408 题、参考管线 254 SQL-native / 154 SQL+Python、五模型 2,040 个提交中的 279 pass / 1,761 fail。该版本只离线重分组，不重新评测。`failure_categories.csv` 是评测器错误类别，PIT 源表是规则诊断标记；二者都不是新人工审计的 A–G 根因标签。

2026-09-07 完成的 `macro_024 × 5模型` 定向重跑未并入这些源表或现有图片。其“冻结 407 题、替换 1 题”的拼接结果为 280 pass / 1,760 fail，但运行上下文除题面外还包含 schema 与选择指引版本变化，不能作为新的 408 题全量受控结果。版本说明与逐模型证据见 [`outputs/evaluation/macro024_targeted_revision_20260907/`](../outputs/evaluation/macro024_targeted_revision_20260907/README.md)。

旧渲染图 `benchmark_composition{,_iclr}.*`、`controlled_run_performance{,_iclr}.*` 呈现旧的 249/159 或 281-pass 数字，已移出本目录。

`secondary_logic_taxonomy.csv` 和 `construction_pipeline.csv` 保留历史材料；后者的“93 accepted submissions independently audited”指冻结的历史 PIT v2 审计，不代表当前 679 案例已完成审计。

本次来源与校验：`outputs/pipeline_classification_audit_20260905/revision_applied.json`。原六份源表备份在同目录的 `pre_revision/`。

随后进行的难度修订已从当前 composition 源表和生成器中移除旧 Difficulty 分面；408 题的新难度待双人试标验证及全量重标，暂不展示难度分布。该步版本记录见 `outputs/difficulty_revision_20260905/`；其中包含旧 composition 的 `legacy/` 备份已按维护者要求从项目中移除。旧图中的 L1–L4 同样不可作为新难度结果。
