# ===================================================================
# Dockerfile：接口自动化测试框架容器镜像
# 基础镜像：python:3.9-slim（体积小，满足运行需求）
# ===================================================================
FROM python:3.9-slim

# 维护者信息
LABEL maintainer="AutoAPI-Test" \
      description="接口自动化测试框架 - 关键字驱动 + 数据驱动"

# 设置时区为亚洲/上海，避免日志时间错乱
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ENV=test

# 安装系统依赖（git 用于 allure 可选，curl 用于健康检查）
RUN apt-get update && apt-get install -y --no-install-recommends \
        tzdata \
        curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 设置工作目录
WORKDIR /app

# 先拷贝依赖清单，利用 Docker 层缓存加速构建
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝项目代码
COPY . .

# 预创建运行时产物目录
RUN mkdir -p /app/reports /app/logs

# 容器内默认通过 run.py 入口执行
# 可通过 docker run 传参覆盖：docker run --rm -e ENV=test autoapi-test --marker smoke
ENTRYPOINT ["python", "run.py"]

# 默认参数：test 环境跑冒烟用例
CMD ["--env", "test", "--marker", "smoke"]
