# BuySideFlow Dataset Card

## 概览

BuySideFlow 是一个面向中文买方投研场景的 Text-to-SQL / SQL-Python 评测数据集。当前版本包含 408 道任务，覆盖股票、基金、债券与宏观四类研究问题，目标是评估模型或 Agent 在私有金融数据库上完成时间锚定查询、表间关联、财务口径过滤、统计分析与图表/摘要生成的能力。



同日后续修订：`stock_038` 已限定年度报告/年度更正来源，综合可判定数现为157/222/411；`macro_024` 同方向去重改按公布日间隔两个自然月，仍为17行。两题已重新生成参考答案并通过隔离重放，72项回归通过；参考以[最新修订报告](../../outputs/reference_semantics_20260914/report.md)为准。随后四题五模型已完成20次新生成及独立复评，共5/20通过；正式目录不保留这四题旧版本，见[完成报告](../../outputs/model_rerun/updated_stock_macro_20260914/completion_report.md)。

评测真源是 `question/*.jsonl` 与 `results/<instance_id>/` 下的参考产物，执行与比较逻辑由 evaluator 决定。`manifest.generated.jsonl` 只用于论文统计、审计和分层报告，可由脚本重建，不作为评测输入。

## 数据组成

文件组成与任务属性由 `python run_manifest.py --data data/dataset` 生成；参考答案生成管线另按 `results/_classify_summary.md` 的 2026-09-05 逐题核查版本统计：

| 维度 | 分布 |
| --- | --- |
| 任务数 | 408 |
| 领域 | stock 166, fund 125, bond 93, macro 24 |
| 参考文件模式（按文件存在性） | sql+python 332, python 76 |
| 参考答案生成管线（按实际业务处理） | SQL-native 254, SQL+Python 154 |
| 评测类型 | csv 402, mixed 1, vision 5 |
| 任务难度 | 408 题待人工重标（unrated）；旧自动层级已停用 |
| 输出结构契约 | strict 379, loose 29 |

两种分类不可混用：存在 `refer.py` 不等于业务计算依赖 Python。SQL+Python 的 154 题含 SQL 编排/语义处理 5 题、Python 实质计算或产出处理 149 题；该标签描述参考实现，不证明任务必须使用 Python。

难度采用 [金融工作流三档规则](DIFFICULTY_RUBRIC.md) 的试标稿，借鉴 BIRD 与 DA-Code，区分 Easy、Medium、Hard，但不从文件存在性、关键词或 SQL 长度自动分档。先进行 40 道任务的双人独立试标，满足预设一致性门槛并冻结规则后，再重标全部 408 题。在此之前不发布新难度分布或难度分层成绩。旧 `L1–L4` 和旧 `easy/medium/hard` 启发式结果只保留作历史记录，不映射为新标签。

## 当前参考验证状态

2026-08-24 对当前 408 题版本执行了隔离式全量参考重放：408/408 个参考程序执行成功并通过现有 evaluator，其中 407 题为字节级 `exact_match`，`stock_159` 为行顺序不同但表头、20 行数据与全部字段值完全相同的 `evaluator_equivalent`，0 题失败。重放前后 1154 个参考文件的聚合 SHA256 均为 `ef88ed40e0c8b068a22d583f2953aca2b9f17e77d0d21db8d0d9046259a0c500`。

相对仓库历史题面发生变化且仍在当前正式版中的 51 题已逐题完成题面—程序语义审查：42 题原样通过，9 题经最小修订后通过，0 题遗留未决问题。修订包括 7 处题面口径明确和 2 处参考程序时点修复，锁定结果文件无需修改；9 题定向重放均为 `exact_match`。

- 当前全量重放报告：`outputs/reference_replay/current_408_20260824/full_408/reference_replay_report.md`
- 51 题语义审查报告：`outputs/reference_replay/current_408_20260816/question_program_alignment_report.md`

