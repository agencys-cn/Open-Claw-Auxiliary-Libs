"""
会话管理器 - ULID 会话 + 任务队列
基于数据库持久化
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any

import ulid

from db import get_database, A2ADatabase

logger = logging.getLogger("a2a")


class SessionState(Enum):
    """会话状态"""
    IDLE = "idle"           # 空闲，等待新任务
    PROCESSING = "processing"  # 正在处理任务
    QUEUED = "queued"       # 排队中


class SessionTask:
    """会话任务"""
    
    def __init__(
        self,
        ulid: str,
        content: str,
        created_at: Optional[datetime] = None,
        a2a_task_id: Optional[str] = None,
        result: Any = None,
        error: str = ""
    ):
        self.ulid = ulid
        self.content = content
        self.created_at = created_at or datetime.now()
        self.a2a_task_id = a2a_task_id
        self.result = result
        self.error = error
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "ulid": self.ulid,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "a2a_task_id": self.a2a_task_id,
            "result": self.result,
            "error": self.error
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionTask":
        return cls(
            ulid=data["ulid"],
            content=data["content"],
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            a2a_task_id=data.get("a2a_task_id"),
            result=data.get("result"),
            error=data.get("error", "")
        )


class GatewaySession:
    """Gateway 会话"""
    
    def __init__(
        self,
        ulid: str,
        state: SessionState = SessionState.IDLE,
        tasks: Optional[List[SessionTask]] = None,
        current_task: Optional[SessionTask] = None,
        created_at: Optional[datetime] = None,
        last_active: Optional[datetime] = None
    ):
        self.ulid = ulid
        self.state = state
        self.tasks = tasks or []
        self.current_task = current_task
        self.created_at = created_at or datetime.now()
        self.last_active = last_active or datetime.now()
        self._is_dirty = False  # 是否有未保存的更改
    
    @property
    def is_dirty(self) -> bool:
        return self._is_dirty
    
    def mark_dirty(self):
        self._is_dirty = True
    
    def add_task(self, content: str) -> SessionTask:
        """添加任务到队列"""
        task = SessionTask(ulid=self.ulid, content=content)
        self.tasks.append(task)
        self.last_active = datetime.now()
        self.mark_dirty()
        return task
    
    def get_next_task(self) -> Optional[SessionTask]:
        """获取下一个待处理任务"""
        if self.tasks:
            self.mark_dirty()
            return self.tasks.pop(0)
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "ulid": self.ulid,
            "state": self.state.value,
            "tasks": [t.to_dict() for t in self.tasks],
            "current_task": self.current_task.to_dict() if self.current_task else None,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GatewaySession":
        tasks = [SessionTask.from_dict(t) for t in data.get("tasks", [])]
        current_task = None
        if data.get("current_task"):
            current_task = SessionTask.from_dict(data["current_task"])
        
        return cls(
            ulid=data["ulid"],
            state=SessionState(data.get("state", "idle")),
            tasks=tasks,
            current_task=current_task,
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            last_active=datetime.fromisoformat(data["last_active"]) if data.get("last_active") else datetime.now()
        )


class SessionStore:
    """
    会话存储管理
    
    支持数据库持久化，过期清理，会话复用。
    
    Features:
        - 持久化存储到 SQLite
        - 过期自动清理（24小时无访问删除）
        - 会话复用（同一 ULID 继续执行）
        - 定期保存变更
    
    Example:
        >>> store = SessionStore()
        >>> 
        >>> # 创建会话
        >>> session, is_new = await store.get_or_create_session()
        >>> 
        >>> # 添加任务
        >>> task, is_first = await store.queue_task(session.ulid, "写一个小说")
        >>> 
        >>> # 完成当前任务
        >>> next_task = await store.complete_current_task(session.ulid, result="完成")
    """
    
    def __init__(self, db: Optional[A2ADatabase] = None):
        """
        初始化会话存储
        
        Args:
            db: 数据库实例（可选，默认使用全局实例）
        """
        self._db = db
        self._sessions: Dict[str, GatewaySession] = {}  # session_key -> GatewaySession
        self._ulid_index: Dict[str, str] = {}  # ulid -> session_key
        self._lock = asyncio.Lock()
        self._save_task: Optional[asyncio.Task] = None
        self._cleanup_task: Optional[asyncio.Task] = None
        
        # 清理配置
        self._cleanup_enabled = os.getenv("SESSION_CLEANUP_ENABLED", "true").lower() == "true"
        self._cleanup_interval = int(os.getenv("SESSION_CLEANUP_INTERVAL", "86400"))  # 24小时
        self._max_idle_days = int(os.getenv("SESSION_MAX_IDLE_DAYS", "15"))
    
    @property
    def db(self) -> A2ADatabase:
        """获取数据库实例"""
        if self._db is None:
            self._db = get_database()
        return self._db
    
    async def initialize(self):
        """初始化（从数据库加载会话）"""
        await self._load_from_db()
        
        # 启动定期保存
        self._save_task = asyncio.create_task(self._save_loop())
        
        # 启动定期清理（仅当启用时）
        if self._cleanup_enabled:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info(f"会话清理已启用: 间隔={self._cleanup_interval}s, 最大空闲={self._max_idle_days}天")
        else:
            logger.info("会话清理已禁用")
        
        logger.info(f"会话存储初始化完成，加载 {len(self._sessions)} 个会话")
    
    async def _load_from_db(self):
        """从数据库加载会话"""
        try:
            sessions_data = self.db.list_sessions(include_expired=False)
            
            for session_data in sessions_data:
                data = session_data.get("data", {})
                if not data:
                    continue
                
                try:
                    session = GatewaySession.from_dict(data)
                    session_key = f"session_{session.ulid}"
                    self._sessions[session_key] = session
                    self._ulid_index[session.ulid] = session_key
                except Exception as e:
                    logger.warning(f"加载会话失败: {e}")
                    continue
        except Exception as e:
            logger.error(f"从数据库加载会话失败: {e}")
    
    async def _save_to_db(self, session: GatewaySession):
        """保存单个会话到数据库"""
        try:
            self.db.save_session(f"session_{session.ulid}", session.to_dict())
            session._is_dirty = False
        except Exception as e:
            logger.error(f"保存会话失败: {e}")
    
    async def _save_all_dirty(self):
        """保存所有有变更的会话"""
        async with self._lock:
            for session in self._sessions.values():
                if session.is_dirty:
                    await self._save_to_db(session)
    
    async def _save_loop(self):
        """定期保存循环（每 30 秒）"""
        while True:
            try:
                await asyncio.sleep(30)
                await self._save_all_dirty()
            except asyncio.CancelledError:
                # 保存最后一次
                await self._save_all_dirty()
                break
            except Exception as e:
                logger.error(f"保存循环异常: {e}")
    
    async def _cleanup_loop(self):
        """定期清理循环"""
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_idle_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"清理循环异常: {e}")
    
    async def create_session(self) -> GatewaySession:
        """创建新会话（生成 ULID）"""
        async with self._lock:
            ulid_str = ulid.ulid()
            session = GatewaySession(ulid=ulid_str)
            session_key = f"session_{ulid_str}"
            
            self._sessions[session_key] = session
            self._ulid_index[ulid_str] = session_key
            
            # 立即保存到数据库
            await self._save_to_db(session)
            
            return session
    
    async def get_or_create_session(self, ulid: Optional[str] = None) -> tuple[GatewaySession, bool]:
        """
        获取或创建会话
        
        如果提供 ulid 且存在，返回已有会话并标记活跃。
        否则创建新会话。
        
        Args:
            ulid: 会话 ULID（可选）
            
        Returns:
            (session, is_new): 会话对象和是否是新创建的
        """
        async with self._lock:
            if ulid:
                session_key = self._ulid_index.get(ulid)
                if session_key and session_key in self._sessions:
                    session = self._sessions[session_key]
                    session.last_active = datetime.now()
                    session.mark_dirty()
                    # 刷新数据库过期时间
                    self.db.touch_session(f"session_{ulid}")
                    return session, False
            
            # 创建新会话
            ulid_str = ulid or ulid.ulid()
            session = GatewaySession(ulid=ulid_str)
            session_key = f"session_{ulid_str}"
            
            self._sessions[session_key] = session
            self._ulid_index[ulid_str] = session_key
            
            # 立即保存到数据库
            await self._save_to_db(session)
            
            return session, True
    
    async def get_session(self, ulid: str) -> Optional[GatewaySession]:
        """
        获取会话
        
        Args:
            ulid: 会话 ULID
        
        Returns:
            GatewaySession 或 None
        """
        # 不在持有锁时调用数据库，避免死锁
        session_key = self._ulid_index.get(ulid)
        if session_key:
            return self._sessions.get(session_key)
        return None
    
    async def queue_task(self, ulid: str, content: str) -> tuple[SessionTask, bool]:
        """
        将任务加入队列
        
        Args:
            ulid: 会话 ULID
            content: 任务内容
            
        Returns:
            (task, is_first): 任务对象和是否是第一个任务
        """
        session, is_new = await self.get_or_create_session(ulid)
        
        if is_new or session.state == SessionState.IDLE:
            # 新会话或空闲状态，第一个任务
            task = SessionTask(ulid=ulid, content=content)
            session.current_task = task
            session.state = SessionState.PROCESSING
            session.mark_dirty()
            return task, True
        
        # 已有会话正在处理，加入队列
        task = session.add_task(content)
        session.state = SessionState.QUEUED
        return task, False
    
    async def complete_current_task(
        self, 
        ulid: str, 
        result: Any = None, 
        error: str = ""
    ) -> Optional[SessionTask]:
        """
        完成当前任务，处理队列中的下一个
        
        Args:
            ulid: 会话 ULID
            result: 任务结果
            error: 错误信息
        
        Returns:
            下一个任务对象（如果队列中还有任务），否则返回 None
        """
        async with self._lock:
            session = await self.get_session(ulid)
            if not session:
                return None
            
            # 标记当前任务完成
            if session.current_task:
                session.current_task.result = result
                session.current_task.error = error
                session.mark_dirty()
            
            # 获取下一个任务
            next_task = session.get_next_task()
            if next_task:
                session.current_task = next_task
                session.state = SessionState.PROCESSING
                session.mark_dirty()
                await self._save_to_db(session)
                return next_task
            else:
                session.current_task = None
                session.state = SessionState.IDLE
                session.mark_dirty()
                await self._save_to_db(session)
                return None
    
    async def cancel_task(self, ulid: str) -> bool:
        """
        取消会话当前任务
        
        Args:
            ulid: 会话 ULID
        
        Returns:
            是否成功
        """
        async with self._lock:
            session = await self.get_session(ulid)
            if not session:
                return False
            
            # 清空队列
            session.tasks.clear()
            session.current_task = None
            session.state = SessionState.IDLE
            session.mark_dirty()
            await self._save_to_db(session)
            
            return True
    
    async def get_queue_status(self, ulid: str) -> Dict[str, Any]:
        """
        获取队列状态
        
        Args:
            ulid: 会话 ULID
        
        Returns:
            队列状态字典
        """
        session = await self.get_session(ulid)
        if not session:
            return {"exists": False}
        
        return {
            "exists": True,
            "ulid": ulid,
            "state": session.state.value,
            "queue_length": len(session.tasks),
            "current_task": (
                session.current_task.content[:50] + "..." 
                if session.current_task and len(session.current_task.content) > 50 
                else session.current_task.content if session.current_task else None
            ),
            "last_active": session.last_active.isoformat()
        }
    
    async def list_sessions(self) -> List[Dict[str, Any]]:
        """
        列出所有会话
        
        Returns:
            会话列表
        """
        async with self._lock:
            return [
                {
                    "ulid": s.ulid,
                    "state": s.state.value,
                    "queue_length": len(s.tasks),
                    "current_task": (
                        s.current_task.content[:50] + "..." 
                        if s.current_task and len(s.current_task.content) > 50 
                        else s.current_task.content if s.current_task else None
                    ),
                    "last_active": s.last_active.isoformat(),
                    "created_at": s.created_at.isoformat()
                }
                for s in self._sessions.values()
            ]
    
    async def delete_session(self, ulid: str) -> bool:
        """
        删除会话
        
        Args:
            ulid: 会话 ULID
        
        Returns:
            是否成功
        """
        async with self._lock:
            session_key = self._ulid_index.get(ulid)
            if not session_key:
                return False
            
            # 从数据库删除
            self.db.delete_session(f"session_{ulid}")
            
            # 从内存删除
            if session_key in self._sessions:
                del self._sessions[session_key]
            del self._ulid_index[ulid]
            
            return True
    
    async def _cleanup_idle_sessions(self) -> int:
        """
        清理空闲会话
        
        删除超过指定天数未访问的会话。
        会同时通知 Gateway 清理会话。
        
        Returns:
            清理的会话数量
        """
        idle_seconds = self._max_idle_days * 86400  # 转换为秒
        cutoff = datetime.now() - timedelta(seconds=idle_seconds)
        
        async with self._lock:
            # 找出需要删除的会话
            to_delete = []
            for session in self._sessions.values():
                if session.last_active < cutoff:
                    to_delete.append(session.ulid)
            
            deleted_count = 0
            for ulid in to_delete:
                # 通知 Gateway 清理会话
                await self._notify_gateway_cleanup(ulid)
                
                # 从数据库删除
                self.db.delete_session(f"session_{ulid}")
                
                # 从内存删除
                session_key = self._ulid_index.get(ulid)
                if session_key and session_key in self._sessions:
                    del self._sessions[session_key]
                if ulid in self._ulid_index:
                    del self._ulid_index[ulid]
                
                deleted_count += 1
                logger.info(f"已清理空闲会话: {ulid}")
            
            if deleted_count > 0:
                logger.info(f"共清理了 {deleted_count} 个空闲会话（>{self._max_idle_days}天无访问）")
            
            return deleted_count
    
    async def _notify_gateway_cleanup(self, ulid: str):
        """
        通知 Gateway 清理会话
        
        Args:
            ulid: 会话 ULID
        """
        try:
            # 导入 gateway 避免循环依赖
            from .gateway import gateway_bridge
            session_key = f"session_{ulid}"
            gateway_bridge.clear_session(session_key)
            logger.debug(f"已通知 Gateway 清理会话: {session_key}")
        except ImportError:
            logger.warning("无法导入 gateway 模块，跳过 Gateway 会话清理")
        except Exception as e:
            logger.error(f"通知 Gateway 清理会话失败: {e}")
    
    async def shutdown(self):
        """关闭（保存所有变更）"""
        if self._save_task:
            self._save_task.cancel()
        if self._cleanup_task:
            self._cleanup_task.cancel()
        
        # 保存所有变更
        await self._save_all_dirty()
        
        logger.info("会话存储已关闭")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats = self.db.get_stats()
        stats["memory_sessions"] = len(self._sessions)
        return stats


# 全局实例（延迟初始化）
_session_store: Optional[SessionStore] = None


def get_session_store() -> SessionStore:
    """获取全局会话存储实例"""
    global _session_store
    if _session_store is None:
        _session_store = SessionStore()
    return _session_store


async def init_session_store() -> SessionStore:
    """初始化全局会话存储"""
    store = get_session_store()
    await store.initialize()
    return store


# 单例实例（供 main.py 导入使用）
session_store = get_session_store()
