"""
Gateway 桥接器
A2A 协议核心组件 - 桥接 OpenClaw Gateway

概述:
    负责 A2A Server 与 OpenClaw Gateway 之间的通信。
    处理消息转发、会话管理、流式输出等。

功能:
    1. 消息转发 - A2A 请求 → Gateway → 返回结果
    2. 流式处理 - 支持 SSE 格式的流式输出
    3. 会话管理 - 维护与 Gateway 的会话上下文
    4. 健康检查 - 监控 Gateway 可用性

示例:
    >>> bridge = GatewayBridge()
    >>> 
    >>> # 同步执行
    >>> result = await bridge.execute("写一个小说", "a2a:client1", {"name": "钱多多"})
    >>> 
    >>> # 流式执行
    >>> async for chunk in bridge.execute_stream("写一个小说", "a2a:client1"):
    ...     print(chunk, end="", flush=True)
"""

import asyncio
import json
import logging
import os
from typing import Optional, AsyncGenerator, Dict, Any
from dataclasses import dataclass, field
import httpx

logger = logging.getLogger("a2a")


@dataclass
class GatewayConfig:
    """
    Gateway 配置
    
    Attributes:
        url: Gateway 服务地址，默认为 http://127.0.0.1:18789
        token: 认证 Token
        timeout: 请求超时时间（秒），默认 0 表示不超时
    """
    url: str = "http://127.0.0.1:18789"
    token: str = ""
    timeout: float = 0.0  # 0 表示不超时，只要 Gateway 在执行就一直等
    
    @classmethod
    def load(cls) -> "GatewayConfig":
        """
        从配置文件加载 Gateway 配置
        
        从 ~/.openclaw/openclaw.json 读取配置。
        
        Returns:
            GatewayConfig 实例
        """
        try:
            with open("/root/.openclaw/openclaw.json") as f:
                config = json.load(f)
                gateway_conf = config.get("gateway", {})
                return cls(
                    url=gateway_conf.get("url", "http://127.0.0.1:3000"),
                    token=gateway_conf.get("auth", {}).get("token", ""),
                    timeout=gateway_conf.get("timeout", 120.0)
                )
        except Exception as e:
            logger.warning(f"加载 Gateway 配置失败: {e}")
            return cls()


