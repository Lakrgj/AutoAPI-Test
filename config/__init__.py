# -*- coding: utf-8 -*-
"""
配置层包（config）
- 暴露全局配置单例 CONFIG，供其它层直接 import
"""
from config.env import CONFIG, EnvConfig, load_config  # noqa: F401

__all__ = ["CONFIG", "EnvConfig", "load_config"]
