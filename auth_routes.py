from __future__ import annotations

from flask import Blueprint, request
import re

from database import authenticate_user, create_user
from utils import make_response


auth_bp = Blueprint("auth_bp", __name__)
_PHONE_RE = re.compile(r"^1\d{10}$")


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@auth_bp.post("/api/auth/register")
def register():
    body = _body()
    phone = str(body.get("phone") or "").strip()
    password = str(body.get("password") or "")
    if not phone or not password:
        return make_response(code=1001, message="缺少必要参数 phone/password", data=None, http_status=400)
    if not _PHONE_RE.match(phone):
        return make_response(code=1001, message="手机号格式不正确", data=None, http_status=400)
    if len(password) < 6:
        return make_response(code=1001, message="密码至少6位", data=None, http_status=400)
    try:
        user = create_user(phone=phone, password=password)
        return make_response(code=0, message="ok", data=user, http_status=200)
    except ValueError as e:
        if str(e) == "phone_already_exists":
            return make_response(code=1001, message="手机号已注册", data=None, http_status=400)
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)
    except Exception:
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)


@auth_bp.post("/api/auth/login")
def login():
    body = _body()
    phone = str(body.get("phone") or "").strip()
    password = str(body.get("password") or "")
    if not phone or not password:
        return make_response(code=1001, message="缺少必要参数 phone/password", data=None, http_status=400)
    if not _PHONE_RE.match(phone):
        return make_response(code=1001, message="手机号格式不正确", data=None, http_status=400)
    try:
        user = authenticate_user(phone=phone, password=password)
        if not user:
            return make_response(code=1003, message="账号或密码错误", data=None, http_status=401)
        return make_response(code=0, message="ok", data=user, http_status=200)
    except Exception:
        return make_response(code=1004, message="服务器错误", data=None, http_status=500)

