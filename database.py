from __future__ import annotations

import json
import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import config


# -----------------------------
# 连接管理
# -----------------------------
_local = threading.local()

_SQLITE_TIMEOUT_SECONDS = 5.0
_SQLITE_BUSY_TIMEOUT_MS = 5000
_SQLITE_LOCK_RETRIES = 5
_SQLITE_LOCK_RETRY_SLEEP_SECONDS = 0.05


def _is_locked_error(err: BaseException) -> bool:
    return isinstance(err, sqlite3.OperationalError) and "database is locked" in str(err).lower()


def _commit_with_retry(conn: sqlite3.Connection) -> None:
    """
    尝试提交事务；遇到 database is locked 进行短暂重试。
    """
    for i in range(_SQLITE_LOCK_RETRIES):
        try:
            conn.commit()
            return
        except Exception as e:
            if _is_locked_error(e) and i < _SQLITE_LOCK_RETRIES - 1:
                try:
                    conn.rollback()
                except Exception:
                    pass
                time.sleep(_SQLITE_LOCK_RETRY_SLEEP_SECONDS)
                continue
            raise


def get_db() -> sqlite3.Connection:
    """
    获取 SQLite 连接（线程局部）。

    说明：
    - SQLite 默认连接不可跨线程使用；这里为避免 Flask 多线程环境问题，
      每个线程缓存一个连接。
    - 数据库文件路径来自 config.databasePath（项目根目录下自动创建）。
    """
    conn: Optional[sqlite3.Connection] = getattr(_local, "conn", None)
    if conn is not None:
        return conn

    db_path = Path(config.databasePath)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(db_path),
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,
        timeout=_SQLITE_TIMEOUT_SECONDS,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute(f"PRAGMA busy_timeout = {_SQLITE_BUSY_TIMEOUT_MS};")
    _local.conn = conn
    return conn


