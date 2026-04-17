"""
会话管理器 - ULID 会话 + 任务队列
"""
import ulid
import asyncio
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SessionState(Enum):
    """会话状态"""
    IDLE = "idle"           # 空闲，等待新任务
    PROCESSING = "processing"  # 正在处理任务
    QUEUED = "queued"       # 排队中


@dataclass
class SessionTask:
    """会话任务"""
    ulid: str
    content: str
    created_at: datetime = field(default_factory=datetime.now)
    future: Optional[asyncio.Future] = None
    result: Any = None
    error: str = ""
    a2a_task_id: Optional[str] = None  # 关联的 A2A 任务 ID


@dataclass 
class GatewaySession:
    """Gateway 会话"""
    ulid: str
    state: SessionState = SessionState.IDLE
    tasks: List[SessionTask] = field(default_factory=list)
    current_task: Optional[SessionTask] = None
    created_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)
    
    def add_task(self, content: str) -> SessionTask:
        """添加任务到队列"""
        task = SessionTask(ulid=self.ulid, content=content)
        self.tasks.append(task)
        self.last_active = datetime.now()
        return task
    
    def get_next_task(self) -> Optional[SessionTask]:
        """获取下一个待处理任务"""
        if self.tasks:
            return self.tasks.pop(0)
        return None


class SessionStore:
    """会话存储管理"""
    
    def __init__(self):
        self.sessions: Dict[str, GatewaySession] = {}
        self.ulid_to_session: Dict[str, str] = {}  # ulid -> session_key
        self._lock = asyncio.Lock()
    
    async def create_session(self) -> GatewaySession:
        """创建新会话（生成 ULID）"""
        async with self._lock:
            ulid_str = ulid.ulid()
            session_key = f"session_{ulid_str}"
            session = GatewaySession(ulid=ulid_str)
            self.sessions[session_key] = session
            self.ulid_to_session[ulid_str] = session_key
            return session
    
    async def get_or_create_session(self, ulid: Optional[str] = None) -> tuple[GatewaySession, bool]:
        """
        获取或创建会话
        
        Args:
            ulid: 如果提供，则尝试获取已有会话；否则创建新会话
            
        Returns:
            (session, is_new): 会话对象和是否是新创建的
        """
        async with self._lock:
            if ulid:
                session_key = self.ulid_to_session.get(ulid)
                if session_key and session_key in self.sessions:
                    session = self.sessions[session_key]
                    session.last_active = datetime.now()
                    return session, False
            
            # 创建新会话
            ulid_str = ulid or ulid.ulid()
            session_key = f"session_{ulid_str}"
            session = GatewaySession(ulid=ulid_str)
            self.sessions[session_key] = session
            self.ulid_to_session[ulid_str] = session_key
            return session, True
    
    async def get_session(self, ulid: str) -> Optional[GatewaySession]:
        """获取会话（注意：不要在持有锁时调用，会死锁）"""
        session_key = self.ulid_to_session.get(ulid)
        if session_key:
            return self.sessions.get(session_key)
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
        
        if is_new:
            # 新会话，第一个任务
            task = SessionTask(ulid=ulid, content=content)
            session.current_task = task
            session.state = SessionState.PROCESSING
            # 注意：不加入队列，直接作为 current_task
            return task, True
        
        # 已有会话，检查状态
        if session.state == SessionState.IDLE:
            # 空闲状态，可以立即处理
            task = SessionTask(ulid=ulid, content=content)
            session.current_task = task
            session.state = SessionState.PROCESSING
            return task, True
        else:
            # 正在处理或已排队，加入队列
            task = SessionTask(ulid=ulid, content=content)
            session.tasks.append(task)
            session.state = SessionState.QUEUED
            return task, False
    
    async def complete_current_task(self, ulid: str, result: Any = None, error: str = "") -> Optional[SessionTask]:
        """完成当前任务，处理队列中的下一个
        
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
            
            # 获取下一个任务
            next_task = session.get_next_task()
            if next_task:
                session.current_task = next_task
                session.state = SessionState.PROCESSING
                return next_task
            else:
                session.current_task = None
                session.state = SessionState.IDLE
                return None
    
    async def get_queue_status(self, ulid: str) -> Dict[str, Any]:
        """获取队列状态"""
        session = await self.get_session(ulid)
        if not session:
            return {"exists": False}
        
        return {
            "exists": True,
            "ulid": ulid,
            "state": session.state.value,
            "queue_length": len(session.tasks),
            "current_task": session.current_task.content[:50] + "..." if session.current_task else None,
        }
    
    async def list_sessions(self) -> List[Dict[str, Any]]:
        """列出所有会话"""
        async with self._lock:
            return [
                {
                    "ulid": s.ulid,
                    "state": s.state.value,
                    "queue_length": len(s.tasks),
                    "current_task": s.current_task.content[:50] + "..." if s.current_task else None,
                    "last_active": s.last_active.isoformat(),
                }
                for s in self.sessions.values()
            ]


# 全局实例
session_store = SessionStore()
