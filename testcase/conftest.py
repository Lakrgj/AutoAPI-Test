# -*- coding: utf-8 -*-
"""
pytest 全局 fixture 与钩子（conftest）
===================================================================
- env_config     (session)：环境配置单例
- request_handler(session)：关键字驱动请求引擎，全 session 复用
- login_token    (session)：登录并共享 Token（依赖 request_handler）
- db_handler     (session)：MySQL 客户端（懒连接）
- redis_handler  (session)：Redis 客户端
- 钩子：
  * pytest_sessionstart       —— 写入 Allure 环境信息
  * pytest_runtest_makereport —— 用例失败时附加失败快照到 Allure
  * pytest_collection_modifyitems —— 用例名中文化 + 自动打 marker
===================================================================
"""
import os
import sys
import pytest
import allure

# 把项目根目录加入 sys.path，确保任意方式启动都能 import utils / config
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config.env import CONFIG  # noqa: E402
from utils.logger import get_logger  # noqa: E402
from utils.request_handler import RequestHandler  # noqa: E402
from utils.db_handler import DBHandler, RedisHandler  # noqa: E402

log = get_logger("conftest")


# =====================================================================
# 一、环境配置 fixture
# =====================================================================
@pytest.fixture(scope="session")
def env_config():
    """返回当前环境配置对象（EnvConfig）"""
    return CONFIG


# =====================================================================
# 二、请求引擎 fixture（session 级复用）
# =====================================================================
@pytest.fixture(scope="session")
def request_handler(env_config):
    """
    构建关键字驱动请求引擎
    - 全 session 复用同一 Session（TCP 连接复用 + Cookie）
    - 注入签名密钥、登录信息（用于 401 自动刷新 Token）
    """
    cfg = env_config
    handler = RequestHandler(
        base_url=cfg["base_url"],
        timeout=cfg.get("timeout", 10),
        retry=cfg.get("retry", 3),
        sign_secret=cfg.get("sign", {}).get("secret", ""),
        sign_app_id=cfg.get("sign", {}).get("app_id", ""),
        login_info=cfg.get("login"),
    )
    yield handler
    handler.close()


# =====================================================================
# 三、登录 Token 共享 fixture（session 级，依赖登录接口）
# =====================================================================
@pytest.fixture(scope="session")
def login_token(request_handler, env_config):
    """
    session 级登录：调用登录接口获取 Token 并注入 request_handler
    - 后续所有业务接口自动携带该 Token
    - 若 Token 过期，request_handler 会在 401 时自动刷新
    """
    login = env_config.get("login", {})
    if not login:
        log.warning("未配置登录信息，login_token 为空")
        yield ""
        return

    with allure.step("会话级登录获取 Token"):
        resp = request_handler.send_request(
            method="POST",
            url=login["url"],
            json={
                "username": login.get("username"),
                "password": login.get("password"),
            },
        )
        # 兼容两种返回结构
        token = (
            request_handler.extract(resp, "$.data.token")
            or request_handler.extract(resp, "$.token")
        )
        if token:
            request_handler.set_token(token)
            log.info("会话级登录成功，Token 已注入")
        else:
            log.error("会话级登录未取到 Token，后续业务接口可能 401")
    yield token


# =====================================================================
# 四、数据库 / Redis 客户端 fixture
# =====================================================================
@pytest.fixture(scope="session")
def db_handler(env_config):
    """MySQL 客户端（懒连接，无 DB 时初始化不报错）"""
    if "db" not in env_config:
        pytest.skip("当前环境未配置数据库，跳过 DB 相关用例")
    handler = DBHandler(env_config["db"])
    yield handler
    handler.close()


@pytest.fixture(scope="session")
def redis_handler(env_config):
    """Redis 客户端"""
    if "redis" not in env_config:
        pytest.skip("当前环境未配置 Redis，跳过 Redis 相关用例")
    handler = RedisHandler(env_config["redis"])
    yield handler
    handler.close()


# =====================================================================
# 五、用例前后置钩子
# =====================================================================
@pytest.fixture(autouse=True)
def _case_context(request):
    """
    每条用例的前后置：
    - 前置：记录用例名、打 Allure feature 标签
    - 后置：用例耗时统计
    """
    # 从模块名提取 feature（如 test_login -> 登录）
    module = request.module.__name__ if request.module else "unknown"
    feature_map = {
        "test_login": "登录模块",
        "test_order": "订单模块",
    }
    feature = feature_map.get(module, module)
    allure.dynamic.feature(feature)
    allure.dynamic.story(request.node.name)

    log.info("---- 开始用例: {} ----", request.node.name)
    start = os.times().elapsed
    yield
    elapsed = (os.times().elapsed - start) * 1000
    log.info("---- 结束用例: {} | 耗时 {:.0f}ms ----", request.node.name, elapsed)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    """
    用例执行结果钩子：
    - 失败时把异常信息附加到 Allure（失败快照）
    - 把 report 挂到 item 上，供 fixture 的后置使用
    """
    outcome = yield
    report = outcome.get_result()

    if report.when == "call" and report.failed:
        # 附加失败信息到 Allure
        try:
            failure_info = f"用例: {item.name}\n阶段: {report.when}\n"
            if report.longrepr:
                failure_info += f"失败原因:\n{report.longreprtext}"
            allure.attach(
                failure_info,
                name="失败原因",
                attachment_type=allure.attachment_type.TEXT,
            )
        except Exception as e:  # pragma: no cover
            log.debug("附加失败信息失败: {}", e)

    # 把 report 存到 item 上，方便 fixture 后置判断 setup 是否失败
    setattr(item, f"rep_{report.when}", report)


def pytest_sessionstart(session):
    """会话开始：写入 Allure 环境信息"""
    try:
        allure_results = os.path.join(_PROJECT_ROOT, "reports", "allure-results")
        os.makedirs(allure_results, exist_ok=True)
        env_info = (
            f"Environment={CONFIG.env}\n"
            f"BaseUrl={CONFIG['base_url']}\n"
            f"Timeout={CONFIG.get('timeout')}s\n"
            f"Retry={CONFIG.get('retry')}\n"
            f"Python={sys.version.split()[0]}\n"
        )
        with open(os.path.join(allure_results, "environment.properties"), "w", encoding="utf-8") as f:
            f.write(env_info)
        log.info("Allure 环境信息已写入")
    except Exception as e:  # pragma: no cover
        log.debug("写入 Allure 环境信息失败: {}", e)


def pytest_collection_modifyitems(items):
    """
    用例收集后处理：
    - 给用例名加中文 nodeid（便于报告阅读）
    - 自动根据用例名中的标记关键字补打 marker
    """
    for item in items:
        # 中文用例名兼容：替换非法字符
        item._nodeid = item.name
