"""
Gateway 桥接器
A2A 协议核心组件 - 桥接 OpenClaw Gateway

概述:
    负责 A2A Server 与 OpenClaw Gateway 之间的通信。
    
    注意：现在推荐使用 gateway_pool.py 中的 GatewayPool 类，
    它提供自动重连和任务队列功能。
    
    GatewayBridge 保留用于简单场景或向后兼容。

功能:
    1. 消息转发 - A2A 请求 → Gateway → 返回结果
    2. 流式处理 - 支持 SSE 格式的流式输出
    3. 会话管理 - 维护与 Gateway 的会话上下文
    4. 健康检查 - 监控 Gateway 可用性
    5. 任务取消传播 - 取消 A2A 任务时同时取消 Gateway 调用
    6. 超时控制 - 支持任务超时自动取消

示例:
    >>> bridge = GatewayBridge()
    >>> 
    >>> # 同步执行
    >>> result = await bridge.execute("写一个小说", "a2a:client1", {"name": "钱多多"})
    >>> 
    >>> # 流式执行
    >>> async for chunk in bridge.execute_stream("写一个小说", "a2a:client1"):
    ...     print(chunk, end="", flush=True)
    
    
    推荐使用（带自动重连）：
    >>> from gateway_pool import get_gateway_pool
    >>> pool = get_gateway_pool()
    >>> await pool.start()
    >>> 
    >>> # 执行任务，Gateway 重启后自动重连
    >>> result = await pool.execute("写一个小说", session_key)
"""

