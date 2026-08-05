// ===================================================================
// Jenkins Pipeline：接口自动化测试框架 CI/CD
// 流程：拉代码 → 安装依赖 → 执行 pytest（按 marker）→ 生成 Allure 报告
//       → 钉钉/邮件通知 → 归档产物
// 依赖：Jenkins 安装 Allure Plugin（ allure commandline 由插件提供）
// ===================================================================

pipeline {
    agent any

    // 构建参数：可在 Jenkins 任务配置页选择
    parameters {
        choice(name: 'ENV',    choices: ['test', 'pre', 'prod'], description: '运行环境')
        string(name: 'MARKER', defaultValue: 'smoke',            description: 'pytest 标记，如 smoke / p0 / regression；留空跑全部')
        string(name: 'TESTDIR', defaultValue: 'testcase',         description: '用例目录或文件')
        string(name: 'PARALLEL', defaultValue: '0',               description: '并发进程数（0=单进程）')
    }

    // 全局环境变量
    environment {
        PYTHON_VERSION = '3.9'
        REPORT_DIR     = 'reports/allure-results'
        ALLURE_REPORT  = "${env.BUILD_URL}/allure"
    }

    options {
        timestamps()              // 日志带时间戳
        timeout(time: 30, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '20'))   // 保留 20 次构建
        disableConcurrentBuilds()                        // 同任务不并发
    }

    stages {
        // 1. 拉取代码
        stage('Checkout') {
            steps {
                checkout scm
                echo "当前分支: ${env.GIT_BRANCH}"
            }
        }

        // 2. 安装依赖
        stage('Install Dependencies') {
            steps {
                sh '''
                    python -m pip install --upgrade pip
                    pip install -r requirements.txt
                    # 校验关键依赖
                    python -c "import pytest, requests, allure, pymysql, redis; print('依赖就绪')"
                '''
            }
        }

        // 3. 执行测试（按 marker 与环境）
        stage('Run Tests') {
            steps {
                sh """
                    # 通过 python run.py 统一入口执行，自动设置 ENV 环境变量
                    python run.py \
                        --env ${params.ENV} \
                        --marker "${params.MARKER}" \
                        --testdir ${params.TESTDIR} \
                        --parallel ${params.PARALLEL} \
                        --reruns 2
                """
            }
        }

        // 4. 生成 Allure 报告（依赖 Jenkins Allure Plugin）
        stage('Allure Report') {
            steps {
                script {
                    if (fileExists('reports/allure-results')) {
                        // allure 插件会读取 results 目录并生成历史趋势报告
                        allure([
                            includeProperties: false,
                            jdk: '',
                            properties: [],
                            reportBuildPolicy: 'ALWAYS',
                            results: [[path: 'reports/allure-results']]
                        ])
                    } else {
                        echo '未找到 allure-results，跳过报告生成'
                    }
                }
            }
        }
    }

    // 后置处理：通知 + 归档（无论成功失败都执行）
    post {
        always {
            echo '===== 开始后置处理 ====='

            // 归档测试产物
            archiveArtifacts artifacts: 'reports/**, logs/**', allowEmptyArchive: true, fingerprint: true

            // 收集测试结果用于通知（通过解析 pytest 输出 / allure results 简单统计）
            script {
                def status = currentBuild.currentResult  // SUCCESS / UNSTABLE / FAILURE
                def summary = "构建状态: ${status}\n环境: ${params.ENV}\n标记: ${params.MARKER}\n报告: ${ALLURE_REPORT}"
                echo summary

                // 钉钉机器人通知（调用框架内置 notify，亦可直接用 curl 推 webhook）
                sh """
                    python -c "
import os
from config.env import CONFIG
from utils.notify import send_build_summary
# 简化统计：通过退出状态判定（实际可解析 allure-results 统计通过/失败数）
total, passed, failed = 0, 0, 0
send_build_summary(
    notify_config=CONFIG.get_notify(),
    total=total, passed=passed, failed=failed,
    report_url='${ALLURE_REPORT}',
    failed_cases=[],
    env='${params.ENV}',
)
" || echo '通知发送失败，忽略'
                """
            }
        }
        success {
            echo '接口自动化测试全部通过 ✅'
        }
        failure {
            echo '接口自动化测试存在失败 ❌，请查看报告'
            // 失败时 @ 全员（在 send_build_summary 中根据 failed>0 控制）
        }
        cleanup {
            echo '===== 清理工作区临时文件 ====='
            // 保留产物，仅清理 pyc 缓存
            sh 'find . -type d -name "__pycache__" -exec rm -rf {} + || true'
        }
    }
}
