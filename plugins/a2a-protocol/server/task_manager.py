"""
任务状态机
A2A 协议核心组件

概述:
    管理任务的完整生命周期，包括状态转换、消息历史、产物管理等。
    任务状态机遵循标准 A2A 协议规范。

状态流转:
    submitted → in_progress → completed
                              ↓
                          failed
                              ↓
                          canceled
                                    ↓
                          input_required（需要更多信息）

示例:
    >>> task = task_store.create(session_id="session-1")
    >>> task.add_message("user", "写一个小说")
    >>> task.transition(TaskState.IN_PROGRESS, "开始处理")
    >>> # ... 处理中 ...
    >>> task.transition(TaskState.COMPLETED, "完成")
    >>> task.add_artifact(Artifact(type="text", content="小说内容..."))
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, List, Dict
from datetime import datetime
from enum import Enum
import uuid

logger = logging.getLogger("a2a")


class TaskState(str, Enum):
    """
    任务状态枚举
    
    状态说明:
        SUBMITTED: 任务已提交，等待处理
        QUEUED: 任务排队中，等待执行
        IN_PROGRESS: 任务正在处理中
        COMPLETED: 任务成功完成
        FAILED: 任务执行失败
        CANCELED: 任务被取消
        INPUT_REQUIRED: 任务需要更多输入信息
    """
    SUBMITTED = "submitted"                 # 已提交
    QUEUED = "queued"                     # 排队中
    IN_PROGRESS = "in_progress"           # 处理中
    COMPLETED = "completed"               # 已完成
    FAILED = "failed"                     # 失败
    CANCELED = "canceled"                 # 已取消
    INPUT_REQUIRED = "input_required"     # 需要输入


@dataclass
class Message:
    """
    消息记录
    
    记录任务中的每条消息，包括用户消息和助手回复。
    
    Attributes:
        role: 消息角色 ("user", "assistant", "system")
        content: 消息内容
        timestamp: 创建时间（ISO 8601 格式）
    """
    role: str
    content: str
    timestamp: str = ""
    
    def __post_init__(self):
        """自动设置时间戳"""
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, str]:
        """转换为字典"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "Message":
        """从字典创建消息"""
        return cls(**data)


@dataclass
class Artifact:
    """
    产物/结果
    
    任务完成时产生的结果，可以是文本、图片、文件等。
    
    Attributes:
        type: 产物类型 ("text", "image", "audio", "video", "file")
        content: 产物内容
        name: 产物名称（可选）
        description: 产物描述（可选）
        metadata: 额外元数据
    """
    type: str = "text"
    content: str = ""
    name: str = ""
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Artifact":
        """从字典创建产物"""
        return cls(**data)


