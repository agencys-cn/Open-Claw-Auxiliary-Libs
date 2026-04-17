"""
Gateway 连接池
A2A 协议扩展 - Gateway 连接管理和自动重连

概述:
    管理与 Gateway 的连接，支持：
    1. 连接池（多个 Gateway 实例）
    2. 健康检查和自动重连
    3. 任务队列（Gateway 重启时缓存任务）
    4. 故障转移

使用场景:
    1. Gateway 重启时自动重连
    2. 多个 Gateway 实例负载均衡
    3. Gateway 故障时缓存任务

示例:
    >>> pool = GatewayPool()
    >>> await pool.start()
    >>> 
    >>> # 使用连接池执行
    >>> result = await pool.execute("写一个小说", session_key="a2a:client1")
    >>> 
    >>> # 监听 Gateway 状态
    >>> pool.on_gw_available(lambda: print("Gateway 上线了"))
    >>> pool.on_gw_unavailable(lambda: print("Gateway 离线了"))
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable, Any, AsyncGenerator, List, Dict

import httpx

logger = logging.getLogger("a2a")


class GatewayState(Enum):
    """Gateway 状态"""
    UNKNOWN = "unknown"       # 未知
    AVAILABLE = "available"   # 可用
    UNAVAILABLE = "unavailable"  # 不可用
    RECONNECTING = "reconnecting"  # 重连中


@dataclass
class GatewayConnection:
    """Gateway 连接"""
    url: str
    token: str = ""
    timeout: float = 0.0
    state: GatewayState = GatewayState.UNKNOWN
    last_check: float = 0.0
    consecutive_failures: int = 0
    last_success: float = 0.0
    
    # 连接池配置
    max_retries: int = 3
    retry_delay: float = 1.0
    max_retry_delay: float = 30.0
    
    # HTTP 客户端
    _client: Optional[httpx.AsyncClient] = field(default=None, init=False, repr=False)


class GatewayPool:
    """
    Gateway 连接池
    
    特性：
    1. 单例模式管理 Gateway 连接
    2. 健康检查循环（自动检测 Gateway 状态）
    3. 自动重连（Gateway 重启后自动恢复）
    4. 任务队列（Gateway 不可用时缓存任务）
    5. 故障转移支持
    
    Attributes:
        urls: Gateway URL 列表（支持多实例）
        tokens: 认证 Token
        check_interval: 健康检查间隔（秒）
        reconnect_interval: 重连检查间隔（秒）
    
    Example:
        >>> pool = GatewayPool()
        >>> await pool.start()
        >>> 
        >>> # 执行任务
        >>> result = await pool.execute("写一个小说")
        >>> 
        >>> # 流式执行
        >>> async for chunk in pool.execute_stream("写一个小说"):
        ...     print(chunk, end="")
    """
    
    _instance: Optional["GatewayPool"] = None
    
    def __init__(
        self,
        urls: List[str] = None,
        token: str = "",
        check_interval: float = 30.0,
        reconnect_interval: float = 5.0,
        task_queue_size: int = 1000
    ):
        """
        初始化连接池
        
        Args:
            urls: Gateway URL 列表
            token: 认证 Token
            check_interval: 健康检查间隔（秒）
            reconnect_interval: 重连检查间隔（秒）
            task_queue_size: 任务队列大小
        """
        if urls is None:
            urls = ["http://127.0.0.1:18789"]
        
        self.urls = urls
        self.token = token
        self.check_interval = check_interval
        self.reconnect_interval = reconnect_interval
        self.task_queue_size = task_queue_size
        
        # 连接状态
        self._connections: Dict[str, GatewayConnection] = {}
        for url in urls:
            self._connections[url] = GatewayConnection(url=url, token=token)
        
        self._active_url: Optional[str] = None
        self._state: GatewayState = GatewayState.UNKNOWN
        
        # 任务队列（Gateway 不可用时）
        self._task_queue: asyncio.Queue = asyncio.Queue(maxsize=task_queue_size)
        
        # 事件回调
        self._on_available_callbacks: List[Callable] = []
        self._on_unavailable_callbacks: List[Callable] = []
        self._on_state_change_callbacks: List[Callable] = []
        
        # 任务
        self._check_task: Optional[asyncio.Task] = None
        self._drain_task: Optional[asyncio.Task] = None
        self._running = False
        
        # 锁
        self._lock = asyncio.Lock()
    
    @classmethod
    def get_instance(cls) -> "GatewayPool":
        """获取单例实例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def start(self):
        """启动连接池"""
        if self._running:
            return
        
        self._running = True
        logger.info(f"Gateway 连接池启动，URLs: {self.urls}")
        
        # 启动健康检查
        self._check_task = asyncio.create_task(self._check_loop())
        
        # 启动任务队列处理
        self._drain_task = asyncio.create_task(self._drain_loop())
        
        # 初始检查
        await self._check_all()
    
    async def stop(self):
        """停止连接池"""
        self._running = False
        
        if self._check_task:
            self._check_task.cancel()
            self._check_task = None
        
        if self._drain_task:
            self._drain_task.cancel()
            self._drain_task = None
        
        # 关闭所有连接
        for conn in self._connections.values():
            if conn._client:
                await conn._client.aclose()
                conn._client = None
        
        logger.info("Gateway 连接池已停止")
    
    # ─────────────────────────────────────────
    # 健康检查
    # ─────────────────────────────────────────
    
    async def _check_loop(self):
        """健康检查循环"""
        while self._running:
            try:
                await asyncio.sleep(self.check_interval)
                await self._check_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"健康检查异常: {e}")
    
    async def _check_all(self):
        """检查所有 Gateway 实例"""
        old_state = self._state
        
        async with self._lock:
            for url, conn in self._connections.items():
                is_alive = await self._health_check(conn)
                
                if is_alive:
                    conn.state = GatewayState.AVAILABLE
                    conn.consecutive_failures = 0
                    self._active_url = url
                    self._state = GatewayState.AVAILABLE
                    logger.debug(f"Gateway 可用: {url}")
                    break
                else:
                    conn.consecutive_failures += 1
                    if conn.consecutive_failures >= 3:
                        conn.state = GatewayState.UNAVAILABLE
            
            # 检查是否所有都不可用
            all_unavailable = all(
                c.state == GatewayState.UNAVAILABLE 
                for c in self._connections.values()
            )
            
            if all_unavailable and self._connections:
                if self._state != GatewayState.UNAVAILABLE:
                    self._state = GatewayState.UNAVAILABLE
                    logger.warning("所有 Gateway 实例都不可用")
                    await self._notify_unavailable()
    
    async def _health_check(self, conn: GatewayConnection) -> bool:
        """
        健康检查
        
        Args:
            conn: Gateway 连接
        
        Returns:
            是否健康
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{conn.url}/health")
                if response.status_code == 200:
                    conn.last_check = datetime.now().timestamp()
                    conn.last_success = datetime.now().timestamp()
                    return True
        except Exception as e:
            logger.debug(f"Gateway 健康检查失败: {conn.url}, {e}")
        
        conn.last_check = datetime.now().timestamp()
        return False
    
    async def _reconnect_loop(self):
        """重连循环（Gateway 恢复时处理缓存的任务）"""
        while self._running:
            try:
                await asyncio.sleep(self.reconnect_interval)
                
                if self._state == GatewayState.UNAVAILABLE:
                    await self._check_all()
                    
                    if self._state == GatewayState.AVAILABLE:
                        logger.info("Gateway 已恢复，开始处理缓存任务")
                        await self._notify_available()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"重连循环异常: {e}")
    
    # ─────────────────────────────────────────
    # 任务队列
    # ─────────────────────────────────────────
    
    async def _drain_loop(self):
        """处理缓存的任务队列"""
        while self._running:
            try:
                # 从队列获取任务
                task_info = await asyncio.wait_for(
                    self._task_queue.get(),
                    timeout=1.0
                )
                
                # 等待 Gateway 可用
                while self._state != GatewayState.AVAILABLE:
                    await asyncio.sleep(1.0)
                    if not self._running:
                        return
                
                # 执行任务
                await self._execute_task(**task_info)
                
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"处理缓存任务异常: {e}")
    
    async def _execute_task(
        self,
        content: str,
        session_key: str,
        context: dict = None,
        future: asyncio.Future = None,
        is_stream: bool = False
    ):
        """执行单个任务"""
        try:
            if is_stream:
                result = ""
                async for chunk in self.execute_stream(content, session_key, context):
                    result += chunk
                if future and not future.done():
                    future.set_result(result)
            else:
                result = await self.execute(content, session_key, context)
                if future and not future.done():
                    future.set_result(result)
        except Exception as e:
            logger.error(f"执行缓存任务失败: {e}")
            if future and not future.done():
                future.set_exception(e)
    
    async def queue_task(
        self,
        content: str,
        session_key: str,
        context: dict = None,
        is_stream: bool = False
    ) -> bool:
        """
        将任务加入队列（Gateway 不可用时）
        
        Args:
            content: 任务内容
            session_key: 会话 key
            context: 上下文
            is_stream: 是否流式
        
        Returns:
            是否成功加入队列
        """
        try:
            self._task_queue.put_nowait({
                "content": content,
                "session_key": session_key,
                "context": context or {},
                "is_stream": is_stream
            })
            logger.info(f"任务已加入队列: {session_key}")
            return True
        except asyncio.QueueFull:
            logger.error("任务队列已满")
            return False
    
    def get_queue_size(self) -> int:
        """获取队列大小"""
        return self._task_queue.qsize()
    
    # ─────────────────────────────────────────
    # 执行
    # ─────────────────────────────────────────
    
    async def _get_client(self) -> httpx.AsyncClient:
        """获取 HTTP 客户端"""
        if not self._active_url:
            raise Exception("No available Gateway")
        
        conn = self._connections[self._active_url]
        
        if conn._client is None:
            conn._client = httpx.AsyncClient(timeout=conn.timeout)
        
        return conn._client
    
    async def execute(
        self,
        content: str,
        session_key: str,
        context: dict = None
    ) -> str:
        """
        执行任务（同步方式）
        
        Args:
            content: 任务内容
            session_key: 会话 key
            context: 上下文
        
        Returns:
            执行结果
        
        Raises:
            Exception: Gateway 不可用或执行失败
        """
        if self._state != GatewayState.AVAILABLE:
            # 加入队列等待
            future = asyncio.Future()
            await self.queue_task(content, session_key, context, is_stream=False)
            # 等待结果
            return await future
        
        conn = self._connections[self._active_url]
        
        # 构建消息
        if context and context.get("name"):
            content = f"[{context['name']}] {content}"
        
        messages = [{"role": "user", "content": content}]
        if context and context.get("history"):
            messages = context["history"] + messages
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {conn.token}",
            "x-openclaw-session-key": session_key,
        }
        
        payload = {
            "model": "openclaw",
            "messages": messages,
            "stream": False,
        }
        
        try:
            client = await self._get_client()
            response = await client.post(
                f"{conn.url}/v1/chat/completions",
                json=payload,
                headers=headers
            )
            
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            elif response.status_code == 401:
                return "认证失败：token 无效"
            else:
                raise Exception(f"HTTP {response.status_code}: {response.text[:200]}")
                
        except httpx.TimeoutException:
            conn.consecutive_failures += 1
            if conn.consecutive_failures >= 3:
                conn.state = GatewayState.UNAVAILABLE
                self._state = GatewayState.UNAVAILABLE
            raise Exception("Gateway 执行超时")
        except Exception as e:
            conn.consecutive_failures += 1
            if conn.consecutive_failures >= 3:
                conn.state = GatewayState.UNAVAILABLE
                self._state = GatewayState.UNAVAILABLE
            raise
    
    async def execute_stream(
        self,
        content: str,
        session_key: str,
        context: dict = None,
        cancel_event: asyncio.Event = None
    ) -> AsyncGenerator[str, None]:
        """
        执行任务（流式方式）
        
        Args:
            content: 任务内容
            session_key: 会话 key
            context: 上下文
            cancel_event: 取消事件
        
        Yields:
            内容片段
        """
        if self._state != GatewayState.AVAILABLE:
            # 加入队列
            future = asyncio.Future()
            await self.queue_task(content, session_key, context, is_stream=True)
            future.result()  # 等待执行完成
            return
        
        conn = self._connections[self._active_url]
        
        # 构建消息
        if context and context.get("name"):
            content = f"[{context['name']}] {content}"
        
        messages = [{"role": "user", "content": content}]
        if context and context.get("history"):
            messages = context["history"] + messages
        
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {conn.token}",
            "x-openclaw-session-key": session_key,
        }
        
        payload = {
            "model": "openclaw",
            "messages": messages,
            "stream": True,
        }
        
        full_content = ""
        
        try:
            client = await self._get_client()
            async with client.stream(
                "POST",
                f"{conn.url}/v1/chat/completions",
                json=payload,
                headers=headers
            ) as response:
                async for line in response.aiter_lines():
                    # 检查取消
                    if cancel_event and cancel_event.is_set():
                        logger.info(f"流式执行被取消: {session_key}")
                        break
                    
                    if not line.strip():
                        continue
                    
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
                
                conn.last_success = datetime.now().timestamp()
                    
        except Exception as e:
            conn.consecutive_failures += 1
            if conn.consecutive_failures >= 3:
                conn.state = GatewayState.UNAVAILABLE
                self._state = GatewayState.UNAVAILABLE
            logger.error(f"Gateway 流式执行失败: {e}")
            raise
    
    # ─────────────────────────────────────────
    # 状态和回调
    # ─────────────────────────────────────────
    
    @property
    def state(self) -> GatewayState:
        """获取连接池状态"""
        return self._state
    
    @property
    def is_available(self) -> bool:
        """是否可用"""
        return self._state == GatewayState.AVAILABLE
    
    @property
    def active_url(self) -> Optional[str]:
        """获取当前活跃的 Gateway URL"""
        return self._active_url
    
    async def _notify_available(self):
        """通知 Gateway 可用"""
        for cb in self._on_available_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb()
                else:
                    cb()
            except Exception as e:
                logger.error(f"可用回调异常: {e}")
    
    async def _notify_unavailable(self):
        """通知 Gateway 不可用"""
        for cb in self._on_unavailable_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb()
                else:
                    cb()
            except Exception as e:
                logger.error(f"不可用回调异常: {e}")
    
    def on_gw_available(self, callback: Callable):
        """注册 Gateway 可用回调"""
        self._on_available_callbacks.append(callback)
    
    def on_gw_unavailable(self, callback: Callable):
        """注册 Gateway 不可用回调"""
        self._on_unavailable_callbacks.append(callback)
    
    def on_state_change(self, callback: Callable):
        """注册状态变化回调"""
        self._on_state_change_callbacks.append(callback)
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "state": self._state.value,
            "active_url": self._active_url,
            "urls": [
                {
                    "url": url,
                    "state": conn.state.value,
                    "consecutive_failures": conn.consecutive_failures,
                    "last_check": conn.last_check,
                    "last_success": conn.last_success
                }
                for url, conn in self._connections.items()
            ],
            "queue_size": self.get_queue_size(),
            "running": self._running
        }


# ─────────────────────────────────────────────
# 全局实例
# ─────────────────────────────────────────────

# 为了兼容旧代码，保留 gateway_bridge 作为全局实例
_gateway_pool: Optional[GatewayPool] = None


def get_gateway_pool() -> GatewayPool:
    """获取全局 Gateway 连接池"""
    global _gateway_pool
    if _gateway_pool is None:
        _gateway_pool = GatewayPool()
    return _gateway_pool


async def start_gateway_pool():
    """启动全局 Gateway 连接池"""
    pool = get_gateway_pool()
    await pool.start()
    return pool


async def stop_gateway_pool():
    """停止全局 Gateway 连接池"""
    global _gateway_pool
    if _gateway_pool:
        await _gateway_pool.stop()
        _gateway_pool = None
