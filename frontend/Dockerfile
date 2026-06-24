# MiroFish Frontend - 多阶段构建：vite 静态构建 -> nginx 服务
# 构建上下文为仓库根目录（需要根目录下的 locales/，详见 src/i18n/index.js）

# 1) 构建阶段
FROM node:20-slim AS build
WORKDIR /app/frontend

# 利用缓存：先装依赖
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# 复制前端源码与共享 locales（i18n 通过 ../../../locales 引用）
COPY frontend/ ./
COPY locales/ /app/locales/

# 前端与后端经 nginx 同源代理，使用相对路径调用 /api
ENV VITE_API_BASE_URL="/"
RUN npm run build

# 2) 运行阶段
FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html

EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
