from __future__ import annotations

import os
import json
import requests
from typing import Optional
from flask import Blueprint, request, jsonify
from config import Config
from chat_orchestrator import run_turn

from chat_music_preview import (
    build_user_message_with_music_constraint,
    ensure_reply_mentions_preview_track,
    get_recommendation_preview_for_emotion,
    merge_system_prompt_with_music_preview,
)


# 创建聊天蓝图
chat_bp = Blueprint('chat', __name__)

def get_user_id_from_headers(headers):
    """
    从请求头中获取用户ID
    """
    user_id = headers.get('X-User-Id')
    if not user_id or user_id.strip() == '':
        return None
    return user_id.strip()


def _log_agent_trace(*, user_id: str, emotion_tag: str, content: str, agent_trace):
    """
    将 Agent 编排信息写入日志，便于后续统一调参与排障。
    """
    if not isinstance(agent_trace, dict):
        return
    try:
        payload = {
            "event": "chat_agent_trace",
            "userId": user_id,
            "emotionTag": str(emotion_tag or ""),
            # 避免日志过大，仅保留前 200 字
            "contentPreview": str(content or "")[:200],
            "plan": agent_trace.get("plan"),
            "toolResults": agent_trace.get("toolResults"),
        }
        print(f"[CHAT_AGENT_TRACE] {json.dumps(payload, ensure_ascii=False)}")
    except Exception as e:
        print(f"[CHAT_AGENT_TRACE] log_failed: {str(e)}")

def build_system_prompt(emotion_tag):
    """
    构建系统提示词，根据情绪标签定制AI回复风格
    """
    style_map = {
        "happy": "认可和放大积极感受，语气轻快，不要泼冷水。",
        "sad": "先共情和接住情绪，再给一个很小、可执行的自我照顾建议。",
        "anxious": "降低紧张感，帮助用户把问题拆小，优先给稳定当下的方法。",
        "angry": "先承认愤怒的合理性，引导安全表达与降温，不鼓励冲动行为。",
        "calm": "保持温和与陪伴感，可适度引导反思或巩固好状态。",
    }

    emotion_key = str(emotion_tag or "").strip().lower()
    emotion_rule = style_map.get(emotion_key, "先理解用户感受，再给简短、温和、可执行的回应。")

    return (
        "你是“聆聆”，一个情绪陪伴助手。\n"
        "目标：让用户感到被理解、被支持，并在必要时获得一个可执行的小建议。\n"
        f"当前情绪策略：{emotion_rule}\n\n"
        "回复要求：\n"
        "1) 先共情，再建议；避免说教、评判、命令式语气。\n"
        "2) 控制在2-4句，中文口语化、自然、简洁。\n"
        "3) 若用户未明确求建议，以陪伴和澄清为主，不强行给方案。\n"
        "4) 遇到自伤/伤人等高风险信号时，优先安全提醒并建议尽快联系线下专业支持与紧急资源。\n"
        "5) 不编造事实，不声称自己执行了现实世界动作。\n"
    )

def call_ai_api(messages, *, temperature: Optional[float] = None):
    """
    调用AI API获取回复。
    temperature 默认 0.3；有推荐曲目预览时建议更低以减少编造歌名。
    """
    try:
        # Read at call time to avoid stale values from old process state.
        api_key = str(os.getenv("OPENAI_API_KEY") or Config.OPENAI_API_KEY or "").strip()
        base_url = str(os.getenv("OPENAI_BASE_URL") or Config.OPENAI_BASE_URL or "").strip().rstrip("/")
        if not api_key:
            return "AI服务未配置：缺少 OPENAI_API_KEY。"
        if not base_url:
            return "AI服务未配置：缺少 OPENAI_BASE_URL。"

        model = str(os.getenv("OPENAI_MODEL") or Config.OPENAI_MODEL or "").strip()
        if not model:
            # Platform-aware fallback model to avoid mismatch.
            model = "deepseek-chat" if "deepseek" in base_url.lower() else "gpt-4o-mini"

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        }
        
        temp = 0.3 if temperature is None else float(temperature)
        payload = {
            'model': model,
            'messages': messages,
            'temperature': max(0.0, min(2.0, temp)),
            'max_tokens': 500
        }
        
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        response.raise_for_status()
        result = response.json()
        
        if 'choices' in result and len(result['choices']) > 0:
            return result['choices'][0]['message']['content']
        else:
            return "AI回复生成失败，请稍后重试。"
            
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        print(f"AI API HTTP错误: status={status}, detail={str(e)}")
        if status == 401:
            return "AI服务鉴权失败（API Key 无效或无权限）。"
        if status == 400:
            return "AI服务请求参数错误（请检查 OPENAI_MODEL 与接口地址是否匹配）。"
        return f"AI服务上游错误（HTTP {status}），请稍后重试。"
    except requests.exceptions.RequestException as e:
        print(f"AI API调用失败: {str(e)}")
        return "AI服务暂时不可用（上游请求失败/超时），请稍后重试。"
    except Exception as e:
        print(f"AI回复生成异常: {str(e)}")
        return "系统内部错误，请联系管理员。"

