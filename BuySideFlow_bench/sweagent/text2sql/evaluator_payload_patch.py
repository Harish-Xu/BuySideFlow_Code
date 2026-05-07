# Payload extraction helper - 专门处理带噪声的print输出
import pandas as pd
from typing import Any

def _is_simple_metadata(obj: Any) -> bool:
    """判断一个对象是否是简单的metadata（噪声）而非核心payload。

    Heuristics:
    - 简单字符串（print输出的key=value）
    - 简单数字/布尔值
    - 简单的key-value dict（所有value都是标量）
    """
    if obj is None:
        return True
    if isinstance(obj, (int, float, bool)):
        # 标量可能是metadata也可能是payload，需要看上下文
        # 暂时认为单独的小数值更可能是metadata
        return True
    if isinstance(obj, str):
        # 字符串通常是print输出，认为是metadata
        # 但如果字符串很长（>100字符）且包含结构化数据，可能是payload
        if len(obj) > 100:
            return False
        return True
    if isinstance(obj, (list, tuple)):
        # 空列表或单元素列表可能是metadata
        if len(obj) <= 1:
            return True
        # 如果所有元素都是简单类型，可能是metadata
        if all(isinstance(x, (int, float, bool, str)) for x in obj):
            return True
        return False
    if isinstance(obj, dict):
        # 空dict
        if not obj:
            return True
        # 如果所有value都是简单标量，可能是metadata（如配置信息）
        if all(isinstance(v, (int, float, bool, str)) for v in obj.values()):
            return True
        return False
    if isinstance(obj, pd.DataFrame):
        # DataFrame永远是payload
        return False
    if isinstance(obj, bytes):
        # 图片是payload
        return False
    return False


def _extract_payloads(results: list[Any]) -> list[Any]:
    """从结果列表中提取有效payload，过滤掉metadata/noise。"""
    payloads = []
    for item in results:
        if not _is_simple_metadata(item):
            payloads.append(item)
    return payloads


def _flatten_dict_to_payloads(obj: dict) -> list[Any]:
    """把dict展开成payload列表，把包含DataFrame的value提取出来。"""
    payloads = []
    for k, v in obj.items():
        if isinstance(v, pd.DataFrame):
            payloads.append(v)
        elif isinstance(v, (list, tuple)) and len(v) > 0:
            # 检查list里是否有DataFrame
            for item in v:
                if isinstance(item, pd.DataFrame):
                    payloads.append(item)
                elif not _is_simple_metadata(item):
                    payloads.append(item)
        elif not _is_simple_metadata(v):
            payloads.append(v)
    return payloads
