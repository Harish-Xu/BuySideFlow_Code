from __future__ import annotations

import ast
import csv
import json
import re
from pathlib import Path
from typing import Any


_QUESTION_BLOCK_RE = re.compile(
    "(?ms)^\\*\\*[^*\\n]*?(\\d+)\\*\\*[:：]\\s*(.*?)(?=^\\*\\*[^*\\n]*?\\d+\\*\\*[:：]|\\Z)"
)
_CODE_BLOCK_RE = re.compile(
    r"(?ms)```(sql|python)\s*\n(.*?)```"
)
_BENCHMARK_TASK_BLOCK_RE = re.compile(r"(?ms)^###\s+(.+?)\s*\n(.*?)(?=^###\s+.+?\s*\n|\Z)")
_BOLD_BENCHMARK_SECTION_LINE_RE = re.compile(r"^\*\*([^*\n]+)\*\*(?:[:：]\s*)?(.*)$")
_PLAIN_BENCHMARK_SECTION_LINE_RE = re.compile(r"^([^:：\n]+?)(?:[:：]\s*(.*))?$")
_MARKDOWN_TABLE_RE = re.compile(r"(?ms)(^\|[^\n]*\|\s*$\n^\|(?:[-: ]+\|)+\s*$\n(?:^\|[^\n]*\|\s*$\n?)*)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]+)\]")
_RICH_TASK_BLOCK_RE = re.compile(r"(?ms)^##\s+Q(\d+)\s*\n(.*?)(?=^##\s+Q\d+\s*\n|\Z)")
_SECTION_RE = re.compile(r"(?ms)^###\s+([A-Za-z0-9_]+)\s*\n(.*?)(?=^###\s+[A-Za-z0-9_]+\s*\n|\Z)")
_SQL_TABLE_RE = re.compile(r"(?ix)\b(?:from|join|update|into)\s+([A-Za-z_][A-Za-z0-9_]*)")
_CTE_NAME_RE = re.compile(r"(?ix)\b(?:with|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(")
_RESULT_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_RESULT_TEXT_SUFFIXES = {".txt"}
_RESULT_CSV_SUFFIXES = {".csv"}
_REFERENCE_PRIMARY_FILENAMES = {"refer.py", "refer.sql", "result.csv", "picture.png", "abstract.txt"}
_REFERENCE_SIDECAR_SUFFIXES = {
    ".json",
    ".jsonl",
    ".parquet",
    ".pkl",
    ".pickle",
    ".feather",
    ".npy",
    ".npz",
    ".xlsx",
    ".xls",
}
_PLAIN_BENCHMARK_SECTION_LABELS = {
    "题面",
    "输出",
    "参考实现",
    "实现",
    "参考 SQL",
    "SQL",
    "标准评测观察日",
    "ETF池",
    "固定ETF池",
    "因子口径",
    "运行命令",
    "真实输出",
    "最终目标持仓输出",
}


def _collect_assignment_names(node: ast.AST) -> list[list[str]]:
    groups: list[list[str]] = []

    def collect_targets(target: ast.AST) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            names: list[str] = []
            for elt in target.elts:
                names.extend(collect_targets(elt))
            return names
        return []

    def visit_body(body: list[ast.stmt]) -> None:
        for stmt in body:
            if isinstance(stmt, ast.Assign):
                names: list[str] = []
                for target in stmt.targets:
                    names.extend(collect_targets(target))
                if names:
                    groups.append(names)
            elif isinstance(stmt, ast.AnnAssign):
                names = collect_targets(stmt.target)
                if names:
                    groups.append(names)
            elif isinstance(stmt, ast.AugAssign):
                names = collect_targets(stmt.target)
                if names:
                    groups.append(names)
            elif isinstance(stmt, ast.If):
                visit_body(stmt.body)

    visit_body(getattr(node, "body", []))
    return groups