@chat_bp.route('/api/chat/send', methods=['POST'])
def send_chat_message():
    """
    AI聊天接口
    POST /api/chat/send
    Headers:
        X-User-Id: 用户ID
    Request Body:
        {
            "content": "用户消息内容",
            "emotionTag": "情绪标签"  # 可选: happy/sad/anxious/angry/calm
        }
    Response:
        {
            "code": 0,
            "message": "ok",
            "data": {
                "aiReply": "AI回复内容",
                "suggestMusic": null,  # 未来扩展
                "musicTip": null       # 未来扩展
            }
        }
    """
    try:
        # 获取用户ID
        user_id = get_user_id_from_headers(request.headers)
        if not user_id:
            return jsonify({
                'code': 1003,
                'message': '未授权访问',
                'data': None
            }), 401

        # 解析请求数据
        data = request.get_json()
        if not data or 'content' not in data:
            return jsonify({
                'code': 1001,
                'message': '缺少必要的参数: content',
                'data': None
            }), 400

        content = data['content']
        emotion_tag = data.get('emotionTag', '')

        agent_mode = str(os.getenv("CHAT_AGENT_MODE", "true")).strip().lower() in ("1", "true", "yes", "on")
        if agent_mode:
            response_data = run_turn(user_id=user_id, content=content, emotion_tag=emotion_tag)
            _log_agent_trace(
                user_id=user_id,
                emotion_tag=emotion_tag,
                content=content,
                agent_trace=response_data.get("agentTrace"),
            )
            # 兼容旧出参，不在默认响应中暴露 agent 调试信息
            response_data.pop("agentTrace", None)
        else:
            # 在调用 LLM 前取一首与情绪一致的候选曲目，注入 system，便于互动（逻辑见 chat_music_preview）
            music_preview = get_recommendation_preview_for_emotion(emotion_tag)
            system_content = merge_system_prompt_with_music_preview(
                build_system_prompt(emotion_tag),
                music_preview,
            )

            user_message = build_user_message_with_music_constraint(content, music_preview)

            # 构建对话消息
            messages = [
                {
                    'role': 'system',
                    'content': system_content
                },
                {
                    'role': 'user',
                    'content': user_message,
                }
            ]

            # 有曲目预览时略降温度，减少编造其他歌名
            chat_temp = 0.12 if music_preview else 0.3

            # 调用AI API
            ai_reply = call_ai_api(messages, temperature=chat_temp)
            ai_reply = ensure_reply_mentions_preview_track(ai_reply, music_preview)
            print(f"AI回复: {ai_reply}")

            # 构建响应（suggestMusic 与上方预览一致，前端可选用同一条推荐）
            music_tip = None
            if music_preview:
                t = str(music_preview.get("title") or "").strip()
                a = str(music_preview.get("artist") or "").strip()
                if t:
                    music_tip = f"{t} — {a}" if a else t

            response_data = {
                'aiReply': ai_reply,
                'suggestMusic': music_preview,
                'musicTip': music_tip,
            }

        return jsonify({
            'code': 0,
            'message': 'ok',
            'data': response_data
        })

    except Exception as e:
        print(f"聊天接口异常: {str(e)}")
        return jsonify({
            'code': 1004,
            'message': f'服务器内部错误: {str(e)}',
            'data': None
        }), 500

# 为了兼容性保留，实际在app.py中注册蓝图
def init_app(app):
    app.register_blueprint(chat_bp)