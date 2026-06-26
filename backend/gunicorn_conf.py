"""Gunicorn configuration with filtered access logging."""
import os

from gunicorn.glogging import Logger

# High-frequency polling endpoints excluded from access logs
ACCESS_LOG_SKIP_PREFIXES = (
    '/api/graph/task/',
)


class FilteredAccessLogger(Logger):
    """Skip access logs for noisy polling endpoints."""

    def access(self, resp, req, environ, request_time):
        path = environ.get('PATH_INFO', '')
        if any(path.startswith(prefix) for prefix in ACCESS_LOG_SKIP_PREFIXES):
            return
        super().access(resp, req, environ, request_time)


logger_class = FilteredAccessLogger
bind = '0.0.0.0:5001'
workers = int(os.environ.get('GUNICORN_WORKERS', 4))
threads = int(os.environ.get('GUNICORN_THREADS', 2))
timeout = int(os.environ.get('GUNICORN_TIMEOUT', 600))
accesslog = '-'
errorlog = '-'
