# 参考答案生成管线分类汇总

版本：2026-09-05。依据题面与当前参考程序逐题核查修订。

本文件按“参考程序中的金融业务逻辑与工作流由谁完成”分类，而不是按 Python 文件模板分类。标签描述当前参考答案生成管线，不表示任务在理论上必须使用 Python；SQL+Python 参考实现可能存在等价的纯 SQL 实现。

## 判定口径

- `sql_only`（SQL-native）：金融筛选、聚合和指标计算均由 SQL 完成。Python 仅可负责读取/执行 SQL、静态参数传入、列投影或重命名、无业务影响的类型与显示格式转换、日期格式化、结果保存及只读校验。`fillna` 等空值处理只有在不改变业务数值、样本集合或缺失语义的格式整理中才属于此类。
- `sql_orchestration_or_semantic`：Python 不承担实质量化计算，但实际负责参考管线的多阶段 SQL 编排、决定业务时间窗口等语义准备，或对 SQL 返回文本执行语义提取。固定临时表的创建与分步查询、逐月执行完整 SQL 并拼接结果均计入编排；单条 SQL 的执行包装不计入。
- `sql_python_compute`：Python 实际承担业务筛选、分组、合并、排名、因子、收益、回撤、回测、回归、插值、聚类、业务摘要或绘图等实质处理。仅导入 pandas、numpy 或绘图库不构成此类证据。

三类互斥并覆盖当前全部 408 道题。

| 类别 | Bond | Fund | Macro | Stock | 合计 |
|---|---:|---:|---:|---:|---:|
| `sql_only` | 34 | 76 | 22 | 122 | 254 |
| `sql_orchestration_or_semantic` | 2 | 1 | 0 | 2 | 5 |
| `sql_python_compute` | 57 | 48 | 2 | 42 | 149 |
| 合计 | 93 | 125 | 24 | 166 | 408 |

## sql_only（254）

### Bond（34）

bond_002, bond_003, bond_011, bond_012, bond_013, bond_014, bond_015, bond_018, bond_019,
bond_020, bond_021, bond_023, bond_025, bond_026, bond_027, bond_028, bond_029, bond_030,
bond_031, bond_032, bond_036, bond_040, bond_042, bond_043, bond_046, bond_066, bond_067,
bond_069, bond_072, bond_073, bond_074, bond_075, bond_088, bond_089

### Fund（76）

fund_001, fund_010, fund_012, fund_013, fund_015, fund_019, fund_021, fund_024, fund_028,
fund_029, fund_031, fund_032, fund_033, fund_034, fund_035, fund_036, fund_037, fund_038,
fund_039, fund_040, fund_041, fund_042, fund_043, fund_044, fund_045, fund_046, fund_051, fund_053,
fund_054, fund_055, fund_056, fund_057, fund_058, fund_061, fund_062, fund_063, fund_064, fund_065,
fund_066, fund_067, fund_068, fund_070, fund_072, fund_073, fund_074, fund_075, fund_076, fund_077,
fund_078, fund_079, fund_080, fund_081, fund_083, fund_084, fund_085, fund_086, fund_087,
fund_088, fund_089, fund_090, fund_092, fund_100, fund_102, fund_103, fund_110, fund_111, fund_112,
fund_113, fund_115, fund_116, fund_117, fund_119, fund_120, fund_121, fund_122, fund_123

### Macro（22）

macro_001, macro_002, macro_003, macro_004, macro_005, macro_006, macro_007, macro_008,
macro_009, macro_010, macro_011, macro_012, macro_013, macro_014, macro_015, macro_016,
macro_017, macro_018, macro_019, macro_020, macro_021, macro_022

### Stock（122）

stock_002, stock_004, stock_006, stock_007, stock_008, stock_011, stock_012,
stock_014, stock_016, stock_017, stock_018, stock_019, stock_021, stock_022, stock_023,
stock_024, stock_025, stock_026, stock_027, stock_028, stock_029, stock_030, stock_031,
stock_034, stock_036, stock_037, stock_038, stock_040, stock_041, stock_043, stock_044,
stock_045, stock_046, stock_047, stock_048, stock_049, stock_050, stock_051, stock_052,
stock_053, stock_054, stock_055, stock_056, stock_057, stock_058, stock_059, stock_061,
stock_062, stock_063, stock_064, stock_065, stock_066, stock_067, stock_068, stock_069,
stock_070, stock_071, stock_072, stock_073, stock_075, stock_076, stock_077, stock_079,
stock_080, stock_082, stock_083, stock_085, stock_086, stock_087, stock_088, stock_089, stock_090,
stock_091, stock_093, stock_094, stock_095, stock_096, stock_098, stock_099, stock_102,
stock_104, stock_105, stock_108, stock_109, stock_110, stock_111, stock_112,
stock_113, stock_114, stock_115, stock_116, stock_117, stock_118, stock_119, stock_120,
stock_121, stock_122, stock_123, stock_124, stock_125, stock_127, stock_128, stock_129,
stock_130, stock_131, stock_132, stock_133, stock_134, stock_135, stock_136, stock_137,
stock_138, stock_139, stock_140, stock_142, stock_143, stock_144, stock_145, stock_146, stock_147,
stock_150, stock_161

## sql_orchestration_or_semantic（5）

### SQL 编排（3）

bond_065, bond_076, stock_009

说明：两道债券题由 Python 执行固定临时表及多语句查询；股票题由 Python 按月运行完整业务 SQL 并拼接结果，均保留为 SQL+Python 编排管线。

### 会计窗口语义准备（1）

stock_158

说明：Python 以 4 月 30 日为分界决定采用的会计年度，这是业务时间窗口决策，不是单纯日期格式转换或多 SQL 编排。

### 语义文本提取（1）

fund_030

## sql_python_compute（149）

### Bond（57）

bond_001, bond_004, bond_005, bond_006, bond_007, bond_008, bond_009, bond_010, bond_016,
bond_017, bond_022, bond_024, bond_033, bond_034, bond_035, bond_037, bond_038, bond_039,
bond_041, bond_044, bond_045, bond_047, bond_048, bond_049, bond_050, bond_051, bond_052,
bond_053, bond_054, bond_055, bond_056, bond_057, bond_058, bond_059, bond_060, bond_061,
bond_062, bond_063, bond_064, bond_068, bond_070, bond_071, bond_077, bond_078, bond_079,
bond_080, bond_081, bond_082, bond_083, bond_084, bond_085, bond_086, bond_087, bond_090,
bond_091, bond_092, bond_093

### Fund（48）

fund_002, fund_003, fund_004, fund_005, fund_006, fund_007, fund_008, fund_009, fund_011,
fund_014, fund_016, fund_017, fund_018, fund_020, fund_022, fund_023, fund_025, fund_026,
fund_027, fund_047, fund_048, fund_049, fund_050, fund_052, fund_059, fund_060,
fund_069, fund_071, fund_082, fund_091, fund_093, fund_094,
fund_095, fund_096, fund_097, fund_098, fund_099, fund_101, fund_104, fund_105, fund_106,
fund_107, fund_108, fund_109, fund_114, fund_118, fund_124, fund_125

### Macro（2）

macro_023, macro_024

### Stock（42）

stock_001, stock_003, stock_005, stock_010, stock_013, stock_015, stock_020, stock_032,
stock_033, stock_035, stock_039, stock_042, stock_060, stock_074, stock_078,
stock_081, stock_084, stock_092, stock_097, stock_100, stock_101, stock_103, stock_106, stock_107,
stock_126, stock_141, stock_148, stock_149, stock_151, stock_152, stock_153,
stock_154, stock_155, stock_156, stock_157, stock_159, stock_160, stock_162, stock_163,
stock_164, stock_165, stock_166

## 二分类映射

若实验只允许 `sql_only` 与 `sql_python` 两类，则将 `sql_orchestration_or_semantic` 并入 `sql_python`：

- `sql_only`：254
- `sql_python`：154（5 + 149）
- 合计：408
