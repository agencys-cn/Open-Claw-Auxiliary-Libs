"""
本地数据库
A2A 协议扩展 - SQLite 本地缓存

概述:
    使用 SQLite 持久化存储：
    1. 会话信息（Session）
    2. Agent Card 缓存
    3. 任务历史（可选）
    
特性:
    - 过期自动清理（默认 24 小时无访问删除）
    - 会话复用（同一 ID 继续执行）
    - 持久化存储（重启不丢失）

数据库文件:
    ~/.openclaw/a2a_cache.db

示例:
    >>> db = A2ADatabase()
    >>> 
    >>> # 存储会话
    >>> db.save_session(session_id, {"state": "active"})
    >>> 
    >>> # 获取会话
    >>> session = db.get_session(session_id)
    >>> 
    >>> # 清理过期会话
    >>> db.cleanup_expired()
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger("a2a")


@dataclass
class SessionRecord:
    """会话记录"""
    session_id: str
    data: str  # JSON 序列化的数据
    created_at: float  # Unix 时间戳
    updated_at: float
    last_accessed: float
    expires_at: float  # 过期时间


@dataclass
class AgentCardRecord:
    """Agent Card 记录"""
    name: str
    data: str  # JSON 序列化的数据
    updated_at: float
    ttl: int = 3600  # 缓存时间（秒）


class A2ADatabase:
    """
    A2A 本地数据库
    
    使用 SQLite 持久化存储会话和 Agent Card。
    
    Attributes:
        db_path: 数据库文件路径
        _conn: 数据库连接
        session_ttl: 会话过期时间（秒），默认 86400（24小时）
        cleanup_interval: 清理间隔（秒），默认 3600（1小时）
    
    Example:
        >>> db = A2ADatabase()
        >>> 
        >>> # 存储会话
        >>> db.save_session("session_123", {"state": "active", "messages": []})
        >>> 
        >>> # 获取会话
        >>> session = db.get_session("session_123")
        >>> if session:
        ...     print(session["state"])
        >>> 
        >>> # 清理过期
        >>> db.cleanup_expired()
    """
    
    def __init__(
        self,
        db_path: Optional[str] = None,
        session_ttl: int = 86400,  # 24 小时
        cleanup_interval: int = 3600  # 1 小时
    ):
        """
        初始化数据库
        
        Args:
            db_path: 数据库文件路径，默认 ~/.openclaw/a2a_cache.db
            session_ttl: 会话过期时间（秒）
            cleanup_interval: 自动清理间隔（秒）
        """
        if db_path is None:
            db_path = os.path.expanduser("~/.openclaw/a2a_cache.db")
        
        self.db_path = db_path
        self.session_ttl = session_ttl
        self.cleanup_interval = cleanup_interval
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        
        # 确保目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # 初始化数据库
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # 会话表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_accessed REAL NOT NULL,
                expires_at REAL NOT NULL
            )
        """)
        
        # Agent Card 缓存表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_cards (
                name TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at REAL NOT NULL,
                ttl INTEGER NOT NULL DEFAULT 3600
            )
        """)
        
        # 索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_expires 
            ON sessions(expires_at)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_last_accessed 
            ON sessions(last_accessed)
        """)
        
        conn.commit()
        logger.info(f"数据库初始化完成: {self.db_path}")
    
    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    @contextmanager
    def _transaction(self):
        """事务上下文管理器"""
        conn = self._get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    
    # ─────────────────────────────────────────
    # 会话操作
    # ─────────────────────────────────────────
    
    def save_session(self, session_id: str, data: Dict[str, Any]) -> bool:
        """
        保存会话
        
        如果会话已存在则更新，否则创建新会话。
        同时更新 last_accessed 时间。
        
        Args:
            session_id: 会话 ID
            data: 会话数据
        
        Returns:
            是否成功
        """
        now = time.time()
        
        with self._transaction() as conn:
            cursor = conn.cursor()
            
            # 检查是否存在
            cursor.execute(
                "SELECT session_id FROM sessions WHERE session_id = ?",
                (session_id,)
            )
            exists = cursor.fetchone() is not None
            
            if exists:
                # 更新
                cursor.execute("""
                    UPDATE sessions 
                    SET data = ?, updated_at = ?, last_accessed = ?,
                        expires_at = ?
                    WHERE session_id = ?
                """, (
                    json.dumps(data),
                    now,
                    now,
                    now + self.session_ttl,
                    session_id
                ))
            else:
                # 创建
                cursor.execute("""
                    INSERT INTO sessions 
                    (session_id, data, created_at, updated_at, last_accessed, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    session_id,
                    json.dumps(data),
                    now,
                    now,
                    now,
                    now + self.session_ttl
                ))
        
        logger.debug(f"会话已保存: {session_id}")
        return True
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        获取会话
        
        同时更新 last_accessed 时间，实现访问延期。
        
        Args:
            session_id: 会话 ID
        
        Returns:
            会话数据，如果不存在或已过期返回 None
        """
        now = time.time()
        
        with self._transaction() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT data, expires_at FROM sessions 
                WHERE session_id = ?
            """, (session_id,))
            
            row = cursor.fetchone()
            if row is None:
                return None
            
            # 检查是否过期
            if row["expires_at"] < now:
                # 已过期，删除
                cursor.execute(
                    "DELETE FROM sessions WHERE session_id = ?",
                    (session_id,)
                )
                logger.debug(f"会话已过期删除: {session_id}")
                return None
            
            # 更新访问时间
            cursor.execute("""
                UPDATE sessions 
                SET last_accessed = ?, expires_at = ?
                WHERE session_id = ?
            """, (now, now + self.session_ttl, session_id))
            
            return json.loads(row["data"])
    
    def touch_session(self, session_id: str) -> bool:
        """
        刷新会话过期时间
        
        Args:
            session_id: 会话 ID
        
        Returns:
            是否成功
        """
        now = time.time()
        
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE sessions 
                SET last_accessed = ?, expires_at = ?
                WHERE session_id = ?
            """, (now, now + self.session_ttl, session_id))
            
            return cursor.rowcount > 0
    
    def delete_session(self, session_id: str) -> bool:
        """
        删除会话
        
        Args:
            session_id: 会话 ID
        
        Returns:
            是否成功
        """
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,)
            )
            deleted = cursor.rowcount > 0
        
        if deleted:
            logger.debug(f"会话已删除: {session_id}")
        return deleted
    
    def list_sessions(self, include_expired: bool = False) -> List[Dict[str, Any]]:
        """
        列出所有会话
        
        Args:
            include_expired: 是否包含已过期的会话
        
        Returns:
            会话列表
        """
        now = time.time()
        conn = self._get_conn()
        cursor = conn.cursor()
        
        if include_expired:
            cursor.execute("""
                SELECT session_id, data, created_at, updated_at, 
                       last_accessed, expires_at
                FROM sessions
                ORDER BY last_accessed DESC
            """)
        else:
            cursor.execute("""
                SELECT session_id, data, created_at, updated_at,
                       last_accessed, expires_at
                FROM sessions
                WHERE expires_at > ?
                ORDER BY last_accessed DESC
            """, (now,))
        
        rows = cursor.fetchall()
        return [
            {
                "session_id": row["session_id"],
                "data": json.loads(row["data"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "last_accessed": row["last_accessed"],
                "expires_at": row["expires_at"],
                "is_expired": row["expires_at"] < now
            }
            for row in rows
        ]
    
    def cleanup_expired(self) -> int:
        """
        清理过期会话
        
        Returns:
            清理的会话数量
        """
        now = time.time()
        
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM sessions WHERE expires_at < ?",
                (now,)
            )
            deleted = cursor.rowcount
        
        if deleted > 0:
            logger.info(f"清理了 {deleted} 个过期会话")
        
        return deleted
    
    def cleanup_idle(self, idle_seconds: int = 604800) -> int:
        """
        清理长期未访问的会话
        
        Args:
            idle_seconds: 空闲时间阈值（秒），默认 7 天
        
        Returns:
            清理的会话数量
        """
        now = time.time()
        cutoff = now - idle_seconds
        
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM sessions WHERE last_accessed < ?",
                (cutoff,)
            )
            deleted = cursor.rowcount
        
        if deleted > 0:
            logger.info(f"清理了 {deleted} 个空闲会话（>{idle_seconds}s 无访问）")
        
        return deleted
    
    # ─────────────────────────────────────────
    # Agent Card 缓存
    # ─────────────────────────────────────────
    
    def save_agent_card(self, name: str, data: Dict[str, Any], ttl: int = 3600) -> bool:
        """
        保存 Agent Card 缓存
        
        Args:
            name: Agent 名称
            data: Agent Card 数据
            ttl: 缓存时间（秒），默认 1 小时
        
        Returns:
            是否成功
        """
        now = time.time()
        
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO agent_cards 
                (name, data, updated_at, ttl)
                VALUES (?, ?, ?, ?)
            """, (name, json.dumps(data), now, ttl))
        
        logger.debug(f"Agent Card 已缓存: {name}")
        return True
    
    def get_agent_card(self, name: str) -> Optional[Dict[str, Any]]:
        """
        获取 Agent Card 缓存
        
        Args:
            name: Agent 名称
        
        Returns:
            Agent Card 数据，如果不存在或已过期返回 None
        """
        now = time.time()
        
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT data, updated_at, ttl FROM agent_cards 
            WHERE name = ?
        """, (name,))
        
        row = cursor.fetchone()
        if row is None:
            return None
        
        # 检查是否过期
        if row["updated_at"] + row["ttl"] < now:
            cursor.execute(
                "DELETE FROM agent_cards WHERE name = ?",
                (name,)
            )
            logger.debug(f"Agent Card 已过期: {name}")
            return None
        
        return json.loads(row["data"])
    
    def delete_agent_card(self, name: str) -> bool:
        """
        删除 Agent Card 缓存
        
        Args:
            name: Agent 名称
        
        Returns:
            是否成功
        """
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM agent_cards WHERE name = ?",
                (name,)
            )
            return cursor.rowcount > 0
    
    def cleanup_expired_cards(self) -> int:
        """
        清理过期的 Agent Card
        
        Returns:
            清理的数量
        """
        now = time.time()
        
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM agent_cards 
                WHERE updated_at + ttl < ?
            """, (now,))
            deleted = cursor.rowcount
        
        if deleted > 0:
            logger.info(f"清理了 {deleted} 个过期 Agent Card")
        
        return deleted
    
    # ─────────────────────────────────────────
    # 统计和清理
    # ─────────────────────────────────────────
    
    def get_stats(self) -> Dict[str, Any]:
        """
        获取数据库统计
        
        Returns:
            统计信息字典
        """
        now = time.time()
        conn = self._get_conn()
        cursor = conn.cursor()
        
        # 会话统计
        cursor.execute("SELECT COUNT(*) as total FROM sessions")
        total_sessions = cursor.fetchone()["total"]
        
        cursor.execute("SELECT COUNT(*) as active FROM sessions WHERE expires_at > ?", (now,))
        active_sessions = cursor.fetchone()["active"]
        
        # Agent Card 统计
        cursor.execute("SELECT COUNT(*) as total FROM agent_cards")
        total_cards = cursor.fetchone()["total"]
        
        # 数据库大小
        db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        
        return {
            "sessions": {
                "total": total_sessions,
                "active": active_sessions,
                "expired": total_sessions - active_sessions
            },
            "agent_cards": {
                "total": total_cards
            },
            "database": {
                "path": self.db_path,
                "size_bytes": db_size,
                "size_mb": round(db_size / 1024 / 1024, 2)
            }
        }
    
    async def start_cleanup_loop(self):
        """启动定期清理任务"""
        if self._cleanup_task is not None:
            return
        
        async def cleanup_loop():
            while True:
                try:
                    await asyncio.sleep(self.cleanup_interval)
                    self.cleanup_expired()
                    self.cleanup_expired_cards()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"清理任务异常: {e}")
        
        self._cleanup_task = asyncio.create_task(cleanup_loop())
        logger.info("数据库清理任务已启动")
    
    async def stop_cleanup_loop(self):
        """停止定期清理任务"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None
            logger.info("数据库清理任务已停止")
    
    def close(self):
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("数据库连接已关闭")
    
    def __del__(self):
        """析构时关闭连接"""
        self.close()


# ─────────────────────────────────────────────
# 全局实例
# ─────────────────────────────────────────────

# 全局数据库实例
_a2a_db: Optional[A2ADatabase] = None


def get_database() -> A2ADatabase:
    """
    获取全局数据库实例（单例模式）
    
    Returns:
        A2ADatabase 实例
    """
    global _a2a_db
    if _a2a_db is None:
        _a2a_db = A2ADatabase()
    return _a2a_db


def init_database() -> A2ADatabase:
    """
    初始化全局数据库
    
    Returns:
        A2ADatabase 实例
    """
    db = get_database()
    return db
