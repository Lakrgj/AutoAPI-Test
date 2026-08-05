# -*- coding: utf-8 -*-
"""
订单接口用例（test_order）
===================================================================
- 依赖登录：通过 login_token fixture 共享会话 Token
- 主流程：创建订单 → 查询订单 → 取消订单（单条用例内顺序执行，保证上下文）
- 数据库断言：下单后校验 t_order 落库状态；取消后校验状态变更
- Redis 断言：下单后校验库存缓存 key 存在
- 异常场景：库存不足 / 商品下架，数据驱动参数化
===================================================================
关键技术点：数据库 + Redis 双重断言，保证接口与数据一致性
"""
import os

import pytest
import allure

from config.env import CONFIG
from utils.data_loader import load_yaml, replace_variables
from utils.assertions import assert_response, assert_db, assert_redis

# 用例数据路径
_DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "testdata", "order_data.yaml",
)


def _load_cases():
    """加载订单用例数据并做变量替换"""
    cases = load_yaml(_DATA_FILE)
    return replace_variables(cases, CONFIG.get("login", {}))


_ALL_CASES = _load_cases()
# 正向下单用例（用于主流程）
_CREATE_CASE = next(
    (c for c in _ALL_CASES if c.get("scene") == "create" and "p0" in c.get("marks", [])),
    None,
)
# 异常下单用例（参数化）
_NEGATIVE_CASES = [c for c in _ALL_CASES if c.get("scene") == "negative"]


def _params_with_marks(cases):
    """包装为 pytest.param 并按 marks 动态打 marker"""
    return [
        pytest.param(c, marks=[getattr(pytest.mark, m) for m in c.get("marks", [])],
                     id=c.get("case_name"))
        for c in cases
    ]


@pytest.mark.usefixtures("login_token")
class TestOrderFlow:
    """订单主流程：创建 → 查询 → 取消（含 DB + Redis 双重断言）"""

    @pytest.mark.smoke
    @pytest.mark.p0
    @pytest.mark.db_check
    @pytest.mark.redis_check
    @allure.feature("订单模块")
    @allure.story("下单主流程")
    def test_order_full_flow(self, request_handler, db_handler, redis_handler):
        """
        完整下单流程：
        1. 创建订单 → 断言响应 + 提取 order_no
        2. 数据库断言：订单已落库且状态为 CREATED
        3. Redis 断言：库存缓存 key 存在
        4. 查询订单 → 断言响应
        5. 取消订单 → 断言响应 + 数据库断言状态变更为 CANCELED
        """
        assert _CREATE_CASE is not None, "order_data.yaml 缺少 scene=create 的 P0 用例"

        # ---------- 1. 创建订单 ----------
        with allure.step("创建订单"):
            req = _CREATE_CASE["request"]
            resp = request_handler.send_request(
                method=req["method"], url=req["url"], json=req.get("json"),
            )
            assert_response(resp, _CREATE_CASE["assert"])
            order_no = request_handler.extract(resp, "$.data.order_no")
            assert order_no, "创建订单未返回 order_no"
            allure.attach(str(order_no), name="订单号",
                          attachment_type=allure.attachment_type.TEXT)

        # ---------- 2. 数据库断言：订单落库 ----------
        with allure.step("数据库断言 - 订单已落库"):
            db_cfg = _CREATE_CASE.get("db_assert", {})
            if db_cfg:
                # SQL 参数化：args 传入 order_no，防 SQL 注入
                assert_db(
                    db_handler,
                    sql=db_cfg["sql"],
                    expected=db_cfg["expected"],
                    args=(order_no,),
                )

        # ---------- 3. Redis 断言：库存缓存 ----------
        with allure.step("Redis 断言 - 库存缓存"):
            r_cfg = _CREATE_CASE.get("redis_assert", {})
            if r_cfg:
                assert_redis(
                    redis_handler,
                    key=r_cfg["key"],
                    expected=r_cfg.get("value"),
                    exists=r_cfg.get("exists", True),
                )

        # ---------- 4. 查询订单 ----------
        with allure.step("查询订单详情"):
            resp = request_handler.send_request(
                method="GET", url=f"/api/v1/order/{order_no}",
            )
            assert_response(resp, {
                "status_code": 200,
                "code": 0,
                "jsonpath": {"$.data.order_no": order_no},
            })

        # ---------- 5. 取消订单 ----------
        with allure.step("取消订单"):
            resp = request_handler.send_request(
                method="POST", url="/api/v1/order/cancel",
                json={"order_no": order_no},
            )
            assert_response(resp, {"status_code": 200, "code": 0})

            # 数据库断言：订单状态变更为 CANCELED
            with allure.step("数据库断言 - 订单状态已取消"):
                assert_db(
                    db_handler,
                    sql="SELECT status FROM t_order WHERE order_no = %s",
                    expected={"status": "CANCELED"},
                    args=(order_no,),
                )


@pytest.mark.regression
@pytest.mark.parametrize("case", _params_with_marks(_NEGATIVE_CASES))
def test_create_order_negative(case, request_handler, login_token):
    """异常下单场景（库存不足 / 商品下架等），数据驱动"""
    with allure.step(case["case_name"]):
        req = case["request"]
        resp = request_handler.send_request(
            method=req["method"], url=req["url"], json=req.get("json"),
        )
        assert_response(resp, case.get("assert", {}))
