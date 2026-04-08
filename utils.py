from __future__ import annotations

from functools import wraps
from typing import Any, Callable, ParamSpec, TypeVar

from flask import jsonify, request
from database import user_exists

P = ParamSpec("P")
R = TypeVar("R")


def make_response(*, code: int, message: str, data: Any = None, http_status: int | None = None):
    if http_status is None:
        http_status = {
            0: 200,
            1001: 400,
            1002: 404,
            1003: 401,
            1004: 500,
        }.get(code, 500)
    return jsonify({"code": code, "message": message, "data": data}), http_status


def require_user_id(func: Callable[P, R]) -> Callable[P, R]:
    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs):  # type: ignore[misc]
        user_id = request.headers.get("X-User-Id")
        if not user_id:
            return make_response(code=1003, message="缺少X-User-Id", data=None, http_status=401)
        try:
            uid = str(user_id).strip()
        except Exception:
            uid = ""
        if not uid or not user_exists(user_id=uid):
            return make_response(code=1003, message="请先登录或登录已失效", data=None, http_status=401)

        kwargs["user_id"] = uid
        return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]

