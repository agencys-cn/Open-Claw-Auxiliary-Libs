"""
SSE (Server-Sent Events) 流式推送
A2A 协议组件

概述:
    实现 Server-Sent Events 服务端推送机制。
    用于向客户端实时推送任务状态、进度等信息。

特性:
    - 单向通信（服务端 → 客户端）
    - HTTP 长连接
    - 自动重连支持
    - 心跳机制

示例:
    >>> sse_client = SSEClient("client-123")
    >>> broadcaster.register("client-123", sse_client)
    >>> 
    >>> # 推送消息
    >>> await broadcaster.send_to("client-123", "task/completed", {
    ...     "taskId": "task-1",
    ...     "result": "完成"
    ... })
"""

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger("a2a")


class SSEClient:
    """
    SSE 客户端
    
    代表一个 SSE 连接，支持事件推送。
    
    Attributes:
        client_id: 客户端标识符
        queue: 消息队列
    
    Example:
        >>> client = SSEClient("client-123")
        >>> await client.send("task/completed", {"taskId": "task-1"})
        >>> await client.close()
    """
    
    def __init__(self, client_id: str):
        """
        初始化 SSE 客户端
        
        Args:
            client_id: 客户端唯一标识符
        """
        self.client_id = client_id
        self.queue: asyncio.Queue = asyncio.Queue()
        self._closed = False
    
    async def send(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        发送事件
        
        Args:
            event_type: 事件类型，如 "task/completed"
            data: 事件数据
        """
        if self._closed:
            return
        await self.queue.put({
            "event": event_type,
            "data": data
        })
    
    async def close(self) -> None:
        """关闭连接"""
        self._closed = True
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
    
    def is_closed(self) -> bool:
        """是否已关闭"""
        return self._closed


class SSEStream:
    """
    SSE 流
    
    生成 SSE 事件流。
    
    Example:
        >>> stream = SSEStream(client)
        >>> async for event in stream.event_stream():
        ...     print(event)
    """
    
    def __init__(self, client: SSEClient):
        """
        初始化 SSE 流
        
        Args:
            client: SSE 客户端
        """
        self.client = client
    
    async def event_stream(self) -> AsyncGenerator[str, None]:
        """
        生成 SSE 事件流
        
        Yields:
            SSE 格式的事件字符串
        
        事件格式:
            event: <type>
            data: <json>
            
        空行分隔每个事件。
        """
        # 发送连接成功事件
        yield f"event: connected\ndata: {json.dumps({'type': 'connected', 'client_id': self.client.client_id, 'timestamp': datetime.now().isoformat()})}\n\n"
        
        while not self.client.is_closed():
            try:
                event = await asyncio.wait_for(self.client.queue.get(), timeout=60.0)
                
                event_type = event.get("event", "message")
                event_data = event.get("data", {})
                
                yield f"event: {event_type}\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"
                
            except asyncio.TimeoutError:
                # 发送心跳
                yield f"event: ping\ndata: {json.dumps({'type': 'ping', 'timestamp': datetime.now().isoformat()})}\n\n"
            except Exception as e:
                logger.error(f"SSE 流异常: {e}")
                break
        
        # 发送断开连接事件
        yield f"event: disconnect\ndata: {json.dumps({'type': 'disconnect', 'client_id': self.client.client_id})}\n\n"


class SSEBroadcaster:
    """
    SSE 广播器
    
    管理所有 SSE 客户端，支持广播和单独发送。
    
    Example:
        >>> broadcaster.register("client-1", sse_client)
        >>> 
        >>> # 广播到所有客户端
        >>> await broadcaster.broadcast("task/progress", {"progress": 50})
        >>> 
        >>> # 发送到指定客户端
        >>> await broadcaster.send_to("client-1", "task/completed", {"taskId": "task-1"})
    """
    
    def __init__(self):
        """初始化广播器"""
        self._clients: Dict[str, SSEClient] = {}
    
    def register(self, client_id: str, client: SSEClient) -> None:
        """
        注册客户端
        
        Args:
            client_id: 客户端标识符
            client: SSE 客户端对象
        """
        self._clients[client_id] = client
        logger.info(f"SSE 客户端注册: {client_id}, 当前连接数: {len(self._clients)}")
    
    def unregister(self, client_id: str) -> None:
        """
        注销客户端
        
        Args:
            client_id: 客户端标识符
        """
        if client_id in self._clients:
            del self._clients[client_id]
            logger.info(f"SSE 客户端注销: {client_id}, 当前连接数: {len(self._clients)}")
    
    async def broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        广播到所有客户端
        
        Args:
            event_type: 事件类型
            data: 事件数据
        """
        for client_id, client in list(self._clients.items()):
            try:
                await client.send(event_type, data)
            except Exception as e:
                logger.error(f"广播到 {client_id} 失败: {e}")
                await self.close_client(client_id)
    
    async def send_to(self, client_id: str, event_type: str, data: Dict[str, Any]) -> bool:
        """
        发送到指定客户端
        
        Args:
            client_id: 客户端标识符
            event_type: 事件类型
            data: 事件数据
        
        Returns:
            是否发送成功
        """
        if client_id not in self._clients:
            return False
        try:
            await self._clients[client_id].send(event_type, data)
            return True
        except Exception as e:
            logger.error(f"发送到 {client_id} 失败: {e}")
            await self.close_client(client_id)
            return False
    
    async def close_client(self, client_id: str) -> None:
        """
        关闭客户端
        
        Args:
            client_id: 客户端标识符
        """
        if client_id in self._clients:
            await self._clients[client_id].close()
            self.unregister(client_id)
    
    @property
    def client_count(self) -> int:
        """客户端数量"""
        return len(self._clients)
    
    def list_clients(self) -> List[str]:
        """列出所有客户端"""
        return list(self._clients.keys())


# 全局广播器
broadcaster = SSEBroadcaster()