import asyncio
import json
import logging
import os
from typing import Optional, AsyncGenerator, Dict, Any, List
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
        task_timeout: 任务超时时间（秒），默认 300 秒
        max_retries: 最大重试次数，默认 3
    """
    url: str = "http://127.0.0.1:18789"
    token: str = ""
    timeout: float = 0.0  # 0 表示不超时，只要 Gateway 在执行就一直等
    task_timeout: int = 300  # 任务超时 300 秒
    
    @classmethod
    def load(cls) -> "GatewayConfig":
        """
        从配置文件加载 Gateway 配置
        
        从环境变量读取配置：
        - GATEWAY_URL
        - GATEWAY_TIMEOUT
        - TASK_TIMEOUT
        
        Returns:
            GatewayConfig 实例
        """
        return cls(
            url=os.getenv("GATEWAY_URL", "http://127.0.0.1:18789"),
            token=os.getenv("GATEWAY_TOKEN", ""),
            timeout=float(os.getenv("GATEWAY_TIMEOUT", "0")),
            task_timeout=int(os.getenv("TASK_TIMEOUT", "300")),
        )


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
    5. 任务取消传播
    6. 超时控制
    
    Attributes:
        config: Gateway 配置
        _session_cache: 会话历史缓存
        _active_tasks: 活跃任务字典 (task_id -> cancel_event)
    
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
        self._active_tasks: Dict[str, asyncio.Event] = {}  # 活跃任务追踪
    
    async def execute(
        self,
        content: str,
        session_key: str,
        context: Dict[str, Any] = None,
        timeout: int = None,
        cancel_event: asyncio.Event = None
    ) -> str:
        """
        执行内容（同步方式）
        
        发送消息到 Gateway，等待完整结果后返回。
        支持超时控制和取消传播。
        
        Args:
            content: 消息内容
            session_key: 会话 key（格式: a2a:client_id）
            context: 额外上下文，包含：
                - name: 发送者名称
                - history: 历史消息列表
            timeout: 超时时间（秒），None 使用默认配置
            cancel_event: 取消事件，可选
        
        Returns:
            执行结果字符串
        
        Raises:
            Exception: Gateway 不可用或执行失败
            asyncio.TimeoutError: 任务超时
            asyncio.CancelledError: 任务被取消
        
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
        
        # 追踪任务
        task_id = session_key
        self._active_tasks[task_id] = cancel_event or asyncio.Event()
        
        try:
            # 设置超时
            timeout_seconds = timeout or self.config.task_timeout
            timeout_obj = None
            if timeout_seconds > 0:
                timeout_obj = asyncio.create_task(
                    self._timeout_handler(task_id, timeout_seconds)
                )
            
            async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                response = await client.post(
                    f"{self.config.url}/v1/chat/completions",
                    json=payload,
                    headers=headers
                )
                
                # 取消超时任务
                if timeout_obj:
                    timeout_obj.cancel()
                
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
                    
        except asyncio.CancelledError:
            # 传播取消
            await self._propagate_cancel(session_key)
            raise
        except httpx.TimeoutException:
            return "执行超时，请稍后重试"
        except Exception as e:
            logger.error(f"Gateway 调用失败: {e}")
            raise
        finally:
            # 清理活跃任务
            self._active_tasks.pop(task_id, None)
    
    async def execute_stream(
        self,
        content: str,
        session_key: str,
        context: Dict[str, Any] = None,
        timeout: int = None,
        cancel_event: asyncio.Event = None
    ) -> AsyncGenerator[str, None]:
        """
        执行内容（流式方式）
        
        发送消息到 Gateway，边接收边 yield 内容片段。
        支持通过 cancel_event 取消执行，支持超时控制。
        
        Args:
            content: 消息内容
            session_key: 会话 key
            context: 额外上下文
            timeout: 超时时间（秒）
            cancel_event: 取消事件
        
        Yields:
            每个内容片段（字符串）
        
        Raises:
            Exception: Gateway 不可用或执行失败
            asyncio.CancelledError: 任务被取消
            asyncio.TimeoutError: 任务超时
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
        task_id = session_key
        timeout_task = None
        
        # 追踪任务
        task_cancel_event = cancel_event or asyncio.Event()
        self._active_tasks[task_id] = task_cancel_event
        
        try:
            # 设置超时
            timeout_seconds = timeout or self.config.task_timeout
            if timeout_seconds > 0:
                timeout_task = asyncio.create_task(
                    self._timeout_handler(task_id, timeout_seconds)
                )
            
            async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self.config.url}/v1/chat/completions",
                    json=payload,
                    headers=headers
                ) as response:
                    async for line in response.aiter_lines():
                        # 检查取消事件
                        if task_cancel_event.is_set():
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
                
                # 取消超时任务
                if timeout_task:
                    timeout_task.cancel()
                
                # 更新会话缓存
                self._update_session(session_key, messages, full_content)
                    
        except asyncio.CancelledError:
            # 传播取消到 Gateway
            await self._propagate_cancel(session_key)
            raise
        except Exception as e:
            logger.error(f"Gateway 流式调用失败: {e}")
            raise
        finally:
            # 清理
            self._active_tasks.pop(task_id, None)
            if timeout_task:
                timeout_task.cancel()
    
    async def _timeout_handler(self, task_id: str, timeout_seconds: int):
        """
        超时处理器
        
        任务超时后设置取消事件。
        
        Args:
            task_id: 任务 ID
            timeout_seconds: 超时时间（秒）
        """
        await asyncio.sleep(timeout_seconds)
        
        if task_id in self._active_tasks:
            logger.warning(f"Task {task_id} 超时 ({timeout_seconds}s)")
            self._active_tasks[task_id].set()
    
    async def _propagate_cancel(self, session_key: str):
        """
        传播取消到 Gateway
        
        通知 Gateway 取消当前会话的执行。
        
        Args:
            session_key: 会话 key
        """
        try:
            # 尝试通知 Gateway 取消会话
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{self.config.url}/v1/sessions/cancel",
                    json={"session_key": session_key}
                )
                logger.info(f"取消传播到 Gateway: {session_key}")
        except Exception as e:
            logger.warning(f"取消传播失败: {e}")
    
    def cancel_task(self, session_key: str) -> bool:
        """
        取消任务
        
        触发取消事件，通知执行中的任务立即停止。
        
        Args:
            session_key: 会话 key
        
        Returns:
            是否成功触发取消
        """
        if session_key in self._active_tasks:
            self._active_tasks[session_key].set()
            logger.info(f"任务取消触发: {session_key}")
            return True
        return False
    
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
        
        # 同时取消活跃任务
        self.cancel_task(session_key)
    
    def has_session(self, session_key: str) -> bool:
        """
        检查会话是否存在
        
        Args:
            session_key: 会话 key
        
        Returns:
            会话是否存在
        """
        return session_key in self._session_cache
    
    def get_active_count(self) -> int:
        """获取活跃任务数"""
        return len(self._active_tasks)


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


def cancel_task(session_key: str) -> bool:
    """
    快捷取消任务函数
    
    使用全局 gateway_bridge 取消任务。
    """
    return gateway_bridge.cancel_task(session_key)