当前环境使用 `DB_SNAPSHOT_ID=jydb_20260430_v1`。该快照指共享 MySQL 实例上的 `jydb` schema；数据库维护方确认该 schema 自 2026-04-30 后未发生写入。实例级 GTID 仍可能因其他 schema 或系统运维事务增长，因此这里锁定的是 schema 级逻辑快照，不宣称整个 MySQL 实例物理只读。

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

参考答案以可执行 SQL/Python 为主，输出以 CSV 表为主；少量任务要求图像或文本摘要。每道题通过 `strict_output_schema` 显式声明结构口径：题面明确列举最终字段且表格形态唯一的任务采用严格结构比较，其 `result.csv` 给出具体列名、列顺序和列数；默认要求列数、列顺序及规范化后的字段名称一致。题面只规定计算语义、筛选条件或指标，或者允许多种等价表格形态的任务采用 loose value-equivalent 比较。参考 CSV 存在表头本身不构成严格契约。

严格结构任务可显式设置 `column_order_matters=false`，表示列顺序不参与评分。此时双方规范化列名必须唯一、列集合及列数必须一致，比较器按列名对齐后检查数据，不根据数值匹配列。该选项省略或为 `true` 时保持原有列顺序检查，不改变行顺序、精度或单位规则；对应说明通过 `output_contract` 提供给模型。当前仅 `fund_005` 启用。


## 时间锚定规则

数据集的核心约束是 point-in-time evaluation：模型只能使用题目给定时间点之前可获得的信息。

时间锚点按以下优先级确定：

1. `inputs.as_of_date`：若题目显式提供结构化日期，作为最高优先级。
2. 题面完整日期：从中文 instruction 中抽取 `YYYY-MM-DD`。
3. 快照默认日期：仅用于明确需要按数据库快照解释的任务，并由运行参数显式提供。
4. 年份、月份、季度和事件窗口：作为封闭统计或回测区间时可直接构成时间契约；只有题目语义要求单一观察日但完全没有时间范围时，才需要在 `manifest_overrides.json` 中补充 `as_of_date`。

当前 time audit 覆盖全部 408 题：408 题为 `pass`，0 题为 `warn`，0 题为 `fail`。年份或封闭区间用于限定统计期和回测窗口，不因缺少单一 `YYYY-MM-DD` 而自动记为 warning。

| anchor_source | 数量 |
| --- | ---: |
| inputs | 35 |
| instruction | 298 |
| instruction_derived | 28 |
| instruction_year | 47 |
| missing | 0 |

年份如 `2024` 不会被机械改写为 `2024-12-31`。当年份本身已经定义封闭统计期或事件窗口时，`as_of_date` 保持为空，但 `needs_as_of_date_override=false`；只有真正缺少时间范围且语义上需要单一观察日的任务才标记为 `true`。

## 泄漏防护

评测代码在执行模型生成的 SQL/Python 前做静态检查，禁止运行时日期函数，以避免模型绕开题面时间锚点：

- SQL 禁止：`CURDATE()`、`CURRENT_DATE`、`NOW()`、`SYSDATE()`、`CURRENT_TIMESTAMP`。
- Python 禁止：`date.today()`、`datetime.now()`、`datetime.today()`、`pd.Timestamp.today()`、`pd.Timestamp.now()`。
- Python 内部调用 `query_to_dataframe(sql)` 时也会再次 lint SQL 字符串，防止把运行时日期函数藏进 Python 动态 SQL。

参考答案还通过 `run_time_audit.py` 做时间审计：

- 财报类表应包含 `INFOPUBLDATE <= as_of_date`，避免使用尚未披露的财务数据。
- 状态类表应包含 point-in-time 状态过滤，例如 `EFFECTIVEDATE <= as_of_date`、`CANCELDATE > as_of_date`、`EXPIREDATE`、`LISTEDDATE` 等。
- 预测题若需要未来 realized return 作为离线标签，必须显式标记 `future_label_allowed`，并在 override 中给出说明。

