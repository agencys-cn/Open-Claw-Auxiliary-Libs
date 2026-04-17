"""
A2A Client
A2A Agent 组件 - 完整的 A2A 客户端实现
"""
import asyncio
import json
import logging
from typing import Optional, AsyncGenerator, Callable, Awaitable, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
import uuid
import threading
from queue import Queue, Empty

import httpx
import websockets

logger = logging.getLogger("a2a")


class TaskStatus(str, Enum):
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
        return self.status == TaskStatus.COMPLETED.value
    
    @property
    def first_text(self) -> str:
        """获取第一个文本产物"""
        for a in self.artifacts:
            if a.type == "text" or isinstance(a, dict) and a.get("type") == "text":
                return a.get("content", "") if isinstance(a, dict) else a.content
        return ""


class A2AClient:
    """
    A2A 客户端 - 完整实现
    
    支持：
    - JSON-RPC 调用
    - SSE 订阅
    - WebSocket 双向通信
    - 任务状态追踪
    """
    
    def __init__(
        self,
        server_url: str,
        client_id: str = None,
        auto_subscribe: bool = True
    ):
        self.server_url = server_url.rstrip("/")
        self.client_id = client_id or f"client_{uuid.uuid4().hex[:8]}"
        self.auto_subscribe = auto_subscribe
        
        # SSE 相关
        self._sse_thread: Optional[threading.Thread] = None
        self._sse_queue: Queue = Queue()
        self._sse_running = False
        
        # WebSocket 相关
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_task: Optional[asyncio.Task] = None
        
        # 事件处理器
        self._event_handlers: dict[str, Callable] = {}
        
        # 已订阅的任务
        self._subscribed_tasks: dict[str, asyncio.Future] = {}
    
    # ─────────────────────────────────────────
    # JSON-RPC 调用
    # ─────────────────────────────────────────
    def _create_request(self, method: str, params: dict = None) -> dict:
        """创建 JSON-RPC 请求"""
        return {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params or {}
        }
    
    async def _rpc_call(self, method: str, params: dict = None) -> dict:
        """发送 JSON-RPC 请求"""
        request = self._create_request(method, params)
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.server_url}/rpc",
                json=request
            )
            
            if response.status_code != 200:
                raise Exception(f"HTTP {response.status_code}: {response.text}")
            
            result = response.json()
            
            if "error" in result:
                raise Exception(f"JSON-RPC Error: {result['error']}")
            
            return result.get("result", {})
    
    # ─────────────────────────────────────────
    # Agent 发现
    # ─────────────────────────────────────────
    async def get_agent_card(self) -> dict:
        """获取 Agent Card"""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{self.server_url}/.well-known/agent.json"
            )
            if response.status_code == 200:
                return response.json()
            raise Exception(f"Agent not found: {response.status_code}")
    
    async def list_agents(self) -> list:
        """列出所有 Agent"""
        result = await self._rpc_call("agents/list")
        return result.get("agents", [])
    
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
        metadata: dict = None
    ) -> str:
        """
        发送任务
        
        Returns:
            task_id: 任务 ID
        """
        message = {
            "role": role,
            "content": content
        }
        if name:
            message["name"] = name
        
        params = {
            "message": message,
            "sessionId": session_id or self.client_id,
            "streaming": streaming
        }
        if metadata:
            params["metadata"] = metadata
        
        result = await self._rpc_call("tasks/send", params)
        task_id = result.get("taskId")
        
        if self.auto_subscribe and task_id:
            asyncio.create_task(self._subscribe_task(task_id))
        
        return task_id
    
    async def get_task(self, task_id: str) -> dict:
        """获取任务状态"""
        return await self._rpc_call("tasks/get", {"taskId": task_id})
    
    async def cancel_task(self, task_id: str) -> dict:
        """取消任务"""
        return await self._rpc_call("tasks/cancel", {"taskId": task_id})
    
    async def get_task_history(self, task_id: str, limit: int = 10) -> list:
        """获取任务历史"""
        result = await self._rpc_call("tasks/history", {"taskId": task_id, "limit": limit})
        return result.get("messages", [])
    
    # ─────────────────────────────────────────
    # 会话操作
    # ─────────────────────────────────────────
    async def create_session(self, session_id: str = None, metadata: dict = None) -> dict:
        """创建会话"""
        params = {"metadata": metadata or {}}
        if session_id:
            params["sessionId"] = session_id
        return await self._rpc_call("sessions/create", params)
    
    async def get_session(self, session_id: str) -> dict:
        """获取会话"""
        return await self._rpc_call("sessions/get", {"sessionId": session_id})
    
    async def get_session_history(self, session_id: str, limit: int = 10) -> list:
        """获取会话历史"""
        result = await self._rpc_call("sessions/history", {"sessionId": session_id, "limit": limit})
        return result.get("messages", [])
    
    # ─────────────────────────────────────────
    # Gateway 操作
    # ─────────────────────────────────────────
    async def gateway_execute(self, content: str, name: str = None) -> str:
        """直接通过 Gateway 执行（不创建任务）"""
        result = await self._rpc_call("gateway/execute", {
            "content": content,
            "sessionId": self.client_id,
            "name": name or self.client_id
        })
        return result.get("result", "")
    
    # ─────────────────────────────────────────
    # SSE 订阅
    # ─────────────────────────────────────────
    async def subscribe_sse(self, client_id: str = None):
        """启动 SSE 订阅"""
        subscribe_id = client_id or self.client_id
        
        async def sse_listener():
            import sseclient
            import requests
            
            self._sse_running = True
            url = f"{self.server_url}/sse/{subscribe_id}"
            
            try:
                response = requests.get(url, stream=True)
                client = sseclient.SSEClient(response)
                
                for event in client.events():
                    if not self._sse_running:
                        break
                    
                    try:
                        data = json.loads(event.data)
                        event_type = event.event
                        
                        # 放入队列
                        self._sse_queue.put((event_type, data))
                        
                        # 调用处理器
                        await self._dispatch_event(event_type, data)
                        
                    except json.JSONDecodeError:
                        pass
                        
            except Exception as e:
                logger.error(f"SSE 监听异常: {e}")
            finally:
                self._sse_running = False
        
        asyncio.create_task(sse_listener())
    
    async def _dispatch_event(self, event_type: str, data: dict):
        """分发事件"""
        if event_type in self._event_handlers:
            try:
                handler = self._event_handlers[event_type]
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception as e:
                logger.error(f"事件处理异常 {event_type}: {e}")
        
        # 调用通配符处理器
        if "*" in self._event_handlers:
            try:
                handler = self._event_handlers["*"]
                if asyncio.iscoroutinefunction(handler):
                    await handler(event_type, data)
                else:
                    handler(event_type, data)
            except Exception as e:
                logger.error(f"通配符事件处理异常: {e}")
    
    async def _subscribe_task(self, task_id: str):
        """订阅任务更新"""
        future = asyncio.Future()
        self._subscribed_tasks[task_id] = future
        
        try:
            while not future.done():
                try:
                    event_type, data = await asyncio.wait_for(
                        self._sse_queue.get(),
                        timeout=1.0
                    )
                    
                    if data.get("taskId") == task_id:
                        if event_type == "task/completed":
                            future.set_result(TaskResult(
                                task_id=task_id,
                                status=TaskStatus.COMPLETED,
                                artifacts=data.get("artifacts", [])
                            ))
                        elif event_type == "task/failed":
                            future.set_result(TaskResult(
                                task_id=task_id,
                                status=TaskStatus.FAILED,
                                error=data.get("error", "Unknown error")
                            ))
                        elif event_type == "task/status":
                            status_data = data.get("status", {})
                            if status_data.get("state") in [TaskStatus.COMPLETED.value, TaskStatus.FAILED.value]:
                                future.set_result(TaskResult(
                                    task_id=task_id,
                                    status=status_data.get("state"),
                                    message=status_data.get("message", "")
                                ))
                except asyncio.TimeoutError:
                    continue
        finally:
            self._subscribed_tasks.pop(task_id, None)
    
    def on(self, event_type: str, handler: Callable):
        """注册事件处理器"""
        self._event_handlers[event_type] = handler
    
    def off(self, event_type: str):
        """注销事件处理器"""
        self._event_handlers.pop(event_type, None)
    
    # ─────────────────────────────────────────
    # 等待结果
    # ─────────────────────────────────────────
    async def wait_for_task(
        self,
        task_id: str,
        timeout: float = 120.0,
        check_interval: float = 1.0
    ) -> TaskResult:
        """等待任务完成"""
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < timeout:
            task_data = await self.get_task(task_id)
            status = task_data.get("status", {})
            state = status.get("state", "") if isinstance(status, dict) else status
            
            if state == TaskStatus.COMPLETED.value:
                artifacts = task_data.get("artifacts", [])
                return TaskResult(
                    task_id=task_id,
                    status=state,
                    artifacts=artifacts,
                    message=status.get("message", "") if isinstance(status, dict) else ""
                )
            elif state in [TaskStatus.FAILED.value, TaskStatus.CANCELED.value]:
                return TaskResult(
                    task_id=task_id,
                    status=state,
                    error=status.get("message", "Task failed") if isinstance(status, dict) else "Task failed"
                )
            
            await asyncio.sleep(check_interval)
        
        raise Exception(f"Timeout waiting for task {task_id}")
    
    async def send_and_wait(
        self,
        content: str,
        role: str = "user",
        name: str = None,
        timeout: float = 120.0
    ) -> TaskResult:
        """发送任务并等待结果"""
        # 启动 SSE 订阅
        if not self._sse_running:
            await self.subscribe_sse()
        
        # 发送任务
        task_id = await self.send_task(content, role, name=name)
        
        # 等待结果
        return await self.wait_for_task(task_id, timeout)
    
    # ─────────────────────────────────────────
    # WebSocket
    # ─────────────────────────────────────────
    async def ws_connect(self):
        """连接 WebSocket"""
        ws_url = self.server_url.replace("http", "ws") + f"/ws/{self.client_id}"
        self._ws = await websockets.connect(ws_url)
        
        asyncio.create_task(self._ws_listener())
        logger.info(f"WebSocket 已连接: {self.client_id}")
    
    async def _ws_listener(self):
        """WebSocket 监听"""
        try:
            async for message in self._ws:
                data = json.loads(message)
                msg_type = data.get("type", "")
                await self._dispatch_event(msg_type, data)
        except Exception as e:
            logger.error(f"WebSocket 监听异常: {e}")
    
    async def ws_send(self, data: dict) -> bool:
        """通过 WebSocket 发送"""
        if self._ws:
            await self._ws.send(json.dumps(data))
            return True
        return False
    
    async def ws_execute(self, content: str, name: str = None) -> dict:
        """通过 WebSocket 执行任务"""
        await self.ws_send({
            "type": "execute",
            "content": content,
            "name": name or self.client_id
        })
        
        result_queue = asyncio.Queue()
        
        def handler(data):
            if data.get("type") in ["result", "error"]:
                asyncio.create_task(result_queue.put(data))
        
        self.on("result", handler)
        self.on("error", handler)
        
        try:
            result = await asyncio.wait_for(result_queue.get(), timeout=120.0)
            return result
        finally:
            self.off("result")
            self.off("error")
    
    async def ws_close(self):
        """关闭 WebSocket"""
        if self._ws:
            await self._ws.close()
            self._ws = None
    
    # ─────────────────────────────────────────
    # 清理
    # ─────────────────────────────────────────
    async def close(self):
        """关闭客户端"""
        self._sse_running = False
        if self._sse_thread:
            self._sse_thread.join(timeout=2.0)
        await self.ws_close()


# ─────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────
async def create_client(
    server_url: str,
    client_id: str = None,
    auto_subscribe: bool = True
) -> A2AClient:
    """创建 A2A 客户端"""
    client = A2AClient(server_url, client_id, auto_subscribe)
    if auto_subscribe:
        await client.subscribe_sse()
    return client
