"""
WebSocket 处理
A2A 协议组件

概述:
    处理 WebSocket 连接和消息。
    支持双向通信，可接收客户端消息并主动推送。

示例:
    >>> ws_server.register_client("client-1", websocket, "127.0.0.1:12345")
    >>> 
    >>> # 发送消息
    >>> await ws_server.send_to("client-1", "task/completed", {"taskId": "task-1"})
    >>> 
    >>> # 广播
    >>> await ws_server.broadcast("ping", {"time": "..."})
"""

import asyncio
import json
import logging
from typing import Optional, Callable, Awaitable, Dict, Any, List
from datetime import datetime

logger = logging.getLogger("a2a")


class WSClient:
    """
    WebSocket 客户端
    
    代表一个 WebSocket 连接。
    
    Attributes:
        client_id: 客户端标识符
        ws: WebSocket 连接对象
        addr: 客户端地址
        metadata: 额外元数据
    
    Example:
        >>> client = WSClient("client-123", websocket, "127.0.0.1:54321")
        >>> await client.send("welcome", {"client_id": "client-123"})
    """
    
    def __init__(self, client_id: str, ws: Any, addr: str):
        """
        初始化 WebSocket 客户端
        
        Args:
            client_id: 客户端唯一标识符
            ws: WebSocket 连接对象
            addr: 客户端地址字符串
        """
        self.client_id = client_id
        self.ws = ws
        self.addr = addr
        self._closed = False
        self.metadata: Dict[str, Any] = {}
    
    async def send(self, event_type: str, data: Dict[str, Any]) -> bool:
        """
        发送 JSON 消息
        
        Args:
            event_type: 消息类型
            data: 消息数据
        
        Returns:
            是否发送成功
        """
        if self._closed:
            return False
        try:
            message = {
                "type": event_type,
                **data
            }
            await self.ws.send_json(message)
            return True
        except Exception as e:
            logger.error(f"WS 发送失败 {self.client_id}: {e}")
            self._closed = True
            return False
    
    async def close(self) -> None:
        """关闭连接"""
        self._closed = True
        try:
            await self.ws.close()
        except:
            pass
    
    def is_closed(self) -> bool:
        """是否已关闭"""
        return self._closed
    
    def __repr__(self) -> str:
        return f"WSClient({self.client_id}@{self.addr})"


class WSHandler:
    """
    WebSocket 消息处理器
    
    注册和处理不同类型的 WebSocket 消息。
    
    Example:
        >>> handler = WSHandler()
        >>> 
        >>> async def handle_execute(data, client):
        ...     result = await process(data["content"])
        ...     await client.send("result", {"result": result})
        >>> 
        >>> handler.register("execute", handle_execute)
    """
    
    def __init__(self):
        """初始化处理器"""
        self._handlers: Dict[str, Callable] = {}
    
    def register(self, msg_type: str, handler: Callable) -> None:
        """
        注册消息处理器
        
        Args:
            msg_type: 消息类型
            handler: 处理函数，签名 (data: dict, client: WSClient) -> Awaitable[None]
        """
        self._handlers[msg_type] = handler
        logger.debug(f"WS 消息处理器注册: {msg_type}")
    
    async def handle(self, msg_type: str, data: Dict[str, Any], client: WSClient) -> None:
        """
        处理消息
        
        Args:
            msg_type: 消息类型
            data: 消息数据
            client: WebSocket 客户端
        """
        if msg_type in self._handlers:
            await self._handlers[msg_type](data, client)
        else:
            logger.warning(f"未处理的 WS 消息类型: {msg_type}")
    
    def unregister(self, msg_type: str) -> None:
        """
        注销处理器
        
        Args:
            msg_type: 消息类型
        """
        if msg_type in self._handlers:
            del self._handlers[msg_type]
    
    def list_handlers(self) -> List[str]:
        """列出所有处理器"""
        return list(self._handlers.keys())


class WSServer:
    """
    WebSocket 服务器
    
    管理所有 WebSocket 连接和消息处理。
    
    Example:
        >>> server = WSServer()
        >>> 
        >>> # 注册客户端
        >>> client = server.register_client("client-1", websocket, "127.0.0.1:12345")
        >>> 
        >>> # 发送消息
        >>> await server.send_to("client-1", "task/completed", {"taskId": "task-1"})
        >>> 
        >>> # 广播
        >>> await server.broadcast("ping", {})
    """
    
    def __init__(self):
        """初始化服务器"""
        self._clients: Dict[str, WSClient] = {}
        self._handler = WSHandler()
    
    def register_client(self, client_id: str, ws: Any, addr: str) -> WSClient:
        """
        注册客户端
        
        Args:
            client_id: 客户端唯一标识符
            ws: WebSocket 连接对象
            addr: 客户端地址
        
        Returns:
            WSClient 对象
        """
        client = WSClient(client_id, ws, addr)
        self._clients[client_id] = client
        logger.info(f"WS 客户端注册: {client_id} @ {addr}, 当前连接数: {len(self._clients)}")
        return client
    
    def unregister_client(self, client_id: str) -> None:
        """
        注销客户端
        
        Args:
            client_id: 客户端标识符
        """
        if client_id in self._clients:
            del self._clients[client_id]
            logger.info(f"WS 客户端注销: {client_id}, 当前连接数: {len(self._clients)}")
    
    def get_client(self, client_id: str) -> Optional[WSClient]:
        """
        获取客户端
        
        Args:
            client_id: 客户端标识符
        
        Returns:
            WSClient 对象
        """
        return self._clients.get(client_id)
    
    async def send_to(self, client_id: str, event_type: str, data: Dict[str, Any]) -> bool:
        """
        发送到指定客户端
        
        Args:
            client_id: 客户端标识符
            event_type: 消息类型
            data: 消息数据
        
        Returns:
            是否发送成功
        """
        client = self._clients.get(client_id)
        if not client:
            return False
        return await client.send(event_type, data)
    
    async def broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        广播到所有客户端
        
        Args:
            event_type: 消息类型
            data: 消息数据
        """
        for client_id in list(self._clients.keys()):
            await self.send_to(client_id, event_type, data)
    
    async def close_client(self, client_id: str) -> None:
        """
        关闭客户端
        
        Args:
            client_id: 客户端标识符
        """
        client = self._clients.get(client_id)
        if client:
            await client.close()
            self.unregister_client(client_id)
    
    @property
    def handler(self) -> WSHandler:
        """获取消息处理器"""
        return self._handler
    
    @property
    def client_count(self) -> int:
        """客户端数量"""
        return len(self._clients)
    
    def list_clients(self) -> List[str]:
        """列出所有客户端"""
        return list(self._clients.keys())
    
    def has_client(self, client_id: str) -> bool:
        """
        检查客户端是否存在
        
        Args:
            client_id: 客户端标识符
        
        Returns:
            是否存在
        """
        return client_id in self._clients


# 全局 WebSocket 服务器
ws_server = WSServer()
