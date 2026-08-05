# -*- coding: utf-8 -*-
"""
日志封装（基于 loguru）
===================================================================
特性：
1. 控制台彩色输出 + 文件按天滚动（rotation="00:00"），保留 30 天
2. 级别过滤（DEBUG/INFO/WARNING/ERROR）
3. 异常堆栈完整记录（backtrace=True, diagnose=True）
4. 按模块名区分（logger.bind(name=...)），日志中显示来源模块
5. enqueue=True 多进程安全（兼容 pytest-xdist 并发）
6. 提供 get_logger(name) 统一入口，全局只初始化一次
"""
import sys
from pathlib import Path

from loguru import logger

# 全局初始化标志，避免重复 add handler
_initialized = False

# 默认日志目录（项目根 / logs）
_DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# 控制台 + 文件统一格式：时间 | 级别 | 模块 | 消息
_FMT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{extra[name]}</cyan> | "
    "<level>{message}</level>"
)


def setup_logger(level: str = "INFO", log_dir: str = None):
    """
    初始化 loguru 全局配置（幂等，重复调用安全）

    :param level:   日志级别，如 DEBUG / INFO / WARNING / ERROR
    :param log_dir: 日志文件目录，默认 项目根/logs
    """
    global _initialized
    if _initialized:
        return

    log_path = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
    log_path.mkdir(parents=True, exist_ok=True)

    # 清除 loguru 默认 handler，避免重复输出
    logger.remove()

    # 1) 控制台输出：彩色 + 级别过滤
    logger.add(
        sys.stderr,
        level=level,
        format=_FMT,
        colorize=True,
        backtrace=True,   # 异常时打印完整调用栈
        diagnose=True,    # 显示变量值，便于定位
    )

    # 2) 文件输出：按天滚动，保留 30 天，多进程安全
    logger.add(
        log_path / "autoapi_{time:YYYY-MM-DD}.log",
        level=level,
        format=_FMT,
        rotation="00:00",          # 每天零点滚动
        retention="30 days",       # 保留 30 天
        compression="zip",         # 历史日志压缩
        encoding="utf-8",
        enqueue=True,              # 多进程安全（兼容 xdist）
        backtrace=True,
        diagnose=True,
    )

    # 3) 错误日志单独归档，便于快速排查
    logger.add(
        log_path / "error_{time:YYYY-MM-DD}.log",
        level="ERROR",
        format=_FMT,
        rotation="00:00",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )

    # 默认 extra，保证 format 中 {extra[name]} 不会 KeyError
    logger.configure(extra={"name": "autoapi"})

    _initialized = True
    logger.info("日志引擎初始化完成 | 级别={} | 目录={}", level, log_path)


def get_logger(name: str = "autoapi"):
    """
    获取绑定模块名的 logger

    :param name: 模块名，如 "request" / "db" / "assert"
    :return: 绑定 name 的 loguru logger 实例
    """
    setup_logger()
    return logger.bind(name=name)


# 模块级默认 logger（直接 from utils.logger import log 使用）
log = get_logger("autoapi")
