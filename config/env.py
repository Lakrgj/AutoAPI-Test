# -*- coding: utf-8 -*-
"""
配置层：环境读取与切换
===================================================================
- 读取 config/config.yaml，按环境变量 ENV=test|pre|prod 切换环境
- 支持 .env 文件注入环境变量（python-dotenv）
- 返回当前环境的配置对象 / 字典，并打印当前环境（便于排查）
- 通过单例 CONFIG 全局复用，避免重复 IO
"""
import os
from pathlib import Path

import yaml
from loguru import logger

try:
    # 若安装了 python-dotenv，则自动加载项目根 .env
    from dotenv import load_dotenv

    _ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(_ROOT / ".env")
except Exception:  # pragma: no cover - dotenv 非强依赖
    pass

# 支持的环境列表
SUPPORTED_ENV = ("test", "pre", "prod")

# 配置文件路径（与本文件同目录的 config.yaml）
CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


def _load_yaml(path) -> dict:
    """读取 YAML 配置文件"""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class EnvConfig:
    """环境配置对象，封装当前环境的配置字典"""

    def __init__(self, env: str = None, config_path: str = None):
        # 环境优先级：构造参数 > 环境变量 ENV > 默认 test
        self.env = (env or os.getenv("ENV", "test")).lower()
        if self.env not in SUPPORTED_ENV:
            raise ValueError(
                f"不支持的环境: {self.env}，仅支持 {SUPPORTED_ENV}，"
                f"请通过环境变量 ENV 或参数指定。"
            )

        all_cfg = _load_yaml(config_path or CONFIG_PATH)
        if self.env not in all_cfg:
            raise KeyError(f"配置文件中未找到环境 [{self.env}] 的配置段")

        # 当前环境配置
        self._env_cfg: dict = all_cfg[self.env]
        # 全局共享配置（如通知配置）
        self._notify: dict = all_cfg.get("notify", {})
        # 全量配置（备用）
        self._all_cfg: dict = all_cfg

        # 打印当前环境，便于日志排查
        logger.info(
            "========== 加载环境配置 ==========\n"
            f"当前环境 ENV     : {self.env}\n"
            f"base_url         : {self._env_cfg.get('base_url')}\n"
            f"timeout / retry  : {self._env_cfg.get('timeout')}s / "
            f"{self._env_cfg.get('retry')}\n"
            f"db host          : {self._env_cfg.get('db', {}).get('host')}\n"
            f"redis host       : {self._env_cfg.get('redis', {}).get('host')}\n"
            "=================================="
        )

    # ---------- 访问接口 ----------
    @property
    def config(self) -> dict:
        """返回当前环境的完整配置字典"""
        return self._env_cfg

    def get(self, key: str, default=None):
        """按 key 取当前环境配置，支持默认值"""
        return self._env_cfg.get(key, default)

    def get_notify(self) -> dict:
        """返回全局通知配置（钉钉 / 邮件）"""
        return self._notify

    def __getitem__(self, key: str):
        """支持下标访问：CONFIG['base_url']"""
        return self._env_cfg[key]

    def __contains__(self, key: str) -> bool:
        return key in self._env_cfg

    def __repr__(self) -> str:
        return f"<EnvConfig env={self.env!r} base_url={self._env_cfg.get('base_url')!r}>"


def load_config(env: str = None) -> EnvConfig:
    """加载指定环境的配置（每次返回新实例）"""
    return EnvConfig(env=env)


# 模块级单例：import 后即可直接使用 CONFIG
CONFIG = EnvConfig()
