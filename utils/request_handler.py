# -*- coding: utf-8 -*-
"""
关键字驱动请求引擎（request_handler）—— 框架核心
===================================================================
基于 requests.Session 封装，提供统一 send_request() 入口，特性：
1. 会话复用：单例 Session 复用 TCP 连接、Cookie，提升性能
2. 统一请求方法：send_request(method, url, **kwargs) 屏蔽 requests 细节
3. 超时 + 重试：基于 HTTPAdapter + urllib3 Retry，连接级重试规避网络抖动
4. HMAC-SHA256 签名：sign() 对参数加签，满足带验签接口
5. Token 自动刷新：响应 401 时自动调登录接口刷新 Token 并重试原请求（对用例透明）
6. 请求/响应日志：结构化打印，关键字段一目了然
7. Allure 附件：请求/响应自动 attach 到报告
8. jsonpath 提取器：extract() 提取响应字段，支持跨用例变量传递
9. 响应断言入口：assert_response() 委托给 assertions 模块
"""
import json
import time
import hmac
import hashlib
import base64
from typing import Any

import requests
import allure
import jsonpath
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from utils.logger import get_logger

log = get_logger("request")


class RequestHandler:
    """关键字驱动请求引擎"""

    def __init__(
        self,
        base_url: str = "",
        timeout: int = 10,
        retry: int = 3,
        sign_secret: str = "",
        sign_app_id: str = "",
        login_info: dict = None,
    ):
        """
        :param base_url:    基础地址，如 http://test-api.example.com
        :param timeout:     单接口超时（秒）
        :param retry:       urllib3 连接级重试次数
        :param sign_secret: HMAC-SHA256 签名密钥
        :param sign_app_id: 签名 AppID
        :param login_info:  登录信息 dict，用于 Token 自动刷新
                            {"url": "/api/login", "username": "...", "password": "..."}
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.sign_secret = sign_secret
        self.sign_app_id = sign_app_id
        self.login_info = login_info or {}
        self.token = ""  # 当前会话 Token

        # 复用 Session：TCP 连接复用 + Cookie 自动管理
        self.session = requests.Session()
        self._mount_retry(retry)

        # 标记是否正在刷新 Token，避免刷新接口 401 时无限递归
        self._refreshing = False

        log.info(
            "RequestHandler 初始化 | base_url={} | timeout={}s | retry={}",
            self.base_url, self.timeout, retry,
        )

    # ------------------------------------------------------------------
    # 1. 超时与重试（urllib3 Retry）
    # ------------------------------------------------------------------
    def _mount_retry(self, retry: int):
        """
        挂载带重试策略的 HTTPAdapter
        - total:                 总重试次数
        - backoff_factor:        指数退避 0.5s, 1s, 2s...
        - status_forcelist:      这些状态码触发重试（5xx / 429）
        - allowed_methods:       仅对幂等方法重试（GET/PUT/DELETE 等）
        - raise_on_status:       False，重试耗尽不抛异常，交由上层断言
        """
        retry_strategy = Retry(
            total=retry,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "PUT", "DELETE", "HEAD", "OPTIONS"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,  # 连接池大小
            pool_maxsize=10,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    # ------------------------------------------------------------------
    # 2. HMAC-SHA256 签名
    # ------------------------------------------------------------------
    def sign(self, payload: str, secret: str = None) -> str:
        """
        HMAC-SHA256 签名（演示用）

        :param payload: 待签名内容（通常为 app_id+timestamp+参数排序拼接串）
        :param secret:  签名密钥，默认用构造传入的 sign_secret
        :return: base64 编码的签名字符串
        """
        secret = secret or self.sign_secret
        if not secret:
            return ""
        # hmac.new(key, msg, digestmod) → 二进制摘要 → base64
        digest = hmac.new(
            key=secret.encode("utf-8"),
            msg=payload.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _add_sign(self, kwargs: dict):
        """
        为请求注入签名头（X-App-Id / X-Timestamp / X-Sign）
        签名串 = sign(app_id + timestamp + 排序后的 query/body)
        """
        if not self.sign_secret:
            return
        timestamp = str(int(time.time()))
        # 收集参与签名的参数：query params 优先，其次 json body
        params = kwargs.get("params") or {}
        if isinstance(params, dict):
            sign_str = "&".join(f"{k}={params[k]}" for k in sorted(params))
        else:
            sign_str = str(params)
        payload = f"{self.sign_app_id}{timestamp}{sign_str}"
        signature = self.sign(payload)

        headers = kwargs.setdefault("headers", {})
        headers["X-App-Id"] = self.sign_app_id
        headers["X-Timestamp"] = timestamp
        headers["X-Sign"] = signature

    # ------------------------------------------------------------------
    # 3. Token 自动刷新
    # ------------------------------------------------------------------
    def set_token(self, token: str):
        """设置会话 Token"""
        self.token = token
        log.debug("Token 已更新: {}...", token[:16] if token else "(空)")

    def refresh_token(self) -> str:
        """
        Token 自动刷新：调用登录接口获取新 Token
        - 响应 401 时由 send_request 自动触发，对用例透明
        - 防重入：_refreshing 标记避免登录接口自身 401 递归
        """
        if self._refreshing:
            log.warning("Token 刷新中，跳过重复刷新")
            return self.token

        if not self.login_info:
            log.error("未配置 login_info，无法自动刷新 Token")
            return self.token

        self._refreshing = True
        try:
            login_url = self.login_info.get("url", "/api/v1/auth/login")
            log.info("Token 过期，自动刷新登录 | url={}", login_url)
            resp = self.session.request(
                method="POST",
                url=self._build_url(login_url),
                json={
                    "username": self.login_info.get("username"),
                    "password": self.login_info.get("password"),
                },
                timeout=self.timeout,
            )
            # 兼容两种返回结构：$.data.token 或 $.token
            token = self.extract(resp, "$.data.token") or self.extract(resp, "$.token")
            if token:
                self.set_token(token)
                log.info("Token 刷新成功")
            else:
                log.error("Token 刷新失败，响应中未取到 token: {}", resp.text[:200])
            return self.token
        finally:
            self._refreshing = False

    def _build_url(self, url: str) -> str:
        """拼接完整 URL：相对路径自动补 base_url"""
        if url.startswith(("http://", "https://")):
            return url
        return f"{self.base_url}{url if url.startswith('/') else '/' + url}"

    # ------------------------------------------------------------------
    # 4. 统一请求方法（核心入口）
    # ------------------------------------------------------------------
    def send_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """
        统一请求入口

        :param method: HTTP 方法 GET/POST/PUT/DELETE...
        :param url:    接口路径，相对路径自动补 base_url
        :param kwargs: 透传 requests 参数（params/json/data/headers/files...）
        :return: requests.Response
        """
        method = method.upper()
        full_url = self._build_url(url)

        # 注入签名
        self._add_sign(kwargs)

        # 注入 Token（登录接口本身不需要）
        headers = kwargs.setdefault("headers", {})
        if self.token and not self._is_auth_url(url):
            headers.setdefault("Authorization", f"Bearer {self.token}")

        # 统一超时
        kwargs.setdefault("timeout", self.timeout)

        # 请求日志 + Allure 附件
        self._log_request(method, full_url, kwargs)
        self._attach_request(method, full_url, kwargs)

        # 发送请求
        resp = self.session.request(method=method, url=full_url, **kwargs)

        # 响应日志 + Allure 附件
        self._log_response(resp)
        self._attach_response(resp)

        # ===== Token 自动刷新：401 时刷新并重试一次 =====
        if resp.status_code == 401 and not self._refreshing and self.login_info:
            log.warning("收到 401 未授权，触发 Token 自动刷新并重试 | url={}", full_url)
            new_token = self.refresh_token()
            if new_token:
                # 更新鉴权头后重试原请求
                headers["Authorization"] = f"Bearer {new_token}"
                self._attach_request(method, full_url + " [重试]", kwargs)
                resp = self.session.request(method=method, url=full_url, **kwargs)
                self._log_response(resp)
                self._attach_response(resp)

        return resp

    def _is_auth_url(self, url: str) -> bool:
        """判断是否为登录/鉴权接口（避免给登录接口带旧 Token）"""
        return any(kw in url for kw in ("login", "auth", "token"))

    # ------------------------------------------------------------------
    # 5. jsonpath 提取器
    # ------------------------------------------------------------------
    def extract(self, response: requests.Response, expr: str):
        """
        jsonpath 提取响应字段

        :param response: requests.Response
        :param expr:     jsonpath 表达式，如 $.data.token
        :return: 提取到的值（取首个）；无匹配返回 None
        """
        try:
            body = response.json()
        except Exception:
            log.error("响应非 JSON，无法提取 | expr={}", expr)
            return None

        matches = jsonpath.jsonpath(body, expr)
        if matches is False or not matches:
            log.warning("jsonpath 无匹配 | expr={} | body={}", expr, str(body)[:200])
            return None
        # jsonpath 返回列表，取第一个
        return matches[0]

    def extract_to(self, response: requests.Response, mapping: dict) -> dict:
        """
        批量提取并返回字典，便于跨用例变量传递

        :param mapping: {"变量名": "$.jsonpath.expr", ...}
        :return: {"变量名": value, ...}
        """
        result = {}
        for var_name, expr in mapping.items():
            result[var_name] = self.extract(response, expr)
        return result

    # ------------------------------------------------------------------
    # 6. 响应断言入口（委托给 assertions 模块）
    # ------------------------------------------------------------------
    def assert_response(self, response: requests.Response, expected: dict):
        """
        响应断言入口，委托给 utils.assertions.assert_response
        expected 结构示例：
            {
                "status_code": 200,
                "code": 0,
                "msg": "success",
                "jsonpath": {"$.data.id": 123}
            }
        """
        # 延迟导入，避免与 assertions 互相 import 时序问题
        from utils.assertions import assert_response as _assert
        _assert(response, expected)

    # ------------------------------------------------------------------
    # 7. 日志与 Allure 附件
    # ------------------------------------------------------------------
    def _log_request(self, method: str, url: str, kwargs: dict):
        log.info(
            ">>> 请求 | {} {} | params={} | json={} | data={}",
            method, url,
            kwargs.get("params"),
            kwargs.get("json"),
            kwargs.get("data"),
        )

    def _log_response(self, resp: requests.Response):
        # 截断超长响应，避免日志爆炸
        text = resp.text if len(resp.text) <= 1000 else resp.text[:1000] + "...(截断)"
        log.info(
            "<<< 响应 | {} | status={} | 耗时={:.0f}ms | body={}",
            resp.url, resp.status_code,
            resp.elapsed.total_seconds() * 1000,
            text,
        )

    def _attach_request(self, method: str, url: str, kwargs: dict):
        """把请求信息附加到 Allure 报告"""
        try:
            content = {
                "method": method,
                "url": url,
                "headers": kwargs.get("headers"),
                "params": kwargs.get("params"),
                "json": kwargs.get("json"),
                "data": kwargs.get("data"),
            }
            allure.attach(
                json.dumps(content, ensure_ascii=False, indent=2, default=str),
                name="请求信息",
                attachment_type=allure.attachment_type.JSON,
            )
        except Exception as e:  # pragma: no cover - allure 在非 pytest 环境下不可用
            log.debug("attach 请求信息失败: {}", e)

    def _attach_response(self, resp: requests.Response):
        """把响应信息附加到 Allure 报告"""
        try:
            # 优先以 JSON 格式附加，便于报告渲染
            try:
                body = resp.json()
                attachment_type = allure.attachment_type.JSON
                content = json.dumps(
                    {"status_code": resp.status_code, "body": body},
                    ensure_ascii=False, indent=2, default=str,
                )
            except Exception:
                body = resp.text
                attachment_type = allure.attachment_type.TEXT
                content = f"status: {resp.status_code}\nbody: {body}"

            allure.attach(content, name="响应信息", attachment_type=attachment_type)
        except Exception as e:  # pragma: no cover
            log.debug("attach 响应信息失败: {}", e)

    # ------------------------------------------------------------------
    # 8. 资源释放
    # ------------------------------------------------------------------
    def close(self):
        """关闭会话，释放连接池"""
        self.session.close()
        log.info("RequestHandler 会话已关闭")