当前审计结果显示运行时日期函数违规为 0，财报披露 guard 缺失为 0，状态 guard 缺失为 0。

## 历史 281-pass 版本的 PIT 人工审计

以下结论属于冻结的历史 281-pass 版本，不是当前磁盘上的 279-pass 重评版本。该历史版本的五个受控运行共有 281 个通过常规结果评测的 `(model, task)` 提交。PIT v2 审计覆盖其中 93 个提交，包括全部 57 个正式规则标记的通过提交和 36 个确定性分层抽取的无标记对照；68 个标签继承自证据未变化的冻结 v1 案例，25 个变化或新增案例由两位复核者独立盲审，实质分歧为 0。

最终审计确认 57 个 workflow 不满足 PIT，其中 44 个同时被判定为结果产物正确，覆盖 24 道题和五个模型。因此，在固定的 281 个通过提交总体中，至少 44 个（15.7%）属于“结果匹配正确但 workflow 的时间口径错误”。该比例是已确认案例占总体的保守下界；36 个对照是分层样本，其未加权错误比例不能解释为整个 benchmark 的假阴性率。

该历史审计的案例包、最终标签、汇总与 provenance 不随本仓库发布。

当前 279-pass 版本另有 `outputs/manual_audit_current279_400fail_279pass_20260904/` 审计包：400 个自动失败样本与全部 279 个自动通过提交，共 679 个案例供双人独立编码。不能将历史 93 例的标签或比例直接当作这 679 例的新审计结果。

## 评测指标

主指标是总分比例：

```text
score = sum(item_score) / sum(item_max_score)
full_score = 完全通过的题目数 / 题目总数
```

指标分为主次两类：

- `primary_tabular`：严格 CSV 结构化评测。适用于 `evaluation_kind=csv` 且 `strict_output_schema=true` 的任务。
- `primary_tabular_loose`：值等价 CSV 评测。适用于题面没有唯一输出结构的 CSV 任务；报告时与严格结构任务分层展示，不混写成严格准确率。
- `secondary_semantic`：文本、图像或混合产物评测。适用于 `mixed` 与 `vision` 任务，作为辅助指标报告。

严格结构化 CSV 评测要求：

- 输出列名与参考 CSV 契约一致；空白、全/半角括号差异不计错，参考已声明单位时表头可省略或按同量纲改写单位（数值随之换算）；未知、歧义或重复列名不予接受。
- 行列结构一致；显式声明列顺序不限时按唯一列名对齐，行顺序仍按原有题意规则比较。
- 数值按题面精度或默认精度比较，并处理常见金融单位尺度差异；参考未声明单位时，单元格 `x%` 或表头 `(%)` 视为展示形式，按 x 比较。
- 日期、代码、空值和文本字段做规范化后比较；零点时间戳视同日期。
- 以上表现层归一自 2026-10-08 生效；`TEXT2SQL_PRESENTATION_NORMALIZATION=0` 恢复此前的字面比较，下表等既有成绩均按字面比较计算。

混合与图像产物采用分项评分。表格、图像、文本按产物类型加权；其中表格仍优先使用结构化比较，图像和文本使用 judge，并通过 judge cache 固定可复现性。

报告同时输出：

- 按 domain、metric_type 等已验证属性的分层得分；难度分层暂停，直至人工规则验证与全量标注完成。
- 错误类型分布：syntax、timeout、schema_mismatch、format_mismatch、logic_mismatch、ai_mismatch 等。
- Agent 过程指标，例如 schema exploration 次数与 Navigation Trap Rate。

截至 2026-09-14，五个受控运行的当前正式结果如下，以 `trajectories/bod` 中的 `tool_use_*_current408.csv` 及 `outputs/model_rerun/updated_stock_macro_20260914/publish_summaries.json` 为准。最后一次更新对 `stock_038`、`macro_024`、`stock_119`、`stock_120` 重新生成五模型答案并串行独立复评，20条中5条通过。每模型其余404条结果保留；这里是历史408条混合汇总，不是当时407题的全量重跑，也不据此声称模型间存在统计显著排名。

