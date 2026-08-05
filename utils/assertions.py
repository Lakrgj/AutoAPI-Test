# -*- coding: utf-8 -*-
"""
断言引擎（assertions）—— 三重断言
===================================================================
1. 响应断言：状态码 / 业务码 code / 业务消息 msg / jsonpath 值 / jsonschema 契约
2. 数据库断言：执行 SQL 并比对结果（下单后订单表记录、状态字段等）
3. Redis 断言：key 是否存在 / value 比对（库存缓存、Token 缓存等）

设计要点：
- 失败统一走 _fail()：记录 ERROR 日志 + 附加 Allure 失败快照 + 抛 AssertionError
- jsonpath 断言支持 not_null / re:正则 两种特殊期望
- assert_response(expected) 为数据驱动用例的统一断言入口
"""
import re
import json
import traceback
from typing import Any

import jsonpath
import jsonschema
import allure

from utils.logger import get_logger

log = get_logger("assert")


# =====================================================================
# 内部工具：失败统一处理
# =====================================================================
def _fail(message: str, context: dict = None):
    """
    断言失败统一处理：
    1. 记录 ERROR 日志（含上下文）
    2. 附加 Allure 失败快照（headless 环境无浏览器截图，
       以「失败快照」HTML/文本形式附加，保留请求响应关键信息）
    3. 抛出 AssertionError，由 pytest 捕获标记失败
    """
    log.error("断言失败 | {}", message)
    if context:
        log.error("断言上下文 | {}", json.dumps(context, ensure_ascii=False, default=str))

    # 附加失败快照到 Allure（替代浏览器截图，CI 环境更实用）
    try:
        snapshot = f"【断言失败】\n{message}\n\n"
        if context:
            snapshot += "【上下文】\n" + json.dumps(
                context, ensure_ascii=False, indent=2, default=str
            )
        snapshot += "\n\n【调用栈】\n" + traceback.format_exc()
        allure.attach(
            snapshot,
            name="失败快照",
            attachment_type=allure.attachment_type.TEXT,
        )
    except Exception as e:  # pragma: no cover - allure 在非 pytest 环境下不可用
        log.debug("attach 失败快照失败: {}", e)

    raise AssertionError(message)


# =====================================================================
# 一、响应断言
# =====================================================================
def assert_status_code(response, expected: int):
    """断言 HTTP 状态码"""
    actual = response.status_code
    if actual != expected:
        _fail(
            f"状态码断言失败：期望 {expected}，实际 {actual}",
            {"expected": expected, "actual": actual, "url": response.url},
        )
    log.info("状态码断言通过 | expected={} actual={}", expected, actual)


def assert_business_code(response, expected_code, expected_msg: str = None):
    """
    断言业务码 code（及可选 msg）
    - 兼容响应体放在 $.code 或 $.data.code
    - msg 支持正则匹配（以 re: 开头）
    """
    try:
        body = response.json()
    except Exception:
        _fail("响应非 JSON，无法断言业务码", {"text": response.text[:300]})

    actual_code = body.get("code", body.get("data", {}).get("code") if isinstance(body.get("data"), dict) else None)
    if actual_code != expected_code:
        _fail(
            f"业务码断言失败：期望 {expected_code}，实际 {actual_code}",
            {"expected_code": expected_code, "actual_code": actual_code, "body": body},
        )

    if expected_msg is not None:
        actual_msg = body.get("msg", body.get("message", ""))
        if expected_msg.startswith("re:"):
            if not re.search(expected_msg[3:], str(actual_msg)):
                _fail(
                    f"业务消息正则断言失败：期望匹配 {expected_msg}，实际 {actual_msg}",
                    {"expected_msg": expected_msg, "actual_msg": actual_msg},
                )
        else:
            if expected_msg not in str(actual_msg):
                _fail(
                    f"业务消息断言失败：期望包含 {expected_msg}，实际 {actual_msg}",
                    {"expected_msg": expected_msg, "actual_msg": actual_msg},
                )

    log.info("业务码断言通过 | code={} msg={}", actual_code, expected_msg)


def assert_jsonpath(response, expr: str, expected: Any):
    """
    jsonpath 值断言
    - expected == "not_null"：仅断言字段存在且非空
    - expected 以 "re:" 开头：正则匹配
    - 否则：相等断言
    """
    try:
        body = response.json()
    except Exception:
        _fail("响应非 JSON，无法执行 jsonpath 断言", {"expr": expr})

    matches = jsonpath.jsonpath(body, expr)
    if matches is False or not matches:
        _fail(
            f"jsonpath 无匹配：{expr}",
            {"expr": expr, "body": str(body)[:500]},
        )
    actual = matches[0]

    if expected == "not_null":
        if actual is None or actual == "":
            _fail(f"jsonpath 断言失败：期望非空，实际 {actual}", {"expr": expr, "actual": actual})
    elif isinstance(expected, str) and expected.startswith("re:"):
        if not re.search(expected[3:], str(actual)):
            _fail(
                f"jsonpath 正则断言失败：{expr} 期望匹配 {expected}，实际 {actual}",
                {"expr": expr, "expected": expected, "actual": actual},
            )
    else:
        if actual != expected:
            _fail(
                f"jsonpath 断言失败：{expr} 期望 {expected}，实际 {actual}",
                {"expr": expr, "expected": expected, "actual": actual},
            )

    log.info("jsonpath 断言通过 | {} = {}", expr, actual)


