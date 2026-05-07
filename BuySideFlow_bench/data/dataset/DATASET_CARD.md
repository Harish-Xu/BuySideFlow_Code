# BuySideFlow Dataset Card

## 概览

BuySideFlow 是一个面向中文买方投研场景的 Text-to-SQL / SQL-Python 评测数据集。当前版本包含 404 道任务，覆盖股票、基金、债券与宏观四类研究问题，目标是评估模型或 Agent 在私有金融数据库上完成时间锚定查询、表间关联、财务口径过滤、统计分析与图表/摘要生成的能力。

评测真源是 `question/*.jsonl` 与 `results/<instance_id>/` 下的参考产物，执行与比较逻辑由 evaluator 决定。`manifest.generated.jsonl` 只用于论文统计、审计和分层报告，可由脚本重建，不作为评测输入。

## 数据组成

当前数据分布由 `python run_manifest.py --data data/dataset` 自动生成：

| 维度 | 分布 |
| --- | --- |
| 任务数 | 404 |
| 领域 | stock 166, fund 122, bond 94, macro 22 |
| 模式 | sql+python 312, python 92 |
| 评测类型 | csv 392, mixed 5, vision 7 |
| 自动难度层级 | L1 19, L2 261, L3 112, L4 12 |
| 严格输出契约 | 404 / 404 |

每道任务至少包含：

- `question/*.jsonl` 中的 `instance_id`、中文 `instruction`，以及可选 `inputs`。
- `results/<instance_id>/refer.sql` 或 `refer.py` 作为参考实现。
- `results/<instance_id>/result.csv`、`picture.png` 或 `abstract.txt` 作为参考输出契约。
- 可选 sidecar 文件，例如 `annual_candidates_2024.json`，作为参考代码运行所需的局部候选集或中间数据。

## 任务构造

任务来自中文买方投研中的常见工作流，而不是孤立的 SQL 模板。构造时优先保留真实研究问题的业务形态：

- 股票：财报披露、估值、业绩增长、行业/指数成分、交易与收益率窗口。
- 基金：净值、规模、持仓、业绩、管理人、产品筛选与排名。
- 债券：发行、评级、到期、收益率、信用主体、候选券筛选。
- 宏观：统计指标、时间序列比较、同比/环比、结构占比。

参考答案以可执行 SQL/Python 为主，输出以 CSV 表为主；少量任务要求图像或文本摘要。CSV 任务以 `result.csv` 的列名作为可执行输出契约，评测时要求生成结果与参考结果在结构和数值上对齐。

## 时间锚定规则

数据集的核心约束是 point-in-time evaluation：模型只能使用题目给定时间点之前可获得的信息。

时间锚点按以下优先级确定：

1. `inputs.as_of_date`：若题目显式提供结构化日期，作为最高优先级。
2. 题面完整日期：从中文 instruction 中抽取 `YYYY-MM-DD`。
3. 快照默认日期：仅用于明确需要按数据库快照解释的任务，并由运行参数显式提供。
4. 缺失或仅年份锚点：不能自动视为完整 `as_of_date`，需要在 `manifest_overrides.json` 中人工补充或说明。

当前 time audit 统计：

| anchor_source | 数量 |
| --- | ---: |
| inputs | 40 |
| instruction | 290 |
| instruction_derived | 36 |
| instruction_year | 38 |
| missing | 0 |

年份如 `2024` 只表示题面存在年份锚点，不等价于 `2024-12-31`。这类任务在 `manifest.generated.jsonl` 中标记 `needs_as_of_date_override=true` 用于人工复核，表示未自动推导出完整单日 `as_of_date`，不代表必须补成单日锚点。

## 泄漏防护

评测代码在执行模型生成的 SQL/Python 前做静态检查，禁止运行时日期函数，以避免模型绕开题面时间锚点：

- SQL 禁止：`CURDATE()`、`CURRENT_DATE`、`NOW()`、`SYSDATE()`、`CURRENT_TIMESTAMP`。
- Python 禁止：`date.today()`、`datetime.now()`、`datetime.today()`、`pd.Timestamp.today()`、`pd.Timestamp.now()`。
- Python 内部调用 `query_to_dataframe(sql)` 时也会再次 lint SQL 字符串，防止把运行时日期函数藏进 Python 动态 SQL。

参考答案还通过 `run_time_audit.py` 做时间审计：

- 财报类表应包含 `INFOPUBLDATE <= as_of_date`，避免使用尚未披露的财务数据。
- 状态类表应包含 point-in-time 状态过滤，例如 `EFFECTIVEDATE <= as_of_date`、`CANCELDATE > as_of_date`、`EXPIREDATE`、`LISTEDDATE` 等。
- 预测题若需要未来 realized return 作为离线标签，必须显式标记 `future_label_allowed`，并在 override 中给出说明。

