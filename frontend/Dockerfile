# MiroFish Frontend - multi-stage build: vite static build -> nginx serving
# Build context is the repo root (needs root-level locales/, see src/i18n/index.js)

# 1) Build stage
FROM node:20-slim AS build
WORKDIR /app/frontend

# Leverage cache: install dependencies first
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Copy frontend source and shared locales (i18n references ../../../locales)
COPY frontend/ ./
COPY locales/ /app/locales/

# Frontend and backend share the same origin via nginx; call /api with a relative path
ENV VITE_API_BASE_URL="/"
RUN npm run build

# 2) Runtime stage
FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/frontend/dist /usr/share/nginx/html

EXPOSE 3000
CMD ["nginx", "-g", "daemon off;"]
