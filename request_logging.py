from __future__ import annotations

import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify, make_response, request

_LOG = logging.getLogger("app.http")
_LOG.propagate = False


class _FlushingRotatingFileHandler(RotatingFileHandler):
    """每次写入后 flush，尽量让 IDE / tail 立刻看到新行。"""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()
        try:
            if self.stream and hasattr(self.stream, "fileno"):
                os.fsync(self.stream.fileno())
        except OSError:
            pass


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(
        os.path.normpath(b)
    )


def _truncate_text(s: str, max_len: int = 4000) -> str:
    if len(s) <= max_len:
        return s
    return s[:max_len] + "…"


def _body_preview() -> str:
    """记录客户端发来的 body（JSON / 原始文本），过长则截断。"""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return ""
    ct = (request.content_type or "").lower()
    if "application/json" in ct:
        j = request.get_json(silent=True)
        if j is not None:
            try:
                return _truncate_text(json.dumps(j, ensure_ascii=False, default=str))
            except Exception:
                pass
    try:
        raw = request.get_data(cache=True, as_text=True)
        return _truncate_text(raw)
    except Exception:
        return "<unreadable>"


def _apply_cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-User-Id"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    return resp


def setup_request_logging(app: Flask) -> None:
    """
    本地滚动日志：backend/logs/app.log
    - SERVER：实际到达 Flask 的 HTTP 请求（含 Origin / User-Agent / X-User-Id / query / body 摘要）
    - CLIENT_OUT：前端在发业务请求前主动上报的“意图”（便于对照；即使主请求失败也能看到客户端点了什么）
    同时镜像到 stderr。
    """
    log_dir = Path(__file__).resolve().parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "app.log"
    target = str(log_path.resolve())

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    )

    has_file = any(
        isinstance(h, RotatingFileHandler)
        and _same_path(getattr(h, "baseFilename", ""), target)
        for h in _LOG.handlers
    )
    if not has_file:
        file_handler = _FlushingRotatingFileHandler(
            target,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        _LOG.addHandler(file_handler)

    if not any(
        isinstance(h, logging.StreamHandler) and h.stream is sys.stderr
        for h in _LOG.handlers
    ):
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        _LOG.addHandler(console)

    _LOG.setLevel(logging.INFO)

    print(f"[app.http] LOG FILE: {target}", file=sys.stderr, flush=True)
    _LOG.info("HTTP logging ready, file=%s", target)

    @app.route("/api/log/outbound", methods=["POST", "OPTIONS"])
    def _log_client_outbound():
        """前端上报：即将发起的业务请求（与 SERVER 日志对照）。"""
        if request.method == "OPTIONS":
            return _apply_cors(make_response("", 204))
        payload = request.get_json(silent=True) or {}
        page = payload.get("page", "-")
        method = payload.get("method", "?")
        path = payload.get("path", "?")
        data = payload.get("data")
        data_s = ""
        try:
            data_s = _truncate_text(json.dumps(data, ensure_ascii=False, default=str))
        except Exception:
            data_s = _truncate_text(str(data))
        _LOG.info(
            "CLIENT_OUT page=%s %s %s data=%s",
            page,
            method,
            path,
            data_s or "-",
        )
        resp = jsonify({"code": 0, "message": "ok", "data": None})
        return _apply_cors(resp)

    @app.before_request
    def _log_request_start():
        if request.method == "OPTIONS":
            return _apply_cors(make_response("", 204))

        request._start_time = time.perf_counter()  # type: ignore[attr-defined]
        qs = request.query_string.decode("utf-8", errors="replace")
        body = _body_preview()
        _LOG.info(
            "SERVER %s %s qs=%s origin=%s referer=%s x_user=%s ua=%s from=%s body=%s",
            request.method,
            request.path,
            qs or "-",
            request.headers.get("Origin", "-"),
            request.headers.get("Referer", "-"),
            request.headers.get("X-User-Id", "-"),
            request.headers.get("User-Agent", "-"),
            request.remote_addr,
            body or "-",
        )

    @app.after_request
    def _log_request_end(response):
        start_time = getattr(request, "_start_time", None)
        elapsed_ms = (
            int((time.perf_counter() - start_time) * 1000) if start_time else -1
        )
        _LOG.info(
            "RES %s %s status=%s cost_ms=%s",
            request.method,
            request.path,
            response.status_code,
            elapsed_ms,
        )
        return _apply_cors(response)
