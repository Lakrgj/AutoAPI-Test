# AutoAPI-Test 接口自动化测试框架

> 一套基于 Python 的、面向 HTTP/RESTful API 的关键字驱动 + 数据驱动接口自动化测试框架，支持多环境切换、数据库 / Redis 双重断言、Token 自动刷新、HMAC-SHA256 签名、Allure 报告与 CI/CD 持续集成。

## 一、框架定位

AutoAPI-Test 定位于 **中大型项目的接口回归测试与持续集成**，目标是让测试同学只关心「用例数据 + 断言期望」，而把请求发送、会话管理、签名、Token 刷新、日志、报告、通知等通用能力下沉到框架层，做到 **用例与引擎分离、数据与代码分离、环境与配置分离**。

适用场景：

- 日常迭代的接口回归测试（冒烟 / P0 主流程 / P1 功能）
- 上线前的多环境（test / pre / prod）回归
- 接入 Jenkins / GitLab CI 实现定时构建 + 失败钉钉告警
- 接口契约校验（JSON Schema）与数据一致性校验（DB + Redis）

## 二、技术栈

| 分层       | 技术 / 库                                   | 说明                                       |
| ---------- | ------------------------------------------- | ------------------------------------------ |
| 测试引擎   | pytest                                      | 用例组织、fixture、mark、参数化            |
| 请求引擎   | requests + urllib3 Retry                    | 会话复用、超时重试、连接池                  |
| 关键字驱动 | 自研 RequestHandler                         | 统一 send_request、签名、Token 刷新、提取   |
| 数据驱动   | PyYAML + openpyxl                           | YAML / Excel 用例数据加载，支持 `${var}` 变量 |
| 断言引擎   | jsonpath + jsonschema                       | 响应断言 + 数据库断言 + Redis 断言          |
| 数据库     | pymysql（自研连接池）                        | 多环境 SQL 校验                             |
| 缓存       | redis-py                                    | Redis Key 断言、缓存校验                    |
| 日志       | loguru                                      | 控制台 + 按天滚动文件，按模块区分            |
| 报告       | allure-pytest                               | 步骤、附件、环境信息、失败截图              |
| 失败重试   | pytest-rerunfailures                        | 不稳定用例自动重跑                          |
| 并发执行   | pytest-xdist                                | 多进程并行加速                              |
| 通知       | dingtalk webhook + smtplib                  | 钉钉机器人 + 邮件构建摘要                   |
| 配置管理   | python-dotenv + config.yaml                 | 多环境配置 + 环境变量注入                   |
| 容器化     | Dockerfile + docker-compose                 | 一键拉起框架 + mysql/redis 依赖             |
| CI/CD      | Jenkinsfile                                 | 流水线编排：拉代码→装依赖→跑测→报告→通知    |

## 三、目录结构

```
AutoAPI-Test/
├── README.md                  # 项目说明（本文件）
├── requirements.txt           # Python 依赖清单
├── pytest.ini                 # pytest 与 allure 配置
├── .gitignore                 # 忽略规则
├── run.py                     # 命令行入口（选环境/标记/目录）
├── Jenkinsfile                # Jenkins 流水线定义
├── Dockerfile                 # 框架容器镜像
├── docker-compose.yml         # 框架 + mysql/redis 一键编排
├── config/                    # ===== 配置层 =====
│   ├── __init__.py
│   ├── config.yaml            # 多环境（test/pre/prod）配置
│   └── env.py                 # 环境读取与切换
├── utils/                     # ===== 工具层 =====
│   ├── __init__.py
│   ├── logger.py              # loguru 日志封装
│   ├── request_handler.py     # 关键字驱动请求引擎（核心）
│   ├── data_loader.py         # YAML/Excel 数据加载 + 变量替换
│   ├── db_handler.py          # pymysql 连接池 + redis 客户端
│   ├── assertions.py          # 响应/DB/Redis 三重断言
│   └── notify.py              # 钉钉 + 邮件通知
├── testcase/                  # ===== 用例层 =====
│   ├── conftest.py            # 全局 fixture 与钩子
│   ├── test_login.py          # 登录接口用例
│   └── test_order.py          # 下单接口用例（含 DB+Redis 断言）
└── testdata/                  # ===== 数据层 =====
    ├── login_data.yaml        # 登录用例数据
    └── order_data.yaml        # 下单用例数据
```

> 报告层（reports/）与日志层（logs/）由运行时自动生成，已加入 .gitignore。

## 四、运行方式

### 1. 安装依赖

```bash
pip install -r requirements.txt
# 生成 HTML 报告需额外安装 allure 命令行
# 下载地址：https://github.com/allure-framework/allure2/releases
```

### 2. 命令行运行（推荐，通过 run.py）

```bash
# 默认 test 环境，跑全部用例
python run.py

# 指定环境
python run.py --env pre

# 指定标记（冒烟用例）
python run.py --marker smoke

# 指定用例目录
python run.py --testdir testcase/test_login.py

# 并发执行
python run.py --env test --marker p0 --parallel 4
```

### 3. 直接使用 pytest

```bash
# 通过环境变量 ENV 切换环境
ENV=test pytest -m "smoke or p0"
ENV=pre  pytest testcase/test_order.py
```

### 4. 生成 Allure 报告

```bash
# run.py 执行结束后会自动打印以下命令
allure serve reports/allure-results          # 在线预览
allure generate reports/allure-results -o reports/html --clean   # 离线报告
```

### 5. Docker 运行

```bash
docker build -t autoapi-test .
docker run --rm -e ENV=test autoapi-test --marker smoke
```

### 6. Docker Compose 一键拉起（含 mysql/redis 依赖）

```bash
docker-compose up --abort-on-container-exit
```

## 五、核心特性

1. **多环境配置切换**：通过环境变量 `ENV` 一键切换 test/pre/prod，配置集中管理在 `config.yaml`。
2. **关键字驱动请求引擎**：`RequestHandler.send_request()` 统一入口，屏蔽 requests 细节。
3. **Token 自动刷新**：响应 401 时自动调用登录接口刷新 Token 并重试原请求，对用例透明。
4. **HMAC-SHA256 签名**：内置 `sign()`，对请求参数做签名，满足带验签接口。
5. **urllib3 Retry 重试**：基于 `HTTPAdapter + Retry` 实现连接级超时与重试，规避网络抖动。
6. **jsonpath 提取器**：响应字段提取，支持跨用例变量传递。
7. **三重断言**：响应断言（状态码/业务码/msg/jsonpath/jsonschema）+ 数据库断言 + Redis 断言。
8. **数据驱动**：YAML/Excel 用例数据，支持 `${var}` 变量替换与 `extract` 字段提取传递。
9. **失败重试**：`pytest-rerunfailures` 自动重跑不稳定用例，降低误报。
10. **Allure 富报告**：自动附加请求/响应、失败时附加堆栈，按 feature/story/step 组织。
11. **钉钉 + 邮件通知**：构建结束后推送通过/失败数、失败用例列表与报告链接。
12. **CI/CD 全链路**：Jenkinsfile + Dockerfile + docker-compose，开箱即接入流水线。