def assert_jsonschema(response, schema: dict):
    """jsonschema 契约断言"""
    try:
        body = response.json()
    except Exception:
        _fail("响应非 JSON，无法执行 jsonschema 断言", {"schema": schema})

    try:
        jsonschema.validate(instance=body, schema=schema)
        log.info("jsonschema 契约断言通过")
    except jsonschema.ValidationError as e:
        _fail(
            f"jsonschema 契约校验失败：{e.message}",
            {"schema": schema, "body": body, "path": list(e.absolute_path)},
        )


def assert_response(response, expected: dict):
    """
    数据驱动用例的统一响应断言入口
    expected 结构（字段均可选）：
        {
            "status_code": 200,
            "code": 0,
            "msg": "success",            # 支持 re: 开头正则
            "jsonpath": {                 # 多个 jsonpath 断言
                "$.data.id": 123,
                "$.data.token": "not_null"
            },
            "jsonschema": { ... }         # 契约校验
        }
    """
    with allure.step("响应断言"):
        if "status_code" in expected:
            assert_status_code(response, expected["status_code"])
        if "code" in expected:
            assert_business_code(response, expected["code"], expected.get("msg"))
        if "jsonpath" in expected:
            for expr, exp_val in expected["jsonpath"].items():
                assert_jsonpath(response, expr, exp_val)
        if "jsonschema" in expected:
            assert_jsonschema(response, expected["jsonschema"])


# =====================================================================
# 二、数据库断言
# =====================================================================
def assert_db(db_handler, sql: str, expected, args: tuple = None):
    """
    数据库断言：执行 SQL 并比对结果

    :param db_handler: DBHandler 实例
    :param sql:         查询 SQL
    :param expected:    期望结果
        - dict：query_one 返回的行字典做「子集匹配」（行中包含期望的所有键值）
        - 其它标量：与 query_one 结果的首个值比对（常用于 SELECT COUNT(*)）
    :param args:        SQL 参数化占位
    """
    with allure.step(f"数据库断言 | SQL: {sql}"):
        row = db_handler.query_one(sql, args)

        if isinstance(expected, dict):
            if not row:
                _fail("数据库断言失败：查询结果为空", {"sql": sql, "expected": expected})
            for k, v in expected.items():
                # 兼容 COUNT(*) 等聚合列名差异：按值顺序兜底匹配
                actual_v = row.get(k)
                if actual_v != v:
                    _fail(
                        f"数据库断言失败：字段 {k} 期望 {v}，实际 {actual_v}",
                        {"sql": sql, "field": k, "expected": v, "actual": actual_v, "row": row},
                    )
        else:
            # 标量期望：取结果行第一个值
            actual_val = next(iter(row.values())) if row else None
            if actual_val != expected:
                _fail(
                    f"数据库断言失败：期望 {expected}，实际 {actual_val}",
                    {"sql": sql, "expected": expected, "actual": actual_val, "row": row},
                )

        log.info("数据库断言通过 | sql={} | expected={}", sql, expected)


# =====================================================================
# 三、Redis 断言
# =====================================================================
def assert_redis(redis_handler, key: str, expected: Any = None, exists: bool = True):
    """
    Redis 断言

    :param redis_handler: RedisHandler 实例
    :param key:            键名
    :param expected:       期望值；None 表示只校验存在性
    :param exists:         True 校验存在 / False 校验不存在
    """
    with allure.step(f"Redis 断言 | key: {key}"):
        actual_exists = redis_handler.redis_exists(key)

        if exists and not actual_exists:
            _fail(f"Redis 断言失败：期望 key 存在，实际不存在 | key={key}", {"key": key})
        if not exists and actual_exists:
            _fail(f"Redis 断言失败：期望 key 不存在，实际存在 | key={key}", {"key": key})

        if expected is not None:
            actual_val = redis_handler.redis_get(key)
            if str(actual_val) != str(expected):
                _fail(
                    f"Redis 断言失败：key={key} 期望 {expected}，实际 {actual_val}",
                    {"key": key, "expected": expected, "actual": actual_val},
                )

        log.info("Redis 断言通过 | key={} | expected={}", key, expected)