def init_db() -> None:
    """创建所有表结构（幂等）。"""
    conn = get_db()
    cur = conn.cursor()

    # moods（心情记录）
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS moods (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT NOT NULL,
          date TEXT NOT NULL,
          emotion TEXT NOT NULL,
          note TEXT,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_moods_user_date ON moods(user_id, date);")

    # favorites（收藏音乐）
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT NOT NULL,
          music_id TEXT NOT NULL,
          title TEXT NOT NULL,
          artist TEXT,
          cover_url TEXT,
          collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(user_id, music_id)
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_favorites_user_time ON favorites(user_id, collected_at);")

    # recent_played（最近播放）
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS recent_played (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT NOT NULL,
          music_id TEXT NOT NULL,
          title TEXT NOT NULL,
          artist TEXT,
          cover_url TEXT,
          played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_recent_user_time ON recent_played(user_id, played_at);")

    # questionnaire（问卷结果）
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS questionnaire (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT NOT NULL,
          date TEXT NOT NULL,
          type TEXT NOT NULL,
          score INTEGER NOT NULL,
          level TEXT NOT NULL,
          details TEXT,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_questionnaire_user_time ON questionnaire(user_id, created_at);")

    # user_preferences（用户设置）
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_preferences (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT UNIQUE NOT NULL,
          preferred_music_genres TEXT,
          preferred_healing_methods TEXT,
          notification_enabled BOOLEAN DEFAULT 1,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # users（账号）
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT UNIQUE NOT NULL,
          phone TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);")

    # treehole_messages（树洞聊天历史，按用户隔离）
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS treehole_messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id TEXT NOT NULL,
          seq INTEGER NOT NULL,
          msg_type TEXT NOT NULL,
          text TEXT,
          song_json TEXT,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_treehole_user_seq ON treehole_messages(user_id, seq);")

    _commit_with_retry(conn)


def _hash_password(password: str, *, salt: str | None = None) -> str:
    """
    PBKDF2-HMAC-SHA256
    storage format: pbkdf2_sha256$iterations$salt$hex_digest
    """
    iterations = 120000
    s = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), s.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${s}${dk.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iters, salt, digest = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iters),
        ).hex()
        return hmac.compare_digest(calc, digest)
    except Exception:
        return False


def create_user(*, phone: str, password: str) -> Dict[str, Any]:
    conn = get_db()
    cur = conn.cursor()
    user_id = f"user_{int(time.time() * 1000)}_{secrets.token_hex(4)}"
    pwd_hash = _hash_password(password)
    try:
        cur.execute(
            """
            INSERT INTO users(user_id, phone, password_hash)
            VALUES(?,?,?)
            """,
            (user_id, phone, pwd_hash),
        )
        _commit_with_retry(conn)
    except sqlite3.IntegrityError:
        raise ValueError("phone_already_exists")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    return {"userId": user_id, "phone": phone}


def authenticate_user(*, phone: str, password: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    row = conn.execute(
        "SELECT user_id, phone, password_hash FROM users WHERE phone = ? LIMIT 1",
        (phone,),
    ).fetchone()
    if not row:
        return None
    if not _verify_password(password, str(row["password_hash"] or "")):
        return None
    return {"userId": str(row["user_id"]), "phone": str(row["phone"])}


def user_exists(*, user_id: str) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM users WHERE user_id = ? LIMIT 1",
        (user_id,),
    ).fetchone()
    return row is not None


def get_treehole_messages(*, user_id: str, limit: int = 300) -> List[Dict[str, Any]]:
    conn = get_db()
    lim = int(limit) if limit and int(limit) > 0 else 300
    rows = conn.execute(
        """
        SELECT seq, msg_type, text, song_json
        FROM treehole_messages
        WHERE user_id = ?
        ORDER BY seq ASC, id ASC
        LIMIT ?
        """,
        (user_id, lim),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        row: Dict[str, Any] = {
            "type": str(r["msg_type"] or ""),
            "text": str(r["text"] or ""),
        }
        song = _json_loads_or_default(r["song_json"], None)
        if isinstance(song, dict):
            row["song"] = song
        out.append(row)
    return out


def replace_treehole_messages(*, user_id: str, messages: List[Dict[str, Any]]) -> None:
    conn = get_db()
    try:
        conn.execute("DELETE FROM treehole_messages WHERE user_id = ?", (user_id,))
        seq = 0
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            seq += 1
            mtype = str(msg.get("type") or "").strip()
            text = str(msg.get("text") or "")
            song = msg.get("song")
            song_json = _json_dumps(song) if isinstance(song, dict) else None
            conn.execute(
                """
                INSERT INTO treehole_messages(user_id, seq, msg_type, text, song_json)
                VALUES(?,?,?,?,?)
                """,
                (user_id, seq, mtype, text, song_json),
            )
        _commit_with_retry(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def clear_treehole_messages(*, user_id: str) -> None:
    conn = get_db()
    try:
        conn.execute("DELETE FROM treehole_messages WHERE user_id = ?", (user_id,))
        _commit_with_retry(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


# -----------------------------
# 通用工具：时间戳/JSON
# -----------------------------
def _ts_to_iso(ts: Any) -> Optional[str]:
    """
    SQLite CURRENT_TIMESTAMP 典型格式：YYYY-MM-DD HH:MM:SS
    API 要求：YYYY-MM-DDTHH:mm:ss
    """
    if ts is None or ts == "":
        return None
    if isinstance(ts, datetime):
        # 去掉微秒，保持到秒
        return ts.replace(microsecond=0).isoformat()
    if not isinstance(ts, str):
        ts = str(ts)
    # 已经是 ISO 格式则直接返回
    if "T" in ts:
        return ts.split(".")[0]
    return ts.replace(" ", "T")


def _iso_to_sqlite_ts(iso_ts: str) -> str:
    """把 YYYY-MM-DDTHH:mm:ss 转回 SQLite 可读的 YYYY-MM-DD HH:MM:SS。"""
    return iso_ts.replace("T", " ")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads_or_default(value: Optional[str], default: Any):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


# -----------------------------
# moods CRUD
# -----------------------------
def add_mood(*, user_id: str, date: str, emotion: str, note: Optional[str] = None) -> int:
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO moods(user_id, date, emotion, note) VALUES(?,?,?,?)",
            (user_id, date, emotion, note),
        )
        _commit_with_retry(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    return int(cur.lastrowid)


def get_moods(*, user_id: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    获取心情列表。
    返回字段对齐文档：[{id,date,emotion,note}]
    """
    conn = get_db()
    params: List[Any] = [user_id]
    where = ["user_id = ?"]
    if start_date:
        where.append("date >= ?")
        params.append(start_date)
    if end_date:
        where.append("date <= ?")
        params.append(end_date)

    sql = f"SELECT id, date, emotion, note FROM moods WHERE {' AND '.join(where)} ORDER BY date DESC, id DESC"
    rows = conn.execute(sql, params).fetchall()
    return [
        {"id": int(r["id"]), "date": r["date"], "emotion": r["emotion"], "note": r["note"]}
        for r in rows
    ]


def get_latest_mood_on_date(*, user_id: str, date: str) -> Optional[Dict[str, Any]]:
    conn = get_db()
    row = conn.execute(
        "SELECT id, date, emotion, note, created_at FROM moods WHERE user_id=? AND date=? ORDER BY id DESC LIMIT 1",
        (user_id, date),
    ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "date": str(row["date"] or ""),
        "emotion": str(row["emotion"] or ""),
        "note": row["note"],
        "created_at": _ts_to_iso(row["created_at"]),
    }


def delete_mood(*, user_id: str, mood_id: int) -> bool:
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM moods WHERE user_id = ? AND id = ?", (user_id, int(mood_id)))
        _commit_with_retry(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    return cur.rowcount > 0


# -----------------------------
# favorites CRUD
# -----------------------------
def add_favorite(
    *,
    user_id: str,
    music_id: str,
    title: str,
    artist: Optional[str],
    cover_url: Optional[str],
) -> int:
    """
    添加收藏。
    注意：表有 UNIQUE(user_id, music_id)，重复收藏会抛 sqlite3.IntegrityError。
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO favorites(user_id, music_id, title, artist, cover_url)
            VALUES(?,?,?,?,?)
            """,
            (user_id, music_id, title, artist, cover_url),
        )
        _commit_with_retry(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    return int(cur.lastrowid)


def is_favorite(*, user_id: str, music_id: str) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM favorites WHERE user_id = ? AND music_id = ? LIMIT 1",
        (user_id, music_id),
    ).fetchone()
    return row is not None


def remove_favorite(*, user_id: str, music_id: str) -> bool:
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM favorites WHERE user_id = ? AND music_id = ?", (user_id, music_id))
        _commit_with_retry(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    return cur.rowcount > 0


def get_favorites(*, user_id: str, page: int = 1, size: int = 20) -> Tuple[List[Dict[str, Any]], int]:
    """
    获取收藏列表（分页）。
    返回：(favorites, total)
    favorites 字段对齐文档：
    [{musicId,title,artist,coverUrl,collectedAt}]
    """
    page = int(page) if page and int(page) > 0 else 1
    size = int(size) if size and int(size) > 0 else 20
    offset = (page - 1) * size

    conn = get_db()
    total_row = conn.execute("SELECT COUNT(1) AS c FROM favorites WHERE user_id = ?", (user_id,)).fetchone()
    total = int(total_row["c"]) if total_row else 0

    rows = conn.execute(
        """
        SELECT music_id, title, artist, cover_url, collected_at
        FROM favorites
        WHERE user_id = ?
        ORDER BY collected_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, size, offset),
    ).fetchall()

    favorites = []
    for r in rows:
        favorites.append(
            {
                "musicId": r["music_id"],
                "title": r["title"],
                "artist": r["artist"] or "",
                "coverUrl": r["cover_url"] or "",
                "collectedAt": _ts_to_iso(r["collected_at"]),
            }
        )
    return favorites, total


# -----------------------------
# recent_played CRUD
# -----------------------------
def add_recent(
    *,
    user_id: str,
    music_id: str,
    title: str,
    artist: Optional[str],
    cover_url: Optional[str],
    played_at: Optional[str] = None,
) -> int:
    """
    添加播放记录。played_at 不传则由数据库生成。

    文档允许 playedAt 可选：不传则服务器生成。
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        if played_at:
            cur.execute(
                """
                INSERT INTO recent_played(user_id, music_id, title, artist, cover_url, played_at)
                VALUES(?,?,?,?,?,?)
                """,
                (user_id, music_id, title, artist, cover_url, _iso_to_sqlite_ts(played_at)),
            )
        else:
            cur.execute(
                """
                INSERT INTO recent_played(user_id, music_id, title, artist, cover_url)
                VALUES(?,?,?,?,?)
                """,
                (user_id, music_id, title, artist, cover_url),
            )
        _commit_with_retry(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    rid = int(cur.lastrowid)
    clean_old_recent(user_id=user_id, keep=50)
    return rid


def get_recent(*, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """
    获取最近播放列表。
    返回字段对齐文档：[{musicId,title,artist,coverUrl,playedAt}]
    """
    limit = int(limit) if limit and int(limit) > 0 else 20
    conn = get_db()
    rows = conn.execute(
        """
        SELECT music_id, title, artist, cover_url, played_at
        FROM recent_played
        WHERE user_id = ?
        ORDER BY played_at DESC, id DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    recent = []
    for r in rows:
        recent.append(
            {
                "musicId": r["music_id"],
                "title": r["title"],
                "artist": r["artist"] or "",
                "coverUrl": r["cover_url"] or "",
                "playedAt": _ts_to_iso(r["played_at"]),
            }
        )
    return recent


def clean_old_recent(*, user_id: str, keep: int = 50) -> None:
    """每个用户保留最近 keep 条播放记录（默认 50）。"""
    keep = int(keep) if keep and int(keep) > 0 else 50
    conn = get_db()
    try:
        conn.execute(
            """
            DELETE FROM recent_played
            WHERE user_id = ?
              AND id NOT IN (
                SELECT id FROM recent_played
                WHERE user_id = ?
                ORDER BY played_at DESC, id DESC
                LIMIT ?
              )
            """,
            (user_id, user_id, keep),
        )
        _commit_with_retry(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


# -----------------------------
# questionnaire CRUD
# -----------------------------
def add_questionnaire(
    *,
    user_id: str,
    date: str,
    q_type: str,
    score: int,
    level: str,
    details: Optional[Dict[str, Any]] = None,
) -> int:
    """
    保存问卷结果。
    details 在数据库中以 JSON 字符串保存。
    """
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO questionnaire(user_id, date, type, score, level, details)
            VALUES(?,?,?,?,?,?)
            """,
            (user_id, date, q_type, int(score), level, _json_dumps(details or {})),
        )
        _commit_with_retry(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    return int(cur.lastrowid)


def get_latest_questionnaire(*, user_id: str) -> Optional[Dict[str, Any]]:
    """
    获取最近一次问卷结果（对齐文档只返回 date/type/score/level）。
    若不存在返回 None（路由层应映射为 404 + code=1002）。
    """
    conn = get_db()
    row = conn.execute(
        """
        SELECT date, type, score, level
        FROM questionnaire
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if not row:
        return None
    return {"date": row["date"], "type": row["type"], "score": int(row["score"]), "level": row["level"]}


# -----------------------------
# user_preferences CRUD
# -----------------------------
def get_preferences(*, user_id: str) -> Dict[str, Any]:
    """
    获取用户设置。
    文档要求字段始终存在：preferredMusicGenres/preferredHealingMethods/notificationEnabled
    """
    conn = get_db()
    row = conn.execute(
        """
        SELECT preferred_music_genres, preferred_healing_methods, notification_enabled
        FROM user_preferences
        WHERE user_id = ?
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if not row:
        return {"preferredMusicGenres": [], "preferredHealingMethods": [], "notificationEnabled": True}

    return {
        "preferredMusicGenres": _json_loads_or_default(row["preferred_music_genres"], []),
        "preferredHealingMethods": _json_loads_or_default(row["preferred_healing_methods"], []),
        "notificationEnabled": bool(row["notification_enabled"]),
    }


def save_preferences(
    *,
    user_id: str,
    preferred_music_genres: Optional[List[str]] = None,
    preferred_healing_methods: Optional[List[str]] = None,
    notification_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    保存用户设置（支持部分字段更新）。
    返回保存后的完整设置（对齐文档响应 data）。
    """
    existing = get_preferences(user_id=user_id)
    merged = {
        "preferredMusicGenres": existing["preferredMusicGenres"],
        "preferredHealingMethods": existing["preferredHealingMethods"],
        "notificationEnabled": existing["notificationEnabled"],
    }
    if preferred_music_genres is not None:
        merged["preferredMusicGenres"] = preferred_music_genres
    if preferred_healing_methods is not None:
        merged["preferredHealingMethods"] = preferred_healing_methods
    if notification_enabled is not None:
        merged["notificationEnabled"] = bool(notification_enabled)

    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO user_preferences(user_id, preferred_music_genres, preferred_healing_methods, notification_enabled, updated_at)
            VALUES(?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
              preferred_music_genres = excluded.preferred_music_genres,
              preferred_healing_methods = excluded.preferred_healing_methods,
              notification_enabled = excluded.notification_enabled,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                user_id,
                _json_dumps(merged["preferredMusicGenres"]),
                _json_dumps(merged["preferredHealingMethods"]),
                1 if merged["notificationEnabled"] else 0,
            ),
        )
        _commit_with_retry(conn)
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    return merged

