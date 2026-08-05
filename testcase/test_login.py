# -*- coding: utf-8 -*-
"""
登录接口用例（test_login）
===================================================================
- 数据驱动：用例数据来自 testdata/login_data.yaml
- 变量替换：${username}/${password} 由当前环境配置注入
- 自动打标：根据用例数据 marks 字段动态打 smoke/p0/p1 等 marker
- 覆盖场景：正向登录 / 密码错误 / 用户名为空 / 账号锁定
- 断言：状态码 + 业务码 + msg + jsonpath，统一走 assert_response
"""
import os

import pytest
import allure

from config.env import CONFIG
from utils.data_loader import load_yaml, replace_variables, build_param_ids
from utils.assertions import assert_response

# 用例数据路径
_DATA_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "testdata", "login_data.yaml",
)


def _load_cases():
    """加载用例并做变量替换（${username}/${password} ← 环境配置）"""
    cases = load_yaml(_DATA_FILE)
    return replace_variables(cases, CONFIG.get("login", {}))


def _params_with_marks(cases):
    """把每条用例包装成 pytest.param，并按 marks 字段动态打 marker"""
    params = []
    for case in cases:
        marks = [getattr(pytest.mark, m) for m in case.get("marks", [])]
        params.append(pytest.param(case, marks=marks, id=case.get("case_name")))
    return params


_LOGIN_CASES = _load_cases()


@pytest.mark.regression
@pytest.mark.parametrize(
    "case",
    _params_with_marks(_LOGIN_CASES),
)
def test_login(case, request_handler):
    """
    登录接口参数化用例
    - 通过 request_handler.send_request 发送请求
    - 通过 assert_response 统一断言（状态码/业务码/msg/jsonpath）
    - 正向用例提取 token 并注入 request_handler，供后续业务用例复用
    """
    with allure.step(case["case_name"]):
        req = case["request"]
        # 发送请求（json/params/data/headers 均透传，缺省为 None）
        resp = request_handler.send_request(
            method=req["method"],
            url=req["url"],
            json=req.get("json"),
            params=req.get("params"),
            data=req.get("data"),
            headers=req.get("headers"),
        )

        # 统一响应断言
        assert_response(resp, case.get("assert", {}))

        # 提取字段（正向用例提取 token 并注入会话，供跨用例复用）
        extract = case.get("extract")
        if extract:
            for var_name, expr in extract.items():
                value = request_handler.extract(resp, expr)
                if var_name == "token" and value:
                    request_handler.set_token(value)
                    allure.attach(str(value), name="提取的Token", attachment_type=allure.attachment_type.TEXT)
