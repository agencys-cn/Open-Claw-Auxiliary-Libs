"""
会话管理
A2A 协议组件

概述:
    管理客户端会话，维护会话与任务的关联。
    会话用于保持上下文，支持多任务串行处理。

示例:
    >>> session = session_store.create("client-1")
    >>> session.add_task("task-123")
    >>> tasks = session.task_ids
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, List, Dict
from datetime import datetime
import uuid

logger = logging.getLogger("a2a")


@dataclass
class Session:
    """
    会话
    
    代表一个客户端的会话上下文。
    
    Attributes:
        id: 会话唯一标识符
        task_ids: 关联的任务 ID 列表
        created_at: 创建时间
        updated_at: 更新时间
        metadata: 额外元数据
    """
    id: str = ""
    task_ids: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """初始化后处理"""
        if not self.id:
            self.id = f"session_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        """从字典创建"""
        return cls(**data)
    
    def add_task(self, task_id: str) -> None:
        """
        添加任务
        
        Args:
            task_id: 任务 ID
        """
        if task_id not in self.task_ids:
            self.task_ids.append(task_id)
            self.updated_at = datetime.now().isoformat()
    
    def remove_task(self, task_id: str) -> bool:
        """
        移除任务
        
        Args:
            task_id: 任务 ID
        
        Returns:
            是否成功移除
        """
        if task_id in self.task_ids:
            self.task_ids.remove(task_id)
            self.updated_at = datetime.now().isoformat()
            return True
        return False
    
    @property
    def task_count(self) -> int:
        """任务数量"""
        return len(self.task_ids)


class SessionStore:
    """
    会话存储
    
    管理所有会话。
    
    Example:
        >>> store = SessionStore()
        >>> session = store.create("client-1")
        >>> 
        >>> retrieved = store.get("session_xxx")
        >>> print(retrieved.task_count)
    """
    
    def __init__(self):
        """初始化存储"""
        self._sessions: Dict[str, Session] = {}
    
    def create(self, session_id: str = None, metadata: Dict[str, Any] = None) -> Session:
        """
        创建会话
        
        Args:
            session_id: 指定会话 ID（可选）
            metadata: 额外元数据
        
        Returns:
            会话对象
        """
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        
        session = Session(id=session_id or "", metadata=metadata or {})
        self._sessions[session.id] = session
        logger.debug(f"Session 创建: {session.id}")
        return session
    
    def get(self, session_id: str) -> Optional[Session]:
        """
        获取会话
        
        Args:
            session_id: 会话 ID
        
        Returns:
            会话对象
        """
        return self._sessions.get(session_id)
    
    def update(self, session: Session) -> None:
        """
        更新会话
        
        Args:
            session: 会话对象
        """
        self._sessions[session.id] = session
    
    def delete(self, session_id: str) -> bool:
        """
        删除会话
        
        Args:
            session_id: 会话 ID
        
        Returns:
            是否成功删除
        """
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False
    
    def clear(self) -> None:
        """清空所有会话"""
        self._sessions.clear()
    
    def list_active(self) -> List[Session]:
        """
        列出所有活跃会话
        
        Returns:
            会话列表
        """
        return list(self._sessions.values())
    
    def __len__(self) -> int:
        """会话数量"""
        return len(self._sessions)
    
    def __contains__(self, session_id: str) -> bool:
        """检查会话是否存在"""
        return session_id in self._sessions


# 全局会话存储
session_store = SessionStore()
