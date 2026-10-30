# Text2SQL Benchmark

论文版 Text2SQL benchmark 工程入口目录。

## 目录

- `run_text2sql.py`：主 benchmark 运行入口
- `run_text2sql.py --prompt-variant no-pit`：最小 `−PIT Guidance` 消融入口；题面、schema、工具、执行门禁、预算和评测器不变，只替换已注入的三份规则资产并移除系统提示中的显式 PIT 句
- `run_text2sql_ablation.py`：参考结果查看工具的独立消融入口，使用与正式 benchmark 相同的 PIT 背景和领域规则
- `run_eval.py`：独立评估入口
- `run_time_audit.py`：时间锚定审计入口
- `run_manifest.py`：派生 manifest 生成入口
- `run_dataset_sanity.py`：发布前输出契约与缓存污染物检查
- `sweagent/`：核心 agent、runtime、evaluator、prompt 与工具实现；基于 [SWE-agent](https://github.com/SWE-agent/SWE-agent) 1.1.0 修改，原 MIT 许可见 `sweagent/LICENSE`
- `spider_agent_lite/`：固定版本的官方 Spider-Agent-Lite 源码及 BuySideFlow 独立适配层；复用现有 dataset、assets 与 evaluator
- Spider安全续跑：`--all --resume-from MODEL_DIR --resume-rules-archive JSON`逐题核验已保存提示、预算/运行环境、参考和规则后，仅调度未触碰的缺失题；原清单与已保存文件不改写，另存来源分段与兼容证据。用法及拒绝条件见[适配层说明](spider_agent_lite/README.md#safe-continuation-after-source-drift)。
- `data/`：benchmark 数据与后续公开数据放置目录
- `trajectories/`：运行输出目录
- `data/dataset/DATASET_CARD.md`：BuySideFlow dataset card

## 默认数据路径

`data/dataset/` 是唯一正式 benchmark。运行配置存在时，独立评估会使用其中记录的数据源；没有运行配置时，固定使用 `data/dataset/`，不再自动搜索旧 Markdown 文件或历史目录。

`data/dataset/comparison_rules.json` 仅保存已核定的逐题表示规则，不是全部408题的新契约体系。规则绑定题面/契约指纹；字段别名不能删除期间或对象限定，单位换算必须显式，缺列、重复列、错值、漏行和漏组件仍失败。无明确行序要求时比较完整行多重集合。半偶/半入差异及显示并列的排序需要原值证据；缺少证据时不能自动放行。原值旁证同时绑定文件和参考程序指纹，修改参考后必须重新核验。

只有 `artifacts` 显式声明的文件才替代默认主答案；`fund_108` 为 `candidates.csv`、`industry_contributions.csv`、`return_summary.csv`，旧 `result.csv` 已撤下。`raw_artifacts` 仅作比较旁证，贯通加载、在线/离线评分及来源记录，不暴露为候选主输出。参考修改须另做必要的定向隔离重放。历史保存结果的本轮复核入口为 `python3 outputs/evaluation/semantic_comparison_20261001/rescore_saved_outputs.py`，该脚本禁止执行候选程序或调用裁判模型，且不回写正式成绩。

经用户定向授权，`fund_013` 在上述历史冻结版本之外新增一条经审核的比较契约：接受同义字段名、规模表头中明确的季末日期，以及同一主基金代码经数据库核验的简称。没有日期的字段不从参考补填，名称不作模糊匹配，错值、漏行和排序错误仍失败。十个配置的 `fund_013` 已按同一新冻结版本评测并同步正式评分与相关审计：六份是修正规则后重新生成，Codex Local、两个Spider及GPT −PIT四份保留原提交，仅补重评。未改变冻结比较规则，两个Spider的字段别名例外另列审计，不手工改判。本轮不将其他407题标为已重评。来源、旧记录及定向同步见 `outputs/model_rerun/fund013_rules6_20261008/remaining4/publication/`；此前六份记录保留在其上级目录。

## 克隆后准备

`data/dataset/manifest.generated.jsonl` 是派生文件，不纳入版本控制。克隆后先生成它；缺少该文件时，一致性门禁 `scripts/verify_dataset_consistency.py` 会失败。生成过程不连接数据库：

```bash
pip install -r requirements.txt
python run_manifest.py --data data/dataset
python scripts/verify_dataset_consistency.py
```

`data/dataset/manifest.generated.jsonl` 是从题面、参考产物和审计规则派生的论文元数据，不作为评测输入。只在 `data/dataset/manifest_overrides.json` 中手工补充无法稳定推导的 `level`、`as_of_date`、`empty_result_reason`、`future_label_allowed`、`future_label_reason`。

## 运行

文本及摘要的语义裁判由 `TEXT_JUDGE_MODEL` 配置，默认是 `gpt-5.5`；纯表格比较仍走确定性比较器。`--offline-judging` 或 `TEXT2SQL_OFFLINE_JUDGE=1` 会禁用模型裁判，不能把该模式下的 `judge_error` 当作答案错误。

图片裁判默认使用项目 `gpt` 预设对应的 `gpt-5.5`，由 `.env` 中的 `VISION_JUDGE_MODEL` 配置，接口和凭据沿用 `OPENAI_API_BASE` 与 `OPENAI_API_KEY`。六道绘图题使用 `sweagent/config/text2sql_visual_contracts.json` 中绑定题面指纹及原文证据的固定评分条款；其他含图片任务才通过 `VISION_CHECKLIST_MODEL`（默认 `gpt-5.5`）从题面提取要求。参考图提供数据依据，不额外规定轴向、配色、标题语言或子图布局。修改后对新启动的评测进程生效；历史成绩需另行重评，缓存按模型、全部图片和评分协议区分。

运行器收集本次执行保存的不同图片及仍开放的图，相同图像去重，同一路径保留最后版本。表格匹配与图片正确性分别输出为 `data_passed` 和 `image_passed`；表格比较失败不等于图片错误。必选语义项决定图片是否通过，非必选外观项不否决通过。裁判超时或无效返回记为 `judge_error`，题面与图片契约不一致记为 `visual_contract_error`，证据写入失败记为 `artifact_error`；这些结果不计入得分分母，未知分项不记作零分。图片、模型、原程序指纹、题面和裁判信息保存到 `trajectories/text2sql_image_artifacts/`，可用 `TEXT2SQL_IMAGE_ARTIFACT_DIR` 指定目录。图片裁判超时由 `VISION_JUDGE_TIMEOUT_SEC` 设置，默认180秒。

在本目录打开终端后直接运行：

```bash
export DB_SNAPSHOT_ID=jydb_YYYYMMDD_v1
# 若数据库并非物理只读快照，必须同时设置该版本的失效时刻：
export DB_SNAPSHOT_CUTOFF=YYYY-MM-DDTHH:MM:SS+08:00
python run_text2sql.py
python run_text2sql.py --model gpt --prompt-variant no-pit
python run_text2sql_ablation.py
python run_eval.py
python run_time_audit.py --data data/dataset
python run_manifest.py --data data/dataset
python run_dataset_sanity.py --data data/dataset
python run_reference_replay.py --data data/dataset --workers 2
```

正式论文实验报告要求 `DB_SNAPSHOT_ID` 非空。该标识只允许指向物理只读快照；若底层实例仍会更新，必须设置 `DB_SNAPSHOT_CUTOFF`，失效后程序拒绝继续沿用旧标识。报告会固定记录 schema/assets、reference artifacts、sidecar 文件和 judge cache 的聚合 SHA256。

当前最小 `−PIT Guidance` 消融固定使用 `gpt-5.5`（运行参数为 `--model gpt`），Full 与 `−PIT` 必须使用同一模型。它以当前规则为母版，仅删除披露截止、历史有效区间、当前状态字段禁用、观察日版本和未来信息泄漏等显式指导；交易日历、金融口径和计算规则保留。`selection_guidance.txt` 当前只进入实例元数据和来源记录，默认实例模板未渲染它，因此不作为本次处理变量。该消融不启用 `reveal_reference_result`，默认输出增加 `no_pit_guidance` 后缀以免覆盖正式运行。

单文件静态审计使用 `python sweagent/text2sql/time_audit_single.py audit --data data/dataset`，与上面的 `run_time_audit.py` 是不同入口。该脚本优先识别明确观察日，区分报告期和预测日期；题面与日期参数冲突、日期无效或参数未绑定时报告 `error`，不将其算作模型错误。文件夹入口读取参考程序，模型提交须通过 `audit_time_anchor_record` 单独传入。多观察日和滚动回测仍须逐决策时点核验，汇总锚点不能替代逐期可得性检查。

时间解析保持三条边界：普通日期用确定性规则规范化；“最后一个交易日”等表达不再转换成自然月末或年末；复杂时间规则可通过可选模型接口提取，但不自动成为已确认的审计依据。默认 CLI 不调用模型或数据库，也不会修改人工标签。

`resolve_time_anchor`、`audit_time_anchor_record` 以及 `run_time_audit_cases.audit_case` 的可选接口：

- `time_rule_extractor(request)`：输入仅为实际题面、参数及其指纹；返回带逐字引文的规则。可使用 `functools.partial(engine.extract_time_rules_with_model, model=...)` 接入现有模型配置；每次最多请求一次、30秒超时、不自动重试。日期明确时不调用。规则区分观察日、报告期、统计区间和事后标签，模型产出的角色解释始终保留 `model_time_rules_unreviewed`，不因 JSON 校验通过而放行。
- `calendar_lookup(rule)`：返回真实日历证据，不用工作日推算或模型猜日期。`engine.query_trading_calendar(rule, query, snapshot_id=...)` 提供限定日期和市场的 SELECT；`query(sql)` 接入现有只读连接并返回字典行。要求指定市场的逐日日历完整，缺行、空结果或市场未指定时保留待复核。多市场按交易日并集，与现有交易日工具一致；需要共同数据日的任务必须另用对应数据源，不能套用交易所日历。
- `event_lookup(rule)`：由任务专用的有界只读查询返回 `source`、`snapshot_id`、`complete` 和 `events: [{id, date}]`。该查询负责核定事件含义、实体、发布日期及事件后的交易日偏移；不得执行模型生成的 SQL。没有适用查询时不猜日期。

最小调用示例（只有执行这段代码才会启用外部服务；凭据沿用现有环境加载路径）：

```python
from functools import partial
import os
from run_time_audit_cases import load_engine
from sweagent.text2sql.db_connector import query_to_dataframe

engine = load_engine()
def calendar_lookup(rule):
    return engine.query_trading_calendar(
        rule,
        lambda sql: query_to_dataframe(sql, timeout=30, max_rows=3000).to_dict("records"),
        snapshot_id=os.environ["DB_SNAPSHOT_ID"],
    )

dates = engine.resolve_time_anchor(
    "在沪深A股中，以2025年4月和8月最后一个交易日为观察日。",
    time_rule_extractor=partial(engine.extract_time_rules_with_model,
                                model=os.environ["TEXT_JUDGE_MODEL"]),
    calendar_lookup=calendar_lookup,
)
```