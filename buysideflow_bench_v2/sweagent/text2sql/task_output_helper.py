"""Small output adapter used by legacy reference implementations."""

from __future__ import annotations

import builtins
import io
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pandas as pd


class PrintCapture(io.StringIO):
    """Capture stdout-bound ``print`` calls while preserving normal output."""

    def __init__(self, original_print) -> None:
        super().__init__()
        self._original_print = original_print

    def restore(self) -> None:
        if builtins.print is not self._original_print:
            builtins.print = self._original_print


def install_print_capture() -> PrintCapture:
    original_print = builtins.print
    capture = PrintCapture(original_print)

    def captured_print(*args, **kwargs) -> None:
        target = kwargs.get("file")
        original_print(*args, **kwargs)
        if target is None or target is sys.stdout:
            capture_kwargs = dict(kwargs)
            capture_kwargs["file"] = capture
            capture_kwargs["flush"] = False
            original_print(*args, **capture_kwargs)

    builtins.print = captured_print
    return capture


def _last_json_value(text: str) -> Any | None:
    decoder = json.JSONDecoder()
    last_value: Any | None = None
    best_end = -1
    best_start = len(text)
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        absolute_end = index + end
        if absolute_end > best_end or (absolute_end == best_end and index < best_start):
            last_value = value
            best_end = absolute_end
            best_start = index
    return last_value


def _json_cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _result_frame(result: Any) -> pd.DataFrame:
    if isinstance(result, pd.DataFrame):
        return result.copy()
    if isinstance(result, pd.Series):
        return result.to_frame().T
    if isinstance(result, list):
        return pd.DataFrame(result)
    if not isinstance(result, dict):
        return pd.DataFrame({"result": [result]})

    row_list_keys = [
        key
        for key, value in result.items()
        if isinstance(value, list) and (not value or all(isinstance(item, dict) for item in value))
    ]
    if row_list_keys:
        row_key = row_list_keys[-1]
        frame = pd.DataFrame(result[row_key])
        metadata: dict[str, Any] = {}
        for key, value in result.items():
            if key == row_key:
                continue
            if isinstance(value, dict):
                metadata.update({f"{key}_{subkey}": subvalue for subkey, subvalue in value.items()})
            else:
                metadata[key] = value
        if frame.empty:
            return pd.DataFrame([{key: _json_cell(value) for key, value in metadata.items()}])
        for key, value in metadata.items():
            frame[key] = _json_cell(value)
        return frame

    return pd.DataFrame(
        [{key: _json_cell(value) for key, value in result.items()}]
    )


def _promote_single_picture(output_dir: Path) -> None:
    target = output_dir / "picture.png"
    if target.exists():
        return
    candidates = sorted(
        path for path in output_dir.rglob("*.png") if path.is_file() and path != target
    )
    if len(candidates) == 1:
        shutil.copyfile(candidates[0], target)


def finalize_run(result: Any, *, print_buffer: PrintCapture | None = None) -> pd.DataFrame:
    """Serialize a reference result to the benchmark's canonical artifacts."""

    try:
        if result is None and print_buffer is not None:
            result = _last_json_value(print_buffer.getvalue())
        if result is None:
            raise ValueError("No result object or JSON output was captured")
        frame = _result_frame(result)
        output_dir = Path.cwd()
        frame.to_csv(output_dir / "result.csv", index=False, encoding="utf-8-sig")
        _promote_single_picture(output_dir)
        return frame
    finally:
        if print_buffer is not None:
            print_buffer.restore()
