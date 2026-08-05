# -*- coding: utf-8 -*-
"""
命令行入口（run.py）
===================================================================
- argparse 解析参数：环境(--env) / 标记(--marker) / 用例目录(--testdir) / 并发(--parallel)
- 设置环境变量 ENV，使 config.env 在 pytest 加载时读取到正确环境
- 组装 pytest 命令行参数并调用 pytest.main()
- 结束后打印 Allure 报告生成命令
===================================================================
用法示例：
    python run.py                              # 默认 test 环境跑全部
    python run.py --env pre --marker smoke     # pre 环境跑冒烟
    python run.py --testdir testcase/test_login.py
    python run.py --parallel 4                 # 4 进程并发
"""
import os
import sys
import argparse
from pathlib import Path

# 项目根目录加入 sys.path（兼容直接 python run.py 启动）
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402

# 运行时产物目录
REPORT_DIR = PROJECT_ROOT / "reports"
ALLURE_RESULTS = REPORT_DIR / "allure-results"
LOG_DIR = PROJECT_ROOT / "logs"


def parse_args():
    parser = argparse.ArgumentParser(
        description="AutoAPI-Test 接口自动化测试框架命令行入口",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--env", default=os.getenv("ENV", "test"),
        choices=["test", "pre", "prod"],
        help="运行环境：test / pre / prod（默认 test，也可用环境变量 ENV）",
    )
    parser.add_argument(
        "--marker", default="",
        help="用例标记筛选，如 smoke / p0 / 'smoke or p0'（默认不筛选）",
    )
    parser.add_argument(
        "--testdir", default="testcase",
        help="用例目录或文件（默认 testcase）",
    )
    parser.add_argument(
        "--parallel", type=int, default=0,
        help="并发进程数，0 表示不并发（依赖 pytest-xdist）",
    )
    parser.add_argument(
        "--reruns", type=int, default=2,
        help="失败重试次数（默认 2）",
    )
    parser.add_argument(
        "--clean", action="store_true", default=True,
        help="执行前清空 allure 历史结果（默认开启）",
    )
    return parser.parse_args()


def build_pytest_args(args) -> list:
    """组装 pytest 命令行参数"""
    cmd_args = [
        args.testdir,
        "-v", "-s",
        f"--alluredir={ALLURE_RESULTS}",
    ]
    if args.clean:
        cmd_args.append("--clean-alluredir")
    if args.reruns > 0:
        cmd_args += [f"--reruns={args.reruns}", "--reruns-delay=1"]
    if args.marker:
        cmd_args += ["-m", args.marker]
    if args.parallel > 0:
        # pytest-xdist 并发：-n auto 自动按 CPU 核数，-n N 指定进程数
        cmd_args += ["-n", str(args.parallel) if args.parallel else "auto"]
    return cmd_args


def main():
    args = parse_args()

    # 关键：设置环境变量 ENV，pytest 加载 conftest 时 config.env 据此切换环境
    os.environ["ENV"] = args.env

    # 确保产物目录存在
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"AutoAPI-Test 启动")
    print(f"  环境   : {args.env}")
    print(f"  标记   : {args.marker or '(全部)'}")
    print(f"  用例   : {args.testdir}")
    print(f"  并发   : {args.parallel or '(单进程)'}")
    print(f"  重试   : {args.reruns}")
    print(f"  报告目录: {ALLURE_RESULTS}")
    print("=" * 60)

    pytest_args = build_pytest_args(args)
    print("pytest 参数:", " ".join(pytest_args))

    # 调用 pytest.main，返回退出码（0=全部通过，非0=有失败/错误）
    exit_code = pytest.main(pytest_args)

    # 结束后打印 Allure 报告生成命令
    print("\n" + "=" * 60)
    print("测试执行结束，退出码:", exit_code)
    print("生成 / 预览 Allure 报告：")
    print(f"  allure serve {ALLURE_RESULTS}")
    print(f"  allure generate {ALLURE_RESULTS} -o {REPORT_DIR / 'html'} --clean")
    print("=" * 60)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
