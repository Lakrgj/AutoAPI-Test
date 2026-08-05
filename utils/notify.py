# -*- coding: utf-8 -*-
"""
通知模块（notify）—— 钉钉机器人 + 邮件
===================================================================
1. DingTalkNotifier：钉钉自定义机器人 webhook 通知
   - 安全设置「加签」：HMAC-SHA256 签名 timestamp，拼接到 webhook URL
   - send_text / send_markdown，支持 @ 指定手机号 / @ 全员
2. EmailNotifier：基于 smtplib 的邮件通知（支持 SSL）
3. send_build_summary：构建结果摘要通知入口
   - 汇总通过/失败数、报告链接、失败用例列表
   - 钉钉 markdown + 邮件双通道推送
"""
import time
import hmac
import hashlib
import base64
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List

import requests

from utils.logger import get_logger

log = get_logger("notify")


# =====================================================================
# 钉钉机器人通知
# =====================================================================
class DingTalkNotifier:
    """钉钉自定义机器人通知（加签模式）"""

    def __init__(self, webhook: str, secret: str = "", at_mobiles: list = None, is_at_all: bool = False):
        """
        :param webhook:    钉钉机器人 webhook 地址
        :param secret:     加签密钥（机器人安全设置：加签）
        :param at_mobiles: @ 指定手机号列表
        :param is_at_all:  是否 @ 全员
        """
        self.webhook = webhook
        self.secret = secret
        self.at_mobiles = at_mobiles or []
        self.is_at_all = is_at_all

    def _build_signed_url(self) -> str:
        """
        生成加签后的 webhook URL
        签名算法：HMAC-SHA256(secret, timestamp + "\n" + secret) → base64 → urlencode
        """
        if not self.secret:
            return self.webhook

        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            self.secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        separator = "&" if "?" in self.webhook else "?"
        return f"{self.webhook}{separator}timestamp={timestamp}&sign={sign}"

    def send_markdown(self, title: str, text: str) -> bool:
        """
        发送 markdown 消息

        :param title: 消息标题（通知列表展示）
        :param text:  markdown 正文
        :return: 是否成功（钉钉 errcode == 0）
        """
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": text},
            "at": {
                "atMobiles": self.at_mobiles,
                "isAtAll": self.is_at_all,
            },
        }
        return self._post(payload)

    def send_text(self, content: str) -> bool:
        """发送纯文本消息"""
        payload = {
            "msgtype": "text",
            "text": {"content": content},
            "at": {
                "atMobiles": self.at_mobiles,
                "isAtAll": self.is_at_all,
            },
        }
        return self._post(payload)

    def _post(self, payload: dict) -> bool:
        url = self._build_signed_url()
        try:
            resp = requests.post(url, json=payload, timeout=10)
            result = resp.json()
            if result.get("errcode") == 0:
                log.info("钉钉通知发送成功 | type={}", payload.get("msgtype"))
                return True
            log.error("钉钉通知发送失败 | resp={}", result)
            return False
        except Exception as e:
            log.exception("钉钉通知发送异常: {}", e)
            return False


# =====================================================================
# 邮件通知
# =====================================================================
class EmailNotifier:
    """基于 smtplib 的邮件通知"""

    def __init__(self, smtp_host: str, smtp_port: int, sender: str,
                 sender_password: str, receivers: List[str], use_ssl: bool = True):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.sender = sender
        self.sender_password = sender_password
        self.receivers = receivers
        self.use_ssl = use_ssl

    def send(self, subject: str, content: str, content_type: str = "html"):
        """
        发送邮件

        :param subject:     主题
        :param content:     正文（html / plain）
        :param content_type: text/html
        """
        import smtplib

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = ",".join(self.receivers)
        msg.attach(MIMEText(content, content_type, "utf-8"))

        try:
            if self.use_ssl:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=15)
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15)
                server.starttls()
            server.login(self.sender, self.sender_password)
            server.sendmail(self.sender, self.receivers, msg.as_string())
            server.quit()
            log.info("邮件通知发送成功 | 收件人={}", self.receivers)
            return True
        except Exception as e:
            log.exception("邮件通知发送异常: {}", e)
            return False


# =====================================================================
# 构建结果摘要通知入口
# =====================================================================
def send_build_summary(notify_config: dict, total: int, passed: int, failed: int,
                       report_url: str, failed_cases: List[str] = None, env: str = ""):
    """
    构建结束后的结果摘要通知（钉钉 + 邮件双通道）

    :param notify_config: 来自 config.yaml 的 notify 配置
    :param total:         用例总数
    :param passed:        通过数
    :param failed:        失败数
    :param report_url:    Allure 报告链接
    :param failed_cases:  失败用例名列表
    :param env:           运行环境
    """
    failed_cases = failed_cases or []
    status = "✅ 全部通过" if failed == 0 else f"❌ 失败 {failed} 个"
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    # ----- 钉钉 markdown -----
    md_lines = [
        f"### 接口自动化测试报告 {status}",
        f"> **环境**：{env}",
        f"> **时间**：{now}",
        f"> **总数**：{total}  **通过**：{passed}  **失败**：{failed}",
        f"> **通过率**：{round(passed / total * 100, 2) if total else 0}%",
        f"> **报告**：[点击查看]({report_url})",
    ]
    if failed_cases:
        md_lines.append("\n**失败用例：**")
        for idx, name in enumerate(failed_cases[:20], start=1):  # 最多列 20 条
            md_lines.append(f"{idx}. {name}")
        if len(failed_cases) > 20:
            md_lines.append(f"...共 {len(failed_cases)} 条，详见报告")

    dingtalk_cfg = notify_config.get("dingtalk", {})
    if dingtalk_cfg.get("webhook"):
        notifier = DingTalkNotifier(
            webhook=dingtalk_cfg["webhook"],
            secret=dingtalk_cfg.get("secret", ""),
            at_mobiles=dingtalk_cfg.get("at_mobiles", []),
            is_at_all=dingtalk_cfg.get("is_at_all", False) and failed > 0,
        )
        notifier.send_markdown("接口自动化测试报告", "\n\n".join(md_lines))
    else:
        log.warning("未配置钉钉 webhook，跳过钉钉通知")

    # ----- 邮件 -----
    email_cfg = notify_config.get("email", {})
    if email_cfg.get("smtp_host") and email_cfg.get("receivers"):
        email_notifier = EmailNotifier(
            smtp_host=email_cfg["smtp_host"],
            smtp_port=int(email_cfg.get("smtp_port", 465)),
            sender=email_cfg.get("sender", ""),
            sender_password=email_cfg.get("sender_password", ""),
            receivers=email_cfg["receivers"],
            use_ssl=email_cfg.get("use_ssl", True),
        )
        html = "<br>".join(line.replace("\n", "<br>") for line in md_lines)
        email_notifier.send(f"[接口自动化] {status} | {env}", html, content_type="html")
    else:
        log.warning("未配置邮件 SMTP，跳过邮件通知")
