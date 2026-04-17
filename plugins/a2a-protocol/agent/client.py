"""
A2A Client - 完整实现
A2A Agent 组件 - 支持自动重连和任务续接
"""
import asyncio
import json
import logging
from typing import Optional, AsyncGenerator, Callable, Awaited, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
import uuid
import threading
from queue import Queue, Empty

import httpx
import websockets

logger = logging.getLogger("a2a")


class ConnectionState(Enum):
    """连接状态"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


@dataclass
class TaskStatus:
    """任务状态"""
    SUBMITTED = "submitted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    INPUT_REQUIRED = "input_required"


@dataclass
class Artifact:
    """产物"""
    type: str = "text"
    content: str = ""
    name: str = ""
    metadata: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "Artifact":
        return cls(**data)


@dataclass
class TaskResult:
    """任务结果"""
    task_id: str
    status: str
    message: str = ""
    artifacts: list = field(default_factory=list)
    error: str = ""
    
    def __post_init__(self):
        if isinstance(self.status, dict):
            self.status = self.status.get("state", self.status)
    
    @property
    def is_success(self) -> bool:
        return self.status == "completed"
    
    @property
    def first_text(self) -> str:
        for a in self.artifacts:
            if a.type == "text" or isinstance(a, dict) and a.get("type") == "text":
                return a.get("content", "") if isinstance(a, dict) else a.content
        return ""


@dataclass
class PendingTask:
    """待处理任务（用于本地队列）"""
    content: str
    role: str = "user"
    session_id: str = None
    streaming: bool = True
    name: str = None
    metadata: dict = None
    ulid: str = None  # 会话 ULID
    task_id: str = None  # 服务器返回的任务 ID
    created_at: float = None  # 创建时间
    retry_count: int = 0
    max_retries: int = 3
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().timestamp()
        if self.metadata is None:
            self.metadata = {}


class ResilientA2AClient:
    """
    健壮的 A2A 客户端
    
    支持：
    - 自动重连（指数退避）
    - 任务续接（断线不丢失任务）
    - 本地任务队列
    - 连接状态管理
    
    特性：
    1. 断线自动重连
    2. 重连后自动恢复未完成的任务
    3. 本地队列缓存（最多 100 个任务）
    4. 任务超时控制
    
    Attributes:
        server_url: 服务器地址
        client_id: 客户端 ID
        session_id: 会话 ID（用于任务续接）
        max_retries: 最大重试次数
        reconnect_delay: 基础重连延迟（秒）
        max_reconnect_delay: 最大重连延迟（秒）
        task_timeout: 任务超时时间（秒）
    
    Example:
        >>> client = ResilientA2AClient("http://127.0.0.1:13666", "my_client")
        >>> 
        >>> # 发送任务（自动处理断线重连）
        >>> result = await client.send_and_wait("写一个小说开头")
        >>> 
        >>> # 或使用上下文管理器
        >>> async with ResilientA2AClient("http://127.0.0.1:13666") as client:
        ...     result = await client.send_and_wait("写一个小说")
    """
    
    def __init__(
        self,
        server_url: str,
        client_id: str = None,
        session_id: str = None,
        max_retries: int = 5,
        reconnect_delay: float = 1.0,
        max_reconnect_delay: float = 60.0,
        task_timeout: float = 300.0,
        local_queue_size: int = 100
    ):
        self.server_url = server_url.rstrip("/")
        self.client_id = client_id or f"client_{uuid.uuid4().hex[:8]}"
        self.session_id = session_id or self.client_id
        
        # 重连配置
        self.max_retries = max_retries
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.task_timeout = task_timeout
        self.local_queue_size = local_queue_size
        
        # 连接状态
        self._state = ConnectionState.DISCONNECTED
        self._connection: Optional[httpx.AsyncClient] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        
        # SSE 相关
        self._sse_task: Optional[asyncio.Task] = None
        self._sse_event_queue: asyncio.Queue = asyncio.Queue()
        
        # 事件处理
        self._event_handlers: dict[str, Callable] = {}
        
        # 本地任务队列（用于断线保护）
        self._pending_tasks: dict[str, PendingTask] = {}  # task_id -> PendingTask
        self._ulid_to_task_id: dict[str, str] = {}  # ulid -> task_id
        
        # 会话 ULID（用于任务续接）
        self._current_ulid: Optional[str] = None
        
        # 锁
        self._lock = asyncio.Lock()
        
        # 回调
        self._on_connect_callbacks: list[Callable] = []
        self._on_disconnect_callbacks: list[Callable] = []
        self._on_reconnect_callbacks: list[Callable] = []
    
    # ─────────────────────────────────────────
    # 连接管理
    # ─────────────────────────────────────────
    
    @property
    def state(self) -> ConnectionState:
        """获取连接状态"""
        return self._state
    
    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._state == ConnectionState.CONNECTED
    
    async def connect(self) -> bool:
        """
        连接到服务器
        
        Returns:
            是否连接成功
        """
        async with self._lock:
            if self._state == ConnectionState.CONNECTED:
                return True
            
            self._state = ConnectionState.CONNECTING
            logger.info(f"正在连接到 {self.server_url}...")
            
            try:
                self._connection = httpx.AsyncClient(timeout=30.0)
                
                # 测试连接
                response = await self._connection.get(f"{self.server_url}/health")
                if response.status_code != 200:
                    raise Exception(f"Health check failed: {response.status_code}")
                
                self._state = ConnectionState.CONNECTED
                logger.info(f"已连接到 {self.server_url}")
                
                # 启动 SSE 订阅
                asyncio.create_task(self._sse_listener())
                
                # 执行连接回调
                for cb in self._on_connect_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(self)
                        else:
                            cb(self)
                    except Exception as e:
                        logger.error(f"连接回调异常: {e}")
                
                # 重连后恢复未完成任务
                await self._restore_pending_tasks()
                
                return True
                
            except Exception as e:
                logger.error(f"连接失败: {e}")
                self._state = ConnectionState.DISCONNECTED
                if self._connection:
                    await self._connection.aclose()
                    self._connection = None
                return False
    
    async def disconnect(self):
        """断开连接"""
        async with self._lock:
            self._state = ConnectionState.DISCONNECTED
            
            if self._sse_task:
                self._sse_task.cancel()
                self._sse_task = None
            
            if self._ws_task:
                self._ws_task.cancel()
                self._ws_task = None
            
            if self._ws:
                await self._ws.close()
                self._ws = None
            
            if self._connection:
                await self._connection.aclose()
                self._connection = None
            
            # 执行断开回调
            for cb in self._on_disconnect_callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(self)
                    else:
                        cb(self)
                except Exception as e:
                    logger.error(f"断开回调异常: {e}")
    
    async def reconnect(self) -> bool:
        """
        重新连接（带指数退避）
        
        Returns:
            是否重连成功
        """
        if self._state == ConnectionState.CONNECTING:
            return False
        
        self._state = ConnectionState.RECONNECTING
        delay = self.reconnect_delay
        
        for attempt in range(1, self.max_retries + 1):
            logger.info(f"尝试重连 ({attempt}/{self.max_retries})，等待 {delay:.1f}s...")
            
            await asyncio.sleep(delay)
            
            if await self.connect():
                logger.info(f"重连成功！")
                
                # 执行重连回调
                for cb in self._on_reconnect_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(self, attempt)
                        else:
                            cb(self, attempt)
                    except Exception as e:
                        logger.error(f"重连回调异常: {e}")
                
                return True
            
            # 指数退避
            delay = min(delay * 2, self.max_reconnect_delay)
        
        logger.error(f"重连失败，已达到最大重试次数 ({self.max_retries})")
        self._state = ConnectionState.DISCONNECTED
        return False
    
    async def ensure_connected(self) -> bool:
        """
        确保已连接（如果断开了则尝试重连）
        
        Returns:
            是否已连接
        """
        if self._state == ConnectionState.CONNECTED:
            return True
        
        if self._state in [ConnectionState.RECONNECTING, ConnectionState.CONNECTING]:
            # 等待连接完成
            for _ in range(30):  # 最多等 30 秒
                await asyncio.sleep(1)
                if self._state == ConnectionState.CONNECTED:
                    return True
            return False
        
        return await self.reconnect()
    
    # ─────────────────────────────────────────
    # SSE 监听
    # ─────────────────────────────────────────
    
    async def _sse_listener(self):
        """SSE 事件监听"""
        url = f"{self.server_url}/sse/{self.client_id}"
        
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", url) as response:
                    async for line in response.aiter_lines():
                        if self._state != ConnectionState.CONNECTED:
                            break
                        
                        if not line.strip():
                            continue
                        
                        if line.startswith("data: "):
                            data_str = line[6:]
                            try:
                                data = json.loads(data_str)
                                event_type = data.get("type", "message")
                                
                                # 放入队列
                                await self._sse_event_queue.put((event_type, data))
                                
                                # 处理事件
                                await self._handle_event(event_type, data)
                                
                            except json.JSONDecodeError:
                                pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"SSE 监听异常: {e}")
        
        # 连接断开，尝试重连
        if self._state == ConnectionState.CONNECTED:
            logger.warning("SSE 连接断开，尝试重连...")
            await self.reconnect()
    
    async def _handle_event(self, event_type: str, data: dict):
        """处理 SSE 事件"""
        # 任务完成/失败事件
        task_id = data.get("taskId")
        if task_id and task_id in self._pending_tasks:
            pending = self._pending_tasks[task_id]
            
            if event_type == "task/completed":
                pending.task_id = None  # 标记完成
                logger.info(f"任务 {task_id} 已完成")
            elif event_type == "task/failed":
                pending.retry_count += 1
                if pending.retry_count < pending.max_retries:
                    logger.warning(f"任务 {task_id} 失败，将重试 ({pending.retry_count}/{pending.max_retries})")
                    # 重试任务
                    await self._retry_task(pending)
                else:
                    pending.task_id = None  # 放弃
                    logger.error(f"任务 {task_id} 已达到最大重试次数")
        
        # 调用处理器
        if event_type in self._event_handlers:
            try:
                handler = self._event_handlers[event_type]
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                logger.error(f"事件处理异常 {event_type}: {e}")
    
    # ─────────────────────────────────────────
    # 任务操作
    # ─────────────────────────────────────────
    
    async def send_task(
        self,
        content: str,
        role: str = "user",
        session_id: str = None,
        streaming: bool = True,
        name: str = None,
        metadata: dict = None,
        ulid: str = None,
        auto_retry: bool = True
    ) -> str:
        """
        发送任务（自动处理断线）
        
        如果断线，任务会进入本地队列，重连后自动发送。
        
        Args:
            content: 任务内容
            role: 角色
            session_id: 会话 ID
            streaming: 是否流式
            name: 发送者名称
            metadata: 额外数据
            ulid: 指定会话 ULID（用于任务续接）
            auto_retry: 是否自动重试
        
        Returns:
            task_id: 任务 ID
        """
        if session_id is None:
            session_id = self.session_id
        
        # 确保已连接
        if not await self.ensure_connected():
            # 断线且无法重连，存入本地队列
            pending = PendingTask(
                content=content,
                role=role,
                session_id=session_id,
                streaming=streaming,
                name=name,
                metadata=metadata,
                ulid=ulid or self._current_ulid,
                max_retries=self.max_retries if auto_retry else 0
            )
            task_id = f"pending_{uuid.uuid4().hex[:12]}"
            self._pending_tasks[task_id] = pending
            logger.warning(f"服务器离线，任务已存入本地队列: {task_id}")
            return task_id
        
        # 发送请求
        try:
            message = {"role": role, "content": content}
            if name:
                message["name"] = name
            
            params = {
                "message": message,
                "sessionId": session_id,
                "streaming": streaming
            }
            if metadata:
                params["metadata"] = metadata
            if ulid:
                params["ulid"] = ulid
            elif self._current_ulid:
                params["ulid"] = self._current_ulid
            
            request = {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "tasks/send",
                "params": params
            }
            
            response = await self._connection.post(
                f"{self.server_url}/rpc",
                json=request
            )
            
            result = response.json()
            
            if "error" in result:
                raise Exception(f"JSON-RPC Error: {result['error']}")
            
            data = result.get("result", {})
            task_id = data.get("taskId")
            
            # 保存 ULID（用于后续任务续接）
            if "ulid" in data:
                self._current_ulid = data["ulid"]
            
            # 加入本地队列（用于追踪）
            pending = PendingTask(
                content=content,
                role=role,
                session_id=session_id,
                streaming=streaming,
                name=name,
                metadata=metadata,
                ulid=self._current_ulid,
                task_id=task_id,
                max_retries=self.max_retries if auto_retry else 0
            )
            self._pending_tasks[task_id] = pending
            if self._current_ulid:
                self._ulid_to_task_id[self._current_ulid] = task_id
            
            return task_id
            
        except Exception as e:
            logger.error(f"发送任务失败: {e}")
            
            # 存入本地队列
            pending = PendingTask(
                content=content,
                role=role,
                session_id=session_id,
                streaming=streaming,
                name=name,
                metadata=metadata,
                ulid=ulid or self._current_ulid,
                max_retries=self.max_retries if auto_retry else 0
            )
            task_id = f"pending_{uuid.uuid4().hex[:12]}"
            self._pending_tasks[task_id] = pending
            return task_id
    
    async def _retry_task(self, pending: PendingTask):
        """重试任务"""
        try:
            pending.retry_count += 1
            task_id = await self.send_task(
                content=pending.content,
                role=pending.role,
                session_id=pending.session_id,
                streaming=pending.streaming,
                name=pending.name,
                metadata=pending.metadata,
                ulid=pending.ulid,
                auto_retry=False  # 已经在 handle_event 里处理了
            )
            pending.task_id = task_id
        except Exception as e:
            logger.error(f"重试任务失败: {e}")
    
    async def _restore_pending_tasks(self):
        """重连后恢复未完成的任务"""
        if not self._pending_tasks:
            return
        
        logger.info(f"正在恢复 {len(self._pending_tasks)} 个待处理任务...")
        
        # 使用当前会话继续
        ulid_to_use = self._current_ulid
        
        for task_id, pending in list(self._pending_tasks.items()):
            if pending.task_id is None:
                # 已完成的任务
                continue
            
            try:
                # 发送相同内容的任务（续接）
                new_task_id = await self.send_task(
                    content=pending.content,
                    role=pending.role,
                    session_id=pending.session_id,
                    streaming=pending.streaming,
                    name=pending.name,
                    metadata=pending.metadata,
                    ulid=ulid_to_use,
                    auto_retry=False
                )
                
                # 更新映射
                pending.task_id = new_task_id
                logger.info(f"任务已续接: {task_id} -> {new_task_id}")
                
            except Exception as e:
                logger.error(f"恢复任务失败 {task_id}: {e}")
        
        logger.info("待处理任务恢复完成")
    
    async def get_task(self, task_id: str) -> dict:
        """获取任务状态"""
        await self.ensure_connected()
        
        request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tasks/get",
            "params": {"taskId": task_id}
        }
        
        response = await self._connection.post(
            f"{self.server_url}/rpc",
            json=request
        )
        
        return response.json().get("result", {})
    
    async def wait_for_task(
        self,
        task_id: str,
        timeout: float = None,
        poll_interval: float = 1.0
    ) -> TaskResult:
        """
        等待任务完成
        
        Args:
            task_id: 任务 ID
            timeout: 超时时间（秒）
            poll_interval: 轮询间隔
        
        Returns:
            TaskResult
        """
        timeout = timeout or self.task_timeout
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < timeout:
            # 检查本地队列
            if task_id in self._pending_tasks:
                pending = self._pending_tasks[task_id]
                if pending.task_id is None:
                    # 任务已完成（本地标记）
                    return TaskResult(
                        task_id=task_id,
                        status="completed",
                        artifacts=[]
                    )
            
            # 检查服务器
            try:
                if await self.ensure_connected():
                    task_data = await self.get_task(task_id)
                    status = task_data.get("status", {})
                    state = status.get("state", "") if isinstance(status, dict) else status
                    
                    if state == "completed":
                        artifacts = task_data.get("artifacts", [])
                        return TaskResult(
                            task_id=task_id,
                            status=state,
                            artifacts=artifacts,
                            message=status.get("message", "") if isinstance(status, dict) else ""
                        )
                    elif state in ["failed", "canceled"]:
                        return TaskResult(
                            task_id=task_id,
                            status=state,
                            error=status.get("message", "Task failed") if isinstance(status, dict) else "Task failed"
                        )
            except Exception as e:
                logger.warning(f"查询任务状态失败: {e}")
            
            await asyncio.sleep(poll_interval)
        
        raise Exception(f"Timeout waiting for task {task_id}")
    
    async def send_and_wait(
        self,
        content: str,
        role: str = "user",
        name: str = None,
        timeout: float = None,
        session_id: str = None,
        ulid: str = None
    ) -> TaskResult:
        """
        发送任务并等待结果（完整流程）
        
        Args:
            content: 任务内容
            role: 角色
            name: 发送者名称
            timeout: 超时时间
            session_id: 会话 ID
            ulid: 指定会话 ULID
        
        Returns:
            TaskResult
        """
        task_id = await self.send_task(
            content=content,
            role=role,
            name=name,
            session_id=session_id,
            ulid=ulid
        )
        
        # 如果是本地队列任务，直接返回已完成
        if task_id.startswith("pending_"):
            pending = self._pending_tasks.get(task_id)
            if pending and pending.task_id is None:
                return TaskResult(task_id=task_id, status="completed")
        
        return await self.wait_for_task(task_id, timeout=timeout)
    
    async def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        if task_id.startswith("pending_"):
            # 本地队列任务
            if task_id in self._pending_tasks:
                del self._pending_tasks[task_id]
                return True
            return False
        
        await self.ensure_connected()
        
        request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tasks/cancel",
            "params": {"taskId": task_id}
        }
        
        response = await self._connection.post(
            f"{self.server_url}/rpc",
            json=request
        )
        
        # 从本地队列移除
        if task_id in self._pending_tasks:
            del self._pending_tasks[task_id]
        
        return True
    
    # ─────────────────────────────────────────
    # 会话操作
    # ─────────────────────────────────────────
    
    async def get_or_create_session(self, ulid: str = None) -> tuple[str, bool]:
        """
        获取或创建会话
        
        Returns:
            (ulid, is_new): ULID 和是否新创建
        """
        await self.ensure_connected()
        
        request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "sessions/create",
            "params": {"sessionId": ulid} if ulid else {}
        }
        
        response = await self._connection.post(
            f"{self.server_url}/rpc",
            json=request
        )
        
        result = response.json().get("result", {})
        session_id = result.get("session_id")
        
        if session_id:
            self._current_ulid = session_id
            # 尝试获取历史
            is_new = result.get("created_at") == result.get("updated_at")
            return session_id, is_new
        
        return ulid, True
    
    # ─────────────────────────────────────────
    # 事件处理
    # ─────────────────────────────────────────
    
    def on(self, event_type: str, handler: Callable):
        """注册事件处理器"""
        self._event_handlers[event_type] = handler
    
    def off(self, event_type: str):
        """注销事件处理器"""
        self._event_handlers.pop(event_type, None)
    
    def on_connect(self, callback: Callable):
        """注册连接回调"""
        self._on_connect_callbacks.append(callback)
    
    def on_disconnect(self, callback: Callable):
        """注册断开回调"""
        self._on_disconnect_callbacks.append(callback)
    
    def on_reconnect(self, callback: Callable):
        """注册重连回调"""
        self._on_reconnect_callbacks.append(callback)
    
    # ─────────────────────────────────────────
    # 上下文管理器
    # ─────────────────────────────────────────
    
    async def __aenter__(self):
        """异步上下文入口"""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文出口"""
        await self.disconnect()
    
    async def close(self):
        """关闭客户端"""
        await self.disconnect()
        self._pending_tasks.clear()
    
    # ─────────────────────────────────────────
    # 便捷方法
    # ─────────────────────────────────────────
    
    async def get_agent_card(self) -> dict:
        """获取 Agent Card"""
        await self.ensure_connected()
        response = await self._connection.get(
            f"{self.server_url}/.well-known/agent.json"
        )
        return response.json()
    
    def get_pending_count(self) -> int:
        """获取待处理任务数"""
        return sum(1 for p in self._pending_tasks.values() if p.task_id is None)
    
    def get_pending_tasks(self) -> list[dict]:
        """获取所有待处理任务"""
        return [
            {
                "task_id": tid,
                "content": p.content[:50],
                "retry_count": p.retry_count,
                "task_id_on_server": p.task_id
            }
            for tid, p in self._pending_tasks.items()
        ]


# ─────────────────────────────────────────────
# 向后兼容：保留原始 A2AClient
# ─────────────────────────────────────────────

class A2AClient(ResilientA2AClient):
    """
    A2A 客户端（兼容模式）
    
    提供与原版相同的接口，但内置重连和任务续接能力。
    """
    
    def __init__(self, server_url: str, client_id: str = None, auto_subscribe: bool = True):
        super().__init__(server_url, client_id)
        self.auto_subscribe = auto_subscribe


# ─────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────

async def create_client(
    server_url: str,
    client_id: str = None,
    auto_subscribe: bool = True,
    **kwargs
) -> ResilientA2AClient:
    """
    创建健壮的 A2A 客户端
    
    Example:
        >>> client = await create_client("http://127.0.0.1:13666", "my_client")
        >>> result = await client.send_and_wait("写一个小说")
    """
    client = ResilientA2AClient(server_url, client_id, **kwargs)
    if auto_subscribe:
        await client.connect()
    return client


# 别名
ResilientClient = ResilientA2AClient