| 模型 | full match | score | full-match rate |
| --- | ---: | ---: | ---: |
| Claude Opus 4.7 | 66/408 | 66.400/408 | 16.18% |
| DeepSeek V4 Flash | 74/408 | 74.780/408 | 18.14% |
| GLM-5.1 | 60/408 | 61.126/408 | 14.71% |
| GPT-5.5 | 86/408 | 87.330/408 | 21.08% |
| Qwen3.6 Plus | 56/408 | 56.400/408 | 13.73% |

本次定向新生成报告见 `outputs/model_rerun/updated_stock_macro_20260914/completion_report.md`，发布级校验见同目录的 `final_verification.json`。此前 `outputs/evaluation/reference_rescore_4x5_20260914/` 的汇总保留为上一更新时点的历史记录。逐领域和逐分析管线源表 `figures/source_data/performance_by_domain.csv` 与 `figures/source_data/performance_by_pipeline.csv`，以及 `outputs/pipeline_classification_audit_20260905/current279_regrouped_stats.json` 仍是 279-pass 历史版本的分层产物，未按当前逐题结果重算，不得与上表的当前总分拼接。现有渲染图亦尚未重绘，版本边界见 `figures/README.md`。

## Provenance 与可复现性

正式实验必须设置 `DB_SNAPSHOT_ID`，并在报告中记录：

- 数据库快照标识。
- 快照适用范围和冻结依据；共享实例场景须明确冻结到 schema，并记录维护方确认的最后写入日期。
- schema/assets 聚合 SHA256。
- reference SQL/Python/CSV/图像/文本及 sidecar 文件聚合 SHA256。
- judge cache 聚合 SHA256。
- 每题 reference hash 与 sidecar hash。

## 不可公开 DB 的处理方式

JYDB 等底层金融数据库受商业授权、隐私和数据再分发限制，不能随数据集公开发布。BuySideFlow 应按三层形态发布和复核：

- 内部完整评测包：包含题面、参考 SQL/Python、参考输出、sidecar、schema assets、DB 连接配置和 evaluator，仅在持有授权数据库快照的环境中使用。
- 公开审计包：包含题面、输入字段、输出 schema、派生 manifest、time audit、评测代码、reference/provenance hash；若授权不允许公开完整 schema 或结果行，则只发布脱敏 schema 摘要、列级契约和哈希。
- 官方封闭评测：外部提交模型生成代码或轨迹，由官方在固定 `DB_SNAPSHOT_ID` 的受控环境中执行，并发布汇总分数、错误类型、分层报告和 provenance hash。

不公开内容包括数据库 dump、连接凭据、受授权限制的原始表数据、可能构成数据再分发的完整查询结果，以及未经许可的完整商业 schema。可公开内容以不泄露底层数据库授权数据为边界。

这种设计不能让外部用户完全本地复刻数据库查询结果，但可以保证题目、评测程序、参考产物版本和时间锚定审计是可核验的。论文中应明确将其定位为 private-database benchmark，而不是可完全离线重建的 open-data benchmark。

## 已知限制

- 年份和事件窗口可以构成充分的时间契约，但不等价于单一 `as_of_date`；分层分析时应同时保留 `anchor_source`，避免混淆统计区间与截面观察日。
- time audit 是静态规则审计，不能替代逐题业务审阅。
- 图像和文本评测依赖 judge；必须固定 judge cache hash，并把该类任务作为辅助指标报告。
- 68题有逐题审核的比较规则（字段别名、单位、精度等），其中部分别名与模型历史输出表头相同，属事后补充；规则自2026-10-08冻结，其余题按参考表头比较。题面写明输出列名前，列名表述差异对不同题、不同模型的影响不完全一致。
- 数据库不可公开会限制第三方完全复现实验，只能通过受控评测环境和 provenance hash 提供可审计性。
