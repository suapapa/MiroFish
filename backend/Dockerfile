# MiroFish Backend - 多阶段构建：builder(含编译器) -> 精简运行时
# 运行时使用 gunicorn，以非 root 用户启动
# 注意：构建上下文为仓库根目录（后端代码通过 ../../../locales 引用根目录 locales/）

# 1) 依赖构建阶段（包含 gcc 等，用于编译 psutil 等需源码构建的包）
FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0

# 编译型依赖所需的构建工具
RUN apt-get update \
  && apt-get install -y --no-install-recommends gcc python3-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制依赖描述文件以利用构建缓存
COPY backend/pyproject.toml backend/uv.lock ./

# 仅安装运行时依赖（不含 dev），生成 /app/.venv
RUN uv sync --frozen --no-dev --no-install-project

# 2) 运行阶段（精简，无编译器）
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    FLASK_DEBUG=false \
    PATH="/app/.venv/bin:$PATH"

# 保持与源码一致的目录布局：/app/backend + /app/locales
WORKDIR /app/backend

# 从 builder 复制已构建好的虚拟环境
COPY --from=builder /app/.venv /app/.venv

# 复制后端源码与共享 locales（locale.py 通过 ../../../locales 引用）
COPY backend/ /app/backend/
COPY locales/ /app/locales/

# 创建非 root 用户并准备上传目录
RUN useradd --create-home --uid 10001 app \
  && mkdir -p /app/backend/uploads \
  && chown -R app:app /app
USER app

EXPOSE 5001

# 通过 gunicorn 启动 WSGI 应用（run.py 暴露的模块级 app 对象）
CMD ["sh", "-c", "gunicorn run:app \
  --bind 0.0.0.0:5001 \
  --workers ${GUNICORN_WORKERS:-4} \
  --threads ${GUNICORN_THREADS:-2} \
  --timeout ${GUNICORN_TIMEOUT:-600} \
  --access-logfile - --error-logfile -"]