当前审计结果显示运行时日期函数违规为 0，财报披露 guard 缺失为 0，状态有效区间 guard 缺失为 0。这里的状态有效区间 guard 指 `EFFECTIVEDATE`、`CANCELDATE`、`EXPIREDATE`、`LISTEDDATE`、`IMPLEMENTDATE`、`REMOVEDATE` 等字段相对于观察日的 point-in-time 过滤。

`run_time_audit.py` 还单独报告 `cs_riskalert_publish_guard_missing=62`。该项是针对 `cs_riskalert` 风险警示记录公告时间或录入更新时间的更强审计项，例如 `IMPLANNOUCEDATE`、`REMOVEINFOPUBLDATE`、`INSERTTIME`、`UPDATETIME` 是否被限制在观察日前。它目前作为 advisory/manual-review 指标报告，不等同于状态有效区间 guard 缺失；许多 ST/*ST 剔除任务已经通过 `IMPLEMENTDATE` / `REMOVEDATE` 定义了观察日状态。

## 评测指标

主指标是总分比例：

```text
score = sum(item_score) / sum(item_max_score)
full_score = 完全通过的题目数 / 题目总数
```

指标分为主次两类：

- `primary_tabular`：严格 CSV 结构化评测。适用于 `evaluation_kind=csv` 且存在 `result.csv` 输出契约的任务，是论文主指标。
- `secondary_semantic`：文本、图像或混合产物评测。适用于 `mixed` 与 `vision` 任务，作为辅助指标报告。

结构化 CSV 评测要求：

- 输出列名与参考 CSV 契约一致。
- 行列结构一致；必要时按比较逻辑归一化行顺序。
- 数值按题面精度或默认精度比较，并处理常见金融单位尺度差异。
- 日期、代码、空值和文本字段做规范化后比较。

混合与图像产物采用分项评分。表格、图像、文本按产物类型加权；其中表格仍优先使用结构化比较，图像和文本使用 judge，并通过 judge cache 固定可复现性。

报告同时输出：

- 按 domain、difficulty/level、metric_type 的分层得分。
- 错误类型分布：syntax、timeout、schema_mismatch、format_mismatch、logic_mismatch、ai_mismatch 等。
- Agent 过程指标，例如 schema exploration 次数与 Navigation Trap Rate。

## Provenance 与可复现性

正式实验必须设置 `DB_SNAPSHOT_ID`，并在报告中记录：

- 数据库快照标识。
- schema/assets 聚合 SHA256。
- reference SQL/Python/CSV/图像/文本及 sidecar 文件聚合 SHA256。
- judge cache 聚合 SHA256。
- 每题 reference hash 与 sidecar hash。

这使论文实验能够固定在同一数据库快照、同一参考答案、同一 judge cache 下复现。`manifest.generated.jsonl` 中的 hash 字段服务于审计和分层分析；评测本身仍以 evaluator 执行结果为准。

## 不可公开 DB 的处理方式

JYDB 等底层金融数据库受商业授权、隐私和数据再分发限制，不能随数据集公开发布。BuySideFlow 应按三层形态发布和复核：

- 内部完整评测包：包含题面、参考 SQL/Python、参考输出、sidecar、schema assets、DB 连接配置和 evaluator，仅在持有授权数据库快照的环境中使用。
- 公开审计包：包含题面、输入字段、输出 schema、派生 manifest、time audit、评测代码、reference/provenance hash；若授权不允许公开完整 schema 或结果行，则只发布脱敏 schema 摘要、列级契约和哈希。
- 官方封闭评测：外部提交模型生成代码或轨迹，由官方在固定 `DB_SNAPSHOT_ID` 的受控环境中执行，并发布汇总分数、错误类型、分层报告和 provenance hash。

不公开内容包括数据库 dump、连接凭据、受授权限制的原始表数据、可能构成数据再分发的完整查询结果，以及未经许可的完整商业 schema。可公开内容以不泄露底层数据库授权数据为边界。

这种设计不能让外部用户完全本地复刻数据库查询结果，但可以保证题目、评测程序、参考产物版本和时间锚定审计是可核验的。论文中应明确将其定位为 private-database benchmark，而不是可完全离线重建的 open-data benchmark。

## 已知限制

- 部分题目只有年份或缺失显式 `as_of_date`，需要通过 `manifest_overrides.json` 做论文版补充。
- time audit 是静态规则审计，不能替代逐题业务审阅。
- 图像和文本评测依赖 judge；必须固定 judge cache hash，并把该类任务作为辅助指标报告。
- 数据库不可公开会限制第三方完全复现实验，只能通过受控评测环境和 provenance hash 提供可审计性。
