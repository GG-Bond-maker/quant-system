"""_jsonable_validation_errors 直接单元核验：只转异常对象，不误伤正常字段。"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str((Path(__file__).resolve().parents[2] / "backend")))
from fastapi.encoders import jsonable_encoder  # noqa: E402

from app.core.errors import (APIResponse, ERR_PARAMS,  # noqa: E402
                             _jsonable_validation_errors, fail)

errors = [
    # 自定义 validator 的 ValueError（必须转字符串）
    {"type": "value_error", "loc": ("body", "end_date"),
     "msg": "Value error, x", "input": "2024-01-01",
     "ctx": {"error": ValueError("结束日期不能早于开始日期")}},
    # 内置约束 ctx（数值/字符串，必须原样保留）
    {"type": "less_than_equal", "loc": ("body", "weight"),
     "msg": "le", "input": 1.5, "ctx": {"le": 1.0}},
    {"type": "string_pattern_mismatch", "loc": ("query", "sort"),
     "msg": "pat", "input": "bad", "ctx": {"pattern": "^(a|b)$"}},
    # 无 ctx
    {"type": "missing", "loc": ("body", "assets"), "msg": "Field required"},
    # ctx 含 list/dict/None（必须原样保留，不得被转成字符串）
    {"type": "x", "loc": ("body", "y"), "msg": "m", "input": None,
     "ctx": {"choices": ["a", "b"], "meta": {"k": 1}, "nothing": None}},
]

out = _jsonable_validation_errors(list(errors))
print("原始 ctx.error 类型:", type(errors[0]["ctx"]["error"]).__name__)
print("转换后 ctx.error 类型:", type(out[0]["ctx"]["error"]).__name__,
      "值=", repr(out[0]["ctx"]["error"]))
print("ctx.le 保持:", repr(out[1]["ctx"]["le"]), type(out[1]["ctx"]["le"]).__name__)
print("ctx.pattern 保持:", repr(out[2]["ctx"]["pattern"]))
print("无 ctx 项不变:", out[3])
print("ctx choices/meta/nothing 保持:",
      repr(out[4]["ctx"]["choices"]), repr(out[4]["ctx"]["meta"]), repr(out[4]["ctx"]["nothing"]))

# 其余字段逐一比对
for a, b in zip(errors, out):
    for k in ("type", "loc", "msg", "input"):
        assert a.get(k) == b.get(k), f"字段 {k} 被改动：{a.get(k)!r} -> {b.get(k)!r}"
    assert set(a.keys()) == set(b.keys()), f"键集合变化：{set(a)} -> {set(b)}"
print("其余字段(type/loc/msg/input) 全部一致: OK")

# 关键：现在必须能过 APIResponse + jsonable_encoder（修复前会 PydanticSerializationError）
enc = jsonable_encoder(fail(ERR_PARAMS, "请求参数错误", out))
s = json.dumps(enc, ensure_ascii=False)
print("序列化成功, 长度=", len(s))
print("SAMPLE:", s[:160])

# 反证：不转换时仍会崩（确认这正是原缺陷）
try:
    jsonable_encoder(fail(ERR_PARAMS, "请求参数错误", list(errors)))
    print("反证：未转换竟未崩（与预期不符）")
except Exception as e:
    print("反证：未转换 ->", type(e).__name__, str(e)[:80])