@dataclass
class TaskStatus:
    """
    任务状态信息
    
    包含当前状态和相关消息。
    
    Attributes:
        state: 任务状态
        message: 状态说明消息
        timestamp: 状态更新时间
    """
    state: TaskState = TaskState.SUBMITTED
    message: str = ""
    timestamp: str = ""
    
    def __post_init__(self):
        """自动设置时间戳"""
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, str]:
        """转换为字典"""
        return {
            "state": self.state.value if isinstance(self.state, TaskState) else self.state,
            "message": self.message,
            "timestamp": self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "TaskStatus":
        """从字典创建状态"""
        state = TaskState(data.get("state", "submitted"))
        return cls(
            state=state,
            message=data.get("message", ""),
            timestamp=data.get("timestamp", "")
        )


@dataclass
class Task:
    """
    任务
    
    代表一个完整的工作单元，包含消息历史、产物和状态信息。
    
    Attributes:
        id: 任务唯一标识符（自动生成，格式: task_xxxxxxxxxxxx）
        session_id: 关联的会话 ID
        messages: 消息历史列表
        artifacts: 产物列表
        status: 当前状态
        created_at: 创建时间
        updated_at: 更新时间
        completed_at: 完成时间
        error: 错误信息（如果失败）
        metadata: 额外元数据
        timeout_seconds: 任务超时时间（秒），默认 0 表示不超时
        cancel_event: 取消事件（asyncio.Event）
    
    Example:
        >>> task = Task(session_id="session-1")
        >>> task.add_message("user", "写一个小说")
        >>> task.transition(TaskState.IN_PROGRESS, "开始处理")
        >>> # ... 处理完成后 ...
        >>> task.transition(TaskState.COMPLETED, "完成")
        >>> task.add_artifact(Artifact(type="text", content="小说第一章..."))
    """
    id: str = ""
    session_id: str = ""
    messages: List[Message] = field(default_factory=list)
    artifacts: List[Artifact] = field(default_factory=list)
    status: TaskStatus = field(default_factory=TaskStatus)
    created_at: str = ""
    updated_at: str = ""
    completed_at: str = ""
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 0  # 默认 0 表示不超时，只要 Gateway 在执行就一直等
    
    # 运行时状态（不序列化）
    _cancel_event: Any = field(default=None, repr=False)
    
    def __post_init__(self):
        """初始化后处理，自动生成 ID 和时间"""
        if not self.id:
            self.id = f"task_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        if isinstance(self.status, dict):
            self.status = TaskStatus.from_dict(self.status)
        # 初始化取消事件（运行时）
        if self._cancel_event is None:
            self._cancel_event = asyncio.Event()
    
    @property
    def cancel_event(self) -> Any:
        """获取取消事件"""
        return self._cancel_event
    
    def cancel(self) -> None:
        """
        取消任务
        
        设置取消事件，通知执行中的任务立即停止。
        """
        if self._cancel_event:
            self._cancel_event.set()
            logger.info(f"Task {self.id} 取消事件已触发")
    
    @property
    def is_canceled(self) -> bool:
        """是否已取消"""
        return self._cancel_event.is_set() if self._cancel_event else False
    
    @property
    def is_expired(self) -> bool:
        """是否已超时"""
        if self.timeout_seconds <= 0:
            return False
        created = datetime.fromisoformat(self.created_at)
        elapsed = (datetime.now() - created).total_seconds()
        return elapsed > self.timeout_seconds
    
    def __post_init__(self):
        """初始化后处理，自动生成 ID 和时间"""
        if not self.id:
            self.id = f"task_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        if isinstance(self.status, dict):
            self.status = TaskStatus.from_dict(self.status)
    
    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典
        
        Returns:
            包含所有字段的字典
        """
        data = asdict(self)
        data["status"] = self.status.to_dict()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """
        从字典创建任务
        
        Args:
            data: 包含任务数据的字典
        
        Returns:
            Task 实例
        """
        if "status" in data:
            data["status"] = TaskStatus.from_dict(data["status"])
        if "messages" in data:
            data["messages"] = [Message.from_dict(m) if isinstance(m, dict) else m for m in data["messages"]]
        if "artifacts" in data:
            data["artifacts"] = [Artifact.from_dict(a) if isinstance(a, dict) else a for a in data["artifacts"]]
        return cls(**data)
    
    def add_message(self, role: str, content: str) -> None:
        """
        添加消息到历史
        
        Args:
            role: 消息角色 ("user", "assistant", "system")
            content: 消息内容
        """
        self.messages.append(Message(role=role, content=content))
        self.updated_at = datetime.now().isoformat()
    
    def add_artifact(self, artifact: Artifact) -> None:
        """
        添加产物
        
        Args:
            artifact: 产物对象
        """
        self.artifacts.append(artifact)
        self.updated_at = datetime.now().isoformat()
    
    def transition(self, new_state: TaskState, message: str = "") -> bool:
        """
        状态转换
        
        执行状态转换，仅在转换有效时更新状态。
        
        Args:
            new_state: 目标状态
            message: 状态说明
        
        Returns:
            转换是否成功
        
        Valid Transitions:
            SUBMITTED → IN_PROGRESS, QUEUED, CANCELED
            QUEUED → IN_PROGRESS, CANCELED
            IN_PROGRESS → COMPLETED, FAILED, INPUT_REQUIRED, CANCELED
            INPUT_REQUIRED → IN_PROGRESS, CANCELED
            COMPLETED → (terminal)
            FAILED → (terminal)
            CANCELED → (terminal)
        """
        current = self.status.state
        valid_transitions = {
            TaskState.SUBMITTED: [TaskState.IN_PROGRESS, TaskState.QUEUED, TaskState.CANCELED],
            TaskState.QUEUED: [TaskState.IN_PROGRESS, TaskState.CANCELED],
            TaskState.IN_PROGRESS: [TaskState.COMPLETED, TaskState.FAILED, TaskState.INPUT_REQUIRED, TaskState.CANCELED],
            TaskState.INPUT_REQUIRED: [TaskState.IN_PROGRESS, TaskState.CANCELED],
            TaskState.COMPLETED: [],
            TaskState.FAILED: [],
            TaskState.CANCELED: [],
        }
        
        if new_state in valid_transitions.get(current, []):
            old_state = current
            self.status = TaskStatus(state=new_state, message=message)
            self.updated_at = datetime.now().isoformat()
            
            if new_state in [TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED]:
                self.completed_at = datetime.now().isoformat()
            
            logger.info(f"Task {self.id}: {old_state.value} → {new_state.value}")
            return True
        else:
            logger.warning(f"Task {self.id}: 无效状态转换 {current.value} → {new_state.value}")
            return False
    
    @property
    def is_terminal(self) -> bool:
        """是否是终态（已完成/失败/取消）"""
        return self.status.state in [TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED]
    
    @property
    def is_active(self) -> bool:
        """是否处于活跃状态（处理中/需要输入）"""
        return self.status.state in [TaskState.IN_PROGRESS, TaskState.INPUT_REQUIRED]


class TaskStore:
    """
    任务存储
    
    内存中的任务存储，支持创建、查询、更新、删除操作。
    
    Attributes:
        _tasks: 任务字典，key 为 task_id
    
    Example:
        >>> store = TaskStore()
        >>> task = store.create(session_id="session-1")
        >>> task_id = task.id
        >>> 
        >>> # 稍后查询
        >>> retrieved = store.get(task_id)
        >>> print(retrieved.status.state)
    """
    
    def __init__(self):
        """初始化存储"""
        self._tasks: Dict[str, Task] = {}
    
    def create(self, session_id: str = "", metadata: Dict[str, Any] = None) -> Task:
        """
        创建新任务
        
        Args:
            session_id: 会话 ID（可选）
            metadata: 额外元数据（可选）
        
        Returns:
            创建的任务对象
        """
        task = Task(session_id=session_id, metadata=metadata or {})
        self._tasks[task.id] = task
        logger.debug(f"Task 创建: {task.id}")
        return task
    
    def get(self, task_id: str) -> Optional[Task]:
        """
        获取任务
        
        Args:
            task_id: 任务 ID
        
        Returns:
            任务对象，如果不存在返回 None
        """
        return self._tasks.get(task_id)
    
    def update(self, task: Task) -> None:
        """
        更新任务
        
        Args:
            task: 任务对象
        """
        self._tasks[task.id] = task
    
    def delete(self, task_id: str) -> bool:
        """
        删除任务
        
        Args:
            task_id: 任务 ID
        
        Returns:
            是否成功删除
        """
        if task_id in self._tasks:
            del self._tasks[task_id]
            logger.debug(f"Task 删除: {task_id}")
            return True
        return False
    
    def list_by_session(self, session_id: str) -> List[Task]:
        """
        列出会话的所有任务
        
        Args:
            session_id: 会话 ID
        
        Returns:
            任务列表
        """
        return [t for t in self._tasks.values() if t.session_id == session_id]
    
    def list_by_state(self, state: TaskState) -> List[Task]:
        """
        按状态列出任务
        
        Args:
            state: 任务状态
        
        Returns:
            任务列表
        """
        return [t for t in self._tasks.values() if t.status.state == state]
    
    def list_active(self) -> List[Task]:
        """
        列出所有活跃任务
        
        Returns:
            任务列表
        """
        return [t for t in self._tasks.values() if t.is_active]
    
    def clear(self) -> None:
        """清空所有任务"""
        self._tasks.clear()
        logger.debug("任务存储已清空")
    
    def __len__(self) -> int:
        """返回任务数量"""
        return len(self._tasks)
    
    def __contains__(self, task_id: str) -> bool:
        """检查任务是否存在"""
        return task_id in self._tasks


# 全局任务存储实例
task_store = TaskStore()