class GatewayHealth:
    """
    Gateway 健康状态管理器
    
    跟踪 Gateway 的可用性状态，注册客户端连接。
    
    Attributes:
        _is_alive: Gateway 是否在线
        _last_check: 最后检查时间
        _clients: 已注册的客户端字典
    
    Example:
        >>> health = GatewayHealth()
        >>> health.register_client("client1", ws_client)
        >>> 
        >>> if health.is_alive:
        ...     print("Gateway 在线")
    """
    
    def __init__(self):
        """初始化健康状态"""
        self._is_alive = True
        self._last_check: float = 0.0
        self._clients: Dict[str, Any] = {}
    
    @property
    def is_alive(self) -> bool:
        """Gateway 是否在线"""
        return self._is_alive
    
    def mark_alive(self):
        """标记为在线"""
        self._is_alive = True
        self._last_check = asyncio.get_event_loop().time()
        logger.info("Gateway 状态: 在线")
    
    def mark_dead(self):
        """标记为离线"""
        self._is_alive = False
        logger.warning("Gateway 状态: 离线")
    
    def register_client(self, client_id: str, client: Any) -> None:
        """
        注册客户端
        
        Args:
            client_id: 客户端标识符
            client: 客户端对象
        """
        self._clients[client_id] = client
        logger.debug(f"Gateway 客户端注册: {client_id}")
    
    def unregister_client(self, client_id: str) -> None:
        """
        注销客户端
        
        Args:
            client_id: 客户端标识符
        """
        if client_id in self._clients:
            del self._clients[client_id]
    
    async def health_check(self) -> bool:
        """
        执行健康检查
        
        尝试连接 Gateway /health 端点。
        
        Returns:
            Gateway 是否可用
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{gateway_config.url}/health")
                if response.status_code == 200:
                    self.mark_alive()
                    return True
        except Exception as e:
            logger.debug(f"Gateway 健康检查失败: {e}")
        self.mark_dead()
        return False


# 全局 Gateway 配置和健康状态
gateway_config = GatewayConfig.load()
gateway_health = GatewayHealth()


class GatewayBridge:
    """
    Gateway 桥接器
    
    桥接 A2A 协议和 OpenClaw Gateway，负责：
    1. 消息转发（A2A → Gateway）
    2. 结果回传（Gateway → A2A）
    3. 会话管理
    4. 流式输出处理
    
    Attributes:
        config: Gateway 配置
        _session_cache: 会话历史缓存
    
    Example:
        >>> bridge = GatewayBridge()
        >>> 
        >>> # 同步执行
        >>> result = await bridge.execute(
        ...     content="写一个小说开头",
        ...     session_key="a2a:client123",
        ...     context={"name": "钱多多"}
        ... )
        >>> 
        >>> # 流式执行
        >>> async for chunk in bridge.execute_stream(content, session_key):
        ...     print(chunk, end="")
    """
    
    def __init__(self, config: GatewayConfig = None):
        """
        初始化桥接器
        
        Args:
            config: Gateway 配置（使用全局配置如果为 None）
        """
        self.config = config or gateway_config
        self._session_cache: Dict[str, list] = {}
    
    async def execute(
        self,
        content: str,
        session_key: str,
        context: Dict[str, Any] = None
    ) -> str:
        """
        执行内容（同步方式）
        
        发送消息到 Gateway，等待完整结果后返回。
        
        Args:
            content: 消息内容
            session_key: 会话 key（格式: a2a:client_id）
            context: 额外上下文，包含：
                - name: 发送者名称
                - history: 历史消息列表
        
        Returns:
            执行结果字符串
        
        Raises:
            Exception: Gateway 不可用或执行失败
        
        Example:
            >>> result = await bridge.execute(
            ...     content="写一个小说",
            ...     session_key="a2a:client1",
            ...     context={"name": "钱多多"}
            ... )
        """
        if not gateway_health.is_alive:
            raise Exception("Gateway 当前不可用，请稍后重试")
        
        # 添加用户名前缀
        if context and context.get("name"):
            content = f"[{context['name']}] {content}"
        
        messages = [{"role": "user", "content": content}]
        
        # 添加历史消息
        if context and context.get("history"):
            messages = context["history"] + messages
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.token}",
            "x-openclaw-session-key": session_key,
        }
        
        payload = {
            "model": "openclaw",
            "messages": messages,
            "stream": False,  # 同步模式
        }
        
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                response = await client.post(
                    f"{self.config.url}/v1/chat/completions",
                    json=payload,
                    headers=headers
                )
                
                if response.status_code == 200:
                    data = response.json()
                    result = data["choices"][0]["message"]["content"]
                    
                    # 更新会话缓存
                    self._update_session(session_key, messages, result)
                    
                    return result
                elif response.status_code == 401:
                    return "认证失败：token 无效"
                else:
                    return f"执行失败: HTTP {response.status_code} - {response.text[:200]}"
                    
        except httpx.TimeoutException:
            return "执行超时，请稍后重试"
        except Exception as e:
            logger.error(f"Gateway 调用失败: {e}")
            return f"执行失败: {e}"
    
    async def execute_stream(
        self,
        content: str,
        session_key: str,
        context: Dict[str, Any] = None,
        cancel_event=None
    ) -> AsyncGenerator[str, None]:
        """
        执行内容（流式方式）
        
        发送消息到 Gateway，边接收边 yield 内容片段。
        支持通过 cancel_event 取消执行。
        
        Args:
            content: 消息内容
            session_key: 会话 key
            context: 额外上下文
            cancel_event: asyncio.Event，可选，用于取消执行
        
        Yields:
            每个内容片段（字符串）
        
        Raises:
            Exception: Gateway 不可用或执行失败
            asyncio.CancelledError: 任务被取消
        
        Example:
            >>> async for chunk in bridge.execute_stream(
            ...     content="写一个小说",
            ...     session_key="a2a:client1"
            ... ):
            ...     print(chunk, end="", flush=True)
        
        Example with Cancel:
            >>> cancel_event = asyncio.Event()
            >>> # 在另一个任务中取消
            >>> # cancel_event.set()
            >>> async for chunk in bridge.execute_stream(
            ...     content="写一个小说",
            ...     session_key="a2a:client1",
            ...     cancel_event=cancel_event
            ... ):
            ...     print(chunk, end="", flush=True)
        """
        if not gateway_health.is_alive:
            raise Exception("Gateway 当前不可用，请稍后重试")
        
        # 添加用户名前缀
        if context and context.get("name"):
            content = f"[{context['name']}] {content}"
        
        messages = [{"role": "user", "content": content}]
        
        # 添加历史消息
        if context and context.get("history"):
            messages = context["history"] + messages
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.token}",
            "x-openclaw-session-key": session_key,
        }
        
        payload = {
            "model": "openclaw",
            "messages": messages,
            "stream": True,  # 流式模式
        }
        
        full_content = ""
        
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.config.url}/v1/chat/completions",
                    json=payload,
                    headers=headers
                ) as response:
                    async for line in response.aiter_lines():
                        # 检查取消事件
                        if cancel_event and cancel_event.is_set():
                            logger.info(f"流式执行被取消: {session_key}")
                            raise asyncio.CancelledError("任务被取消")
                        
                        if not line.strip():
                            continue
                        
                        # 解析 SSE 格式: data: {...}
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            
                            try:
                                data = json.loads(data_str)
                                delta = data.get("choices", [{}])[0].get("delta", {})
                                content_chunk = delta.get("content", "")
                                if content_chunk:
                                    full_content += content_chunk
                                    yield content_chunk
                            except json.JSONDecodeError:
                                continue
                
                # 更新会话缓存
                self._update_session(session_key, messages, full_content)
                    
        except asyncio.CancelledError:
            # 取消是正常流程，不记录为错误
            raise
        except Exception as e:
            logger.error(f"Gateway 流式调用失败: {e}")
            raise
    
    def _update_session(self, session_key: str, messages: list, result: str) -> None:
        """
        更新会话缓存
        
        缓存消息历史，避免每次发送完整历史。
        
        Args:
            session_key: 会话 key
            messages: 发送的消息列表
            result: 收到的结果
        """
        if session_key not in self._session_cache:
            self._session_cache[session_key] = []
        
        self._session_cache[session_key].extend(messages)
        self._session_cache[session_key].append({
            "role": "assistant",
            "content": result
        })
        
        # 限制缓存大小（保留最近 20 条）
        if len(self._session_cache[session_key]) > 20:
            self._session_cache[session_key] = self._session_cache[session_key][-20:]
    
    def get_history(self, session_key: str, limit: int = 10) -> list:
        """
        获取会话历史
        
        Args:
            session_key: 会话 key
            limit: 返回最近 N 条
        
        Returns:
            消息列表
        """
        cache = self._session_cache.get(session_key, [])
        return cache[-limit:] if limit > 0 else cache
    
    def clear_session(self, session_key: str) -> None:
        """
        清除会话缓存
        
        Args:
            session_key: 会话 key
        """
        if session_key in self._session_cache:
            del self._session_cache[session_key]
            logger.debug(f"会话已清除: {session_key}")
    
    def has_session(self, session_key: str) -> bool:
        """
        检查会话是否存在
        
        Args:
            session_key: 会话 key
        
        Returns:
            会话是否存在
        """
        return session_key in self._session_cache


# 全局桥接器实例
gateway_bridge = GatewayBridge()


# ─────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────

async def execute(content: str, session_key: str, **kwargs) -> str:
    """
    快捷执行函数
    
    使用全局 gateway_bridge 执行内容。
    
    See Also:
        GatewayBridge.execute()
    """
    return await gateway_bridge.execute(content, session_key, **kwargs)


async def execute_stream(content: str, session_key: str, **kwargs) -> AsyncGenerator[str, None]:
    """
    快捷流式执行函数
    
    使用全局 gateway_bridge 执行流式内容。
    
    See Also:
        GatewayBridge.execute_stream()
    """
    async for chunk in gateway_bridge.execute_stream(content, session_key, **kwargs):
        yield chunk


def get_history(session_key: str, limit: int = 10) -> list:
    """
    快捷获取历史函数
    
    使用全局 gateway_bridge 获取会话历史。
    
    See Also:
        GatewayBridge.get_history()
    """
    return gateway_bridge.get_history(session_key, limit)


def clear_session(session_key: str) -> None:
    """
    快捷清除会话函数
    
    使用全局 gateway_bridge 清除会话。
    
    See Also:
        GatewayBridge.clear_session()
    """
    gateway_bridge.clear_session(session_key)