def infer_result_vars(python_code: str) -> list[str]:
    """Infer result variable names from Python code via AST.

    Handles these patterns (in priority order):
    1. def main() / def run() / def detect_xxx() entry point — return empty so
       _fallback_result_vars captures stdout of the entry function.
    2. if __name__ == '__main__': result = run_xxx() — result is the var.
    3. print(json.dumps(var, ...)) — var is the result (not 'json').
    4. print(var) / print(var.method(...)) at module level.
    5. Multi-line / stdout-only answers fall back to ["_main_output"].
    6. Last module-level assignment that looks like a result (not an ALL_CAPS constant).
    """
    if not python_code or not python_code.strip():
        return []

    # Normalize curly/smart quotes so AST can parse the code
    normalized = python_code.translate(str.maketrans("\u2018\u2019\u201c\u201d", "''\"\""))

    try:
        tree = ast.parse(normalized)
    except SyntaxError:
        # AST failed — regex fallback
        m = None
        for m in re.finditer(r'\bprint\s*\(\s*([A-Za-z_]\w*)(?:\s*[\.,)])', normalized):
            pass
        if m:
            return [m.group(1)]
        if "print(" in normalized:
            return ["_main_output"]
        for m in re.finditer(r'^([A-Za-z_]\w*)\s*=', normalized, re.MULTILINE):
            pass
        return [m.group(1)] if m else []

    # Pattern 1: any top-level function def (main/run/detect_xxx etc.) means
    # results live inside a function — return empty, let fallback handle it.
    has_entry_func = any(isinstance(n, ast.FunctionDef) for n in tree.body)
    if has_entry_func:
        # Exception: if __name__ == '__main__' block assigns a variable at module level,
        # that variable IS accessible (e.g. result = run_xxx())
        main_block_var: str | None = None
        for node in tree.body:
            if (isinstance(node, ast.If)
                    and isinstance(node.test, ast.Compare)
                    and isinstance(node.test.left, ast.Name)
                    and node.test.left.id == "__name__"):
                for stmt in node.body:
                    if isinstance(stmt, ast.Assign):
                        for t in stmt.targets:
                            if isinstance(t, ast.Name):
                                main_block_var = t.id
        if main_block_var:
            return [main_block_var]
        return []

    def _chase_to_name(node: ast.AST) -> str | None:
        """Extract base variable name through Call/Attribute/Subscript chains."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, (ast.Subscript, ast.Attribute)):
            return _chase_to_name(node.value)
        if isinstance(node, ast.Call):
            return _chase_to_name(node.func)
        return None

    # Pattern 2: print(json.dumps(var, ...)) — extract var, not 'json'
    # Pattern 3: print(var) / print(var.method(...)) / print(df[cols].head().to_string())
    printed_var: str | None = None
    print_call_count = 0
    saw_complex_print = False
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id == "print" and call.args:
                print_call_count += 1
                arg = call.args[0]
                # print(json.dumps(var, ...)) → var
                if (isinstance(arg, ast.Call)
                        and isinstance(arg.func, ast.Attribute)
                        and arg.func.attr == "dumps"
                        and isinstance(arg.func.value, ast.Name)
                        and arg.func.value.id == "json"
                        and arg.args
                        and isinstance(arg.args[0], ast.Name)):
                    printed_var = arg.args[0].id
                # print(var)
                elif isinstance(arg, ast.Name):
                    printed_var = arg.id
                # print(var.method(...)) or print(df[cols].head().to_string())
                elif isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute):
                    base_name = _chase_to_name(arg.func.value)
                    if base_name:
                        printed_var = base_name
                    else:
                        saw_complex_print = True
                else:
                    saw_complex_print = True

    if print_call_count > 1 or saw_complex_print:
        return ["_main_output"]
    if printed_var:
        return [printed_var]

    # Pattern 4: last module-level assignment that is NOT an ALL_CAPS constant
    groups = _collect_assignment_names(tree)
    for group in reversed(groups):
        candidates = [n for n in group if n and not n.startswith("_") and not n.isupper()]
        if candidates:
            return candidates
    return []


def _infer_difficulty(sql_code: str, python_code: str, question: str) -> str:
    """推断题目难度：easy / medium / hard。

    - hard: 含窗口函数、可视化/图表、多层嵌套 SQL(CTE>=2 或 select 出现>2 次)。
    - medium: 多表 JOIN、sql+python 组合、Python 含多个函数定义。
    - easy: 单表 SQL 或简单 Python。
    """
    combined_code = ((sql_code or "") + "\n" + (python_code or "")).lower()
    combined_text = ((question or "") + "\n" + (sql_code or "") + "\n" + (python_code or "")).lower()

    hard_keywords = [
        "窗口函数", "over(", "row_number", "rank()", "dense_rank",
        "可视化", "图表", "matplotlib", "seaborn", "plt.", "plotly", "画图", "绘图",
    ]
    if any(kw in combined_text for kw in hard_keywords):
        return "hard"

    cte_count = len(_CTE_NAME_RE.findall(sql_code)) if sql_code else 0
    select_count = sql_code.lower().count("select") if sql_code else 0
    if cte_count >= 2 or select_count > 2:
        return "hard"

    tables = infer_schema_tables(sql_code, python_code)
    if len(tables) > 1:
        return "medium"

    if sql_code and python_code:
        return "medium"

    if python_code:
        try:
            tree = ast.parse(python_code)
            func_count = sum(1 for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
            if func_count > 1:
                return "medium"
        except SyntaxError:
            pass

    return "easy"


def infer_schema_tables(sql_code: str = "", python_code: str = "") -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    combined = "\n".join(part for part in (sql_code, python_code) if part)
    cte_names = {name.lower() for name in _CTE_NAME_RE.findall(sql_code)}
    for raw_name in _SQL_TABLE_RE.findall(combined):
        table_name = raw_name.strip()
        lower_name = table_name.lower()
        if not table_name or lower_name in seen or lower_name in cte_names:
            continue
        seen.add(lower_name)
        ordered.append(table_name)
    return ordered


def _extract_markdown_links(text: str) -> list[str]:
    out: list[str] = []
    for match in _MARKDOWN_LINK_RE.finditer(text or ""):
        value = match.group(1).strip()
        if value and value not in out:
            out.append(value)
    return out


def _resolve_reference_code(base_dir: Path, link_names: list[str]) -> tuple[str, str]:
    sql_code = ""
    python_code = ""
    for link_name in link_names:
        candidate = (base_dir / link_name).resolve()
        if not candidate.exists() or not candidate.is_file():
            continue
        suffix = candidate.suffix.lower()
        try:
            content = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = candidate.read_text(encoding="utf-8", errors="ignore")
        if suffix == ".sql" and not sql_code:
            sql_code = content.strip()
        elif suffix == ".py" and not python_code:
            python_code = content.strip()
    return sql_code, python_code


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _numeric_suffix(value: str) -> int | None:
    matches = re.findall(r"\d+", value or "")
    if not matches:
        return None
    try:
        return int(matches[-1])
    except ValueError:
        return None


def _result_dir_sort_key(path: Path) -> tuple[int, str]:
    numeric = _numeric_suffix(path.name)
    return (numeric if numeric is not None else 10**9, path.name.lower())


def _find_result_dir(results_dir: Path, instance_id: str, index: int) -> Path | None:
    if not results_dir.exists():
        return None

    exact = results_dir / instance_id
    if exact.is_dir():
        return exact

    instance_id_lower = instance_id.lower()
    for candidate in sorted((p for p in results_dir.iterdir() if p.is_dir()), key=_result_dir_sort_key):
        if candidate.name.lower() == instance_id_lower:
            return candidate

    target_number = _numeric_suffix(instance_id)
    if target_number is not None:
        for candidate in sorted((p for p in results_dir.iterdir() if p.is_dir()), key=_result_dir_sort_key):
            if _numeric_suffix(candidate.name) == target_number:
                return candidate

    ordered = sorted((p for p in results_dir.iterdir() if p.is_dir()), key=_result_dir_sort_key)
    if 0 <= index < len(ordered):
        return ordered[index]
    return None


def _collect_reference_code_from_dir(result_dir: Path | None) -> tuple[str, str, list[str]]:
    if result_dir is None or not result_dir.exists():
        return "", "", []

    code_files = [
        path
        for path in result_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".sql", ".py"}
    ]

    def sort_key(path: Path) -> tuple[int, str]:
        is_refer = 0 if path.stem.lower().startswith("refer") else 1
        suffix_order = 0 if path.suffix.lower() == ".sql" else 1
        return (is_refer, suffix_order, path.name.lower())

    sql_parts: list[str] = []
    python_parts: list[str] = []
    link_names: list[str] = []
    for path in sorted(code_files, key=sort_key):
        content = _read_text_file(path).strip()
        if not content:
            continue
        link_names.append(path.name)
        if path.suffix.lower() == ".sql":
            sql_parts.append(content)
        elif path.suffix.lower() == ".py":
            python_parts.append(content)

    return "\n\n".join(sql_parts), "\n\n".join(python_parts), link_names


def _collect_reference_artifacts(result_dir: Path | None) -> list[dict[str, str]]:
    if result_dir is None or not result_dir.exists():
        return []

    artifacts: list[dict[str, str]] = []
    for path in sorted((p for p in result_dir.iterdir() if p.is_file()), key=lambda item: item.name.lower()):
        suffix = path.suffix.lower()
        kind = ""
        if suffix in _RESULT_CSV_SUFFIXES:
            kind = "csv"
        elif suffix in _RESULT_IMAGE_SUFFIXES:
            kind = "image"
        elif suffix in _RESULT_TEXT_SUFFIXES:
            kind = "text"
        if not kind:
            continue
        artifacts.append({"kind": kind, "path": str(path.resolve()), "name": path.name})
    return artifacts


def _collect_reference_sidecars(result_dir: Path | None) -> list[dict[str, str]]:
    if result_dir is None or not result_dir.exists():
        return []

    sidecars: list[dict[str, str]] = []
    for path in sorted((p for p in result_dir.iterdir() if p.is_file()), key=lambda item: item.name.lower()):
        name = path.name
        if name.startswith(".") or name.lower() in _REFERENCE_PRIMARY_FILENAMES:
            continue
        if path.suffix.lower() not in _REFERENCE_SIDECAR_SUFFIXES:
            continue
        sidecars.append({"kind": "sidecar", "path": str(path.resolve()), "name": name})
    return sidecars


def _infer_artifact_evaluation_kind(artifacts: list[dict[str, str]]) -> str:
    kinds = {item.get("kind") for item in artifacts}
    if "image" in kinds:
        return "vision"
    if "text" in kinds and "csv" in kinds:
        return "mixed"
    if "text" in kinds:
        return "text_ai"
    return "csv"


def _format_record_inputs(inputs: Any) -> str:
    if inputs in (None, "", {}, []):
        return ""
    try:
        rendered = json.dumps(inputs, ensure_ascii=False, indent=2, sort_keys=True)
    except TypeError:
        rendered = str(inputs)
    return (
        "输入参数（必须使用；若题面出现 as_of_date、etf_pool 等变量，以这里为准）：\n"
        f"{rendered}"
    )


def _read_csv_header(path: Path) -> list[str]:
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                reader = csv.reader(file)
                return [cell.strip() for cell in next(reader, [])]
        except UnicodeDecodeError:
            continue
    return []


def _csv_artifact_headers(artifacts: list[dict[str, str]]) -> list[str]:
    for artifact in artifacts:
        if artifact.get("kind") != "csv":
            continue
        path = Path(artifact.get("path") or "")
        if path.exists() and path.is_file():
            return [name for name in _read_csv_header(path) if name]
    return []


def _build_folder_output_contract(csv_headers: list[str]) -> str:
    if not csv_headers:
        return ""
    columns = ", ".join(csv_headers)
    return (
        "最终表格输出必须包含且仅包含以下列，列名和顺序保持一致："
        f"{columns}。"
    )


def _read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL in {path} line {line_number}: {exc}") from exc
        if isinstance(record, dict):
            records.append(record)
    return records


def _question_jsonl_files(source_path: Path) -> list[Path]:
    if source_path.is_file() and source_path.suffix.lower() == ".jsonl":
        return [source_path]
    question_dir = source_path / "question"
    if not question_dir.exists():
        return []
    files = sorted(question_dir.glob("*.jsonl"), key=lambda path: (path.name.lower() != "fund.jsonl", path.name.lower()))
    return files


def _parse_folder_text2sql_tasks(source_path: Path) -> list[dict[str, Any]]:
    root = source_path
    if source_path.is_file() and source_path.suffix.lower() == ".jsonl":
        root = source_path.parent.parent if source_path.parent.name == "question" else source_path.parent

    question_files = _question_jsonl_files(source_path)
    if not question_files:
        return []

    results_dir = root / "results"
    tasks: list[dict[str, Any]] = []
    global_index = 0

    for question_file in question_files:
        for record in _read_jsonl_records(question_file):
            raw_id = (
                record.get("instance_id")
                or record.get("id")
                or record.get("qid")
                or f"{question_file.stem}_{global_index + 1:03d}"
            )
            task_id = str(raw_id).strip()
            question = str(record.get("instruction") or record.get("question") or record.get("query") or "").strip()
            if not task_id or not question:
                global_index += 1
                continue
            inputs = record.get("inputs")
            input_block = _format_record_inputs(inputs)
            if input_block:
                question = f"{question}\n\n{input_block}"

            result_dir = _find_result_dir(results_dir, task_id, global_index)
            sql_code, python_code, reference_links = _collect_reference_code_from_dir(result_dir)
            artifacts = _collect_reference_artifacts(result_dir)
            sidecars = _collect_reference_sidecars(result_dir)
            csv_headers = _csv_artifact_headers(artifacts)
            schema_tables = infer_schema_tables(sql_code, python_code)
            evaluation_kind = _infer_artifact_evaluation_kind(artifacts)

            notes = [
                f"source_folder: {root}",
                f"question_file: {question_file.name}",
                f"result_dir: {result_dir if result_dir is not None else '(missing)'}",
                f"evaluation_kind: {evaluation_kind}",
                f"reference_artifacts: {', '.join(item['name'] for item in artifacts) if artifacts else '(none)'}",
                f"reference_sidecars: {', '.join(item['name'] for item in sidecars) if sidecars else '(none)'}",
                f"inputs: {json.dumps(inputs, ensure_ascii=False, sort_keys=True) if input_block else '(none)'}",
            ]

            task = _build_task(
                task_id=task_id,
                question=question,
                canonical_question=question,
                desk_style_paraphrases="",
                task_spec="",
                output_contract=_build_folder_output_contract(csv_headers),
                evaluation_contract="",
                notes="\n".join(notes),
                sql_code=sql_code,
                python_code=python_code,
                schema_tables=schema_tables,
            )
            task["benchmark_title"] = task_id
            task["reference_links"] = reference_links
            task["reference_results"] = []
            task["reference_artifact_paths"] = artifacts
            task["reference_sidecar_paths"] = sidecars
            task["evaluation_kind"] = evaluation_kind
            task["result_dir"] = str(result_dir.resolve()) if result_dir is not None else ""
            task["inputs"] = inputs or {}
            task["strict_output_schema"] = bool(csv_headers)
            tasks.append(task)
            global_index += 1

    return tasks


def _split_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip().replace("\\|", "|") for cell in stripped.strip("|").split("|")]


def _parse_markdown_table_cell(text: str, *, column_name: str = "") -> Any:
    value = text.strip().strip("`")
    if value == "":
        return ""
    lowered_column = column_name.strip().lower()
    if any(token in lowered_column for token in ("code", "name", "date")):
        return value
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?", value):
        try:
            return float(value)
        except ValueError:
            return value
    if re.fullmatch(r"[-+]?\d+(?:[eE][-+]?\d+)?", value):
        unsigned = value.lstrip("+-")
        if len(unsigned) > 1 and unsigned.startswith("0"):
            return value
        try:
            return int(value)
        except ValueError:
            return value
    return value


def _parse_markdown_table_records(table_text: str) -> list[dict[str, Any]]:
    lines = [line.rstrip() for line in table_text.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    headers = _split_markdown_row(lines[0])
    if not headers:
        return []

    records: list[dict[str, Any]] = []
    for line in lines[2:]:
        cells = _split_markdown_row(line)
        if not cells:
            continue
        if len(cells) < len(headers):
            cells += [""] * (len(headers) - len(cells))
        elif len(cells) > len(headers):
            cells = cells[: len(headers)]
        row: dict[str, Any] = {}
        for key, cell in zip(headers, cells, strict=False):
            row[key] = _parse_markdown_table_cell(cell, column_name=key)
        records.append(row)
    return records


def _append_benchmark_section(sections: dict[str, str], label: str, lines: list[str]) -> None:
    content = "\n".join(lines).strip()
    if label in sections and content:
        sections[label] = f"{sections[label]}\n\n{content}".strip()
    else:
        sections[label] = content


def _extract_benchmark_section_header(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped:
        return None

    bold_match = _BOLD_BENCHMARK_SECTION_LINE_RE.fullmatch(stripped)
    if bold_match:
        return bold_match.group(1).strip(), (bold_match.group(2) or "").strip()

    plain_match = _PLAIN_BENCHMARK_SECTION_LINE_RE.fullmatch(stripped)
    if not plain_match or (":" not in stripped and "：" not in stripped):
        return None

    label = plain_match.group(1).strip()
    if label not in _PLAIN_BENCHMARK_SECTION_LABELS:
        return None
    return label, (plain_match.group(2) or "").strip()


def _parse_benchmark_sections(block: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_label: str | None = None
    current_lines: list[str] = []

    for raw_line in block.strip().splitlines():
        header = _extract_benchmark_section_header(raw_line)
        if header is not None:
            if current_label is not None:
                _append_benchmark_section(sections, current_label, current_lines)
            current_label, inline_content = header
            current_lines = [inline_content] if inline_content else []
            continue

        if current_label is not None:
            current_lines.append(raw_line.rstrip())

    if current_label is not None:
        _append_benchmark_section(sections, current_label, current_lines)
    return sections


def _split_output_contract(output_block: str) -> tuple[str, str]:
    if not output_block.strip():
        return "", ""

    output_lines: list[str] = []
    eval_lines: list[str] = []
    for raw_line in output_block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "稳定输出约束" in line or "评估输出" in line:
            eval_lines.append(line)
        else:
            output_lines.append(line)

    if not output_lines and output_block.strip():
        output_lines.append(output_block.strip())
    return "\n".join(output_lines).strip(), "\n".join(eval_lines).strip()


def _build_benchmark_question(question: str, sections: dict[str, str]) -> str:
    question = question.strip()
    supplemental: list[str] = []
    for label, content in sections.items():
        normalized = label.strip()
        if normalized in {"题面", "参考实现", "实现", "SQL", "参考 SQL"}:
            continue
        if not content.strip():
            continue
        title = "输出要求" if normalized == "输出" else normalized
        supplemental.append(f"{title}：\n{content.strip()}")

    if not supplemental:
        return question
    return f"{question}\n\n补充要求：\n" + "\n\n".join(supplemental)


def _parse_benchmark_text2sql_tasks(text: str, *, source_path: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for index, match in enumerate(_BENCHMARK_TASK_BLOCK_RE.finditer(text), start=1):
        title = match.group(1).strip()
        block = match.group(2).strip()

        sections = _parse_benchmark_sections(block)
        question_text = sections.get("题面", "").strip()
        if not question_text:
            continue

        output_contract, evaluation_contract = _split_output_contract(sections.get("输出", ""))
        combined_question = _build_benchmark_question(question_text, sections)
        table_matches = list(_MARKDOWN_TABLE_RE.finditer(block))
        reference_results: list[Any] = []
        if table_matches:
            records = _parse_markdown_table_records(table_matches[-1].group(1))
            reference_results = [records]

        link_names: list[str] = []
        for label in ("参考 SQL", "SQL", "参考实现", "实现"):
            link_names.extend(_extract_markdown_links(sections.get(label, "")))
        link_names = list(dict.fromkeys(link_names))
        sql_code, python_code = _resolve_reference_code(source_path.parent, link_names)
        schema_tables = infer_schema_tables(sql_code, python_code)
        task_spec = "\n\n".join(
            f"{label}：\n{content.strip()}"
            for label, content in sections.items()
            if label not in {"题面", "输出", "参考实现", "实现", "SQL", "参考 SQL"} and content.strip()
        ).strip()
        notes = [
            f"benchmark_title: {title}",
            f"reference_links: {', '.join(link_names) if link_names else '(none)'}",
            f"has_reference_results: {'yes' if reference_results else 'no'}",
        ]

        tasks.append(
            _build_task(
                task_id=f"q{index}",
                question=combined_question,
                canonical_question=question_text,
                desk_style_paraphrases="",
                task_spec=task_spec,
                output_contract=output_contract,
                evaluation_contract=evaluation_contract,
                notes="\n".join(notes),
                sql_code=sql_code,
                python_code=python_code,
                schema_tables=schema_tables,
            )
        )
        tasks[-1]["benchmark_title"] = title
        tasks[-1]["reference_results"] = reference_results
        tasks[-1]["reference_links"] = link_names
    return tasks


def _parse_rich_markdown_table(block: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or stripped.count("|") < 3:
            continue
        parts = [part.strip() for part in stripped.strip("|").split("|")]
        if len(parts) != 2:
            continue
        key, value = parts
        if not key or not value:
            continue
        if set(key) == {"-"} or set(value) == {"-"}:
            continue
        meta[key] = value
    return meta


def _parse_rich_sections(block: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    for name, content in _SECTION_RE.findall(block):
        sections[name.strip().lower()] = content.strip()
    return sections


def _extract_first_code_blocks(block: str) -> tuple[str, str]:
    sql_code = ""
    python_code = ""
    for lang, code in _CODE_BLOCK_RE.findall(block):
        normalized_lang = lang.strip().lower()
        code = code.strip()
        if normalized_lang == "sql" and not sql_code:
            sql_code = code
        elif normalized_lang == "python" and not python_code:
            python_code = code
    return sql_code, python_code


def _build_task(
    *,
    task_id: str,
    question: str,
    canonical_question: str,
    desk_style_paraphrases: str,
    task_spec: str,
    output_contract: str,
    evaluation_contract: str,
    notes: str,
    sql_code: str,
    python_code: str,
    schema_tables: list[str],
) -> dict[str, Any]:
    if sql_code and python_code:
        mode = "sql+python"
    elif python_code:
        mode = "python"
    else:
        mode = "sql"

    return {
        "id": task_id,
        "question": question,
        "canonical_question": canonical_question or question,
        "desk_style_paraphrases": desk_style_paraphrases,
        "task_spec": task_spec,
        "output_contract": output_contract,
        "evaluation_contract": evaluation_contract,
        "notes": notes,
        "schema_tables": schema_tables,
        "difficulty": _infer_difficulty(sql_code, python_code, question),
        "mode": mode,
        "sql_code": sql_code,
        "python_code": python_code,
        "result_vars": infer_result_vars(python_code) if python_code else [],
    }


def _parse_rich_text2sql_tasks(text: str) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for match in _RICH_TASK_BLOCK_RE.finditer(text):
        raw_id = match.group(1)
        block = match.group(2).strip()
        meta = _parse_rich_markdown_table(block)
        sections = _parse_rich_sections(block)
        sql_code, python_code = _extract_first_code_blocks(block)
        if not sql_code and not python_code:
            continue

        question = sections.get("canonical_question", "").strip() or meta.get("question", "").strip()
        if not question:
            continue

        schema_tables = [item.strip() for item in meta.get("schema_tables", "").split(",") if item.strip()]
        tasks.append(
            _build_task(
                task_id=f"q{raw_id}",
                question=question,
                canonical_question=sections.get("canonical_question", "").strip() or question,
                desk_style_paraphrases=sections.get("desk_style_paraphrases", "").strip(),
                task_spec=sections.get("task_spec", "").strip(),
                output_contract=sections.get("output_contract", "").strip(),
                evaluation_contract=sections.get("evaluation_contract", "").strip(),
                notes=sections.get("notes", "").strip(),
                sql_code=sql_code,
                python_code=python_code,
                schema_tables=schema_tables,
            )
        )
    return tasks


def parse_text2sql_tasks(path: str | Path) -> list[dict[str, Any]]:
    source_path = Path(path)
    if source_path.is_dir() or source_path.suffix.lower() == ".jsonl":
        folder_tasks = _parse_folder_text2sql_tasks(source_path)
        if folder_tasks:
            return folder_tasks

    text = source_path.read_text(encoding="utf-8")

    benchmark_tasks = _parse_benchmark_text2sql_tasks(text, source_path=source_path)
    if benchmark_tasks:
        return benchmark_tasks

    rich_tasks = _parse_rich_text2sql_tasks(text)
    if rich_tasks:
        return rich_tasks

    tasks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for match in _QUESTION_BLOCK_RE.finditer(text):
        raw_id = match.group(1)
        body = match.group(2).strip()
        question = re.split(r"```(?:sql|python)\s*", body, maxsplit=1)[0].strip()
        sql_code, python_code = _extract_first_code_blocks(body)
        if not sql_code and not python_code:
            continue

        base_id = f"q{raw_id}"
        task_id = base_id
        if task_id in seen_ids:
            candidate = int(raw_id) + 1
            while f"q{candidate}" in seen_ids:
                candidate += 1
            task_id = f"q{candidate}"
        seen_ids.add(task_id)

        tasks.append(
            _build_task(
                task_id=task_id,
                question=question,
                canonical_question=question,
                desk_style_paraphrases="",
                task_spec="",
                output_contract="",
                evaluation_contract="",
                notes="",
                sql_code=sql_code,
                python_code=python_code,
                schema_tables=[],
            )
        )

    return tasks
