# -*- coding: utf-8 -*-
"""
工具层包（utils）
===================================================================
封装框架通用能力：日志、请求引擎、数据加载、数据库/缓存、断言、通知
各模块之间保持松耦合，统一从本包导出便捷入口。
"""
__version__ = "1.0.0"
__author__ = "AutoAPI-Test"

# 便捷导出（按需 import，避免循环依赖与无谓的初始化开销）
__all__ = [
    "logger",
    "request_handler",
    "data_loader",
    "db_handler",
    "assertions",
    "notify",
]
