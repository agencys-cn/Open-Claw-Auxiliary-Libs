"""
心跳保活
A2A 协议扩展 - SSE/WebSocket 长连接保活

概述:
    1. SSE 心跳 - 定期发送 comment 行防止连接超时
    2. WebSocket 心跳 - 定期发送 ping/pong 帧
    3. 任务进度心跳 - 长时间任务定期推送进度
    4. Gateway 心跳 - 监控 Gateway 可用性

心跳间隔:
    - SSE 心跳: 30 秒（防止代理超时）
    - WebSocket ping: 30 秒
    - 任务进度: 30 秒（流式传输时）
    - Gateway 健康检查: 60 秒

示例:
    >>> heartbeat = HeartbeatManager()
    >>> 
    >>> # SSE 心跳
    >>> async for line in sse_with_heartbeat(event_stream):
    ...     yield line
    >>> 
    >>> # WebSocket 心跳
    >>> heartbeat.start_ws_heartbeat(ws_client)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Set, Any, AsyncGenerator
from enum import Enum

logger = logging.getLogger("a2a")


class HeartbeatType(Enum):
    """心跳类型"""
    SSE = "sse"                     # SSE comment 行
    WEBSOCKET_PING = "ws_ping"     # WebSocket ping
    TASK_PROGRESS = "task_progress"  # 任务进度
    GATEWAY_HEALTH = "gateway_health"  # Gateway 健康检查


@dataclass
class HeartbeatConfig:
    """
    心跳配置
    
    Attributes:
        sse_interval: SSE 心跳间隔（秒），默认 30
        ws_interval: WebSocket ping 间隔（秒），默认 30
        task_progress_interval: 任务进度心跳间隔（秒），默认 30
        gateway_check_interval: Gateway 健康检查间隔（秒），默认 60
        enable_sse: 是否启用 SSE 心跳
        enable_ws: 是否启用 WebSocket 心跳
        enable_task_progress: 是否启用任务进度心跳
        enable_gateway_check: 是否启用 Gateway 健康检查
    """
    sse_interval: int = 30
    ws_interval: int = 30
    task_progress_interval: int = 30
    gateway_check_interval: int = 60
    enable_sse: bool = True
    enable_ws: bool = True
    enable_task_progress: bool = True
    enable_gateway_check: bool = True


class HeartbeatRecord:
    """心跳记录"""
    
    def __init__(self, client_id: str, hb_type: HeartbeatType):
        self.client_id = client_id
        self.hb_type = hb_type
        self.last_heartbeat: float = time.monotonic()
        self.total_heartbeats = 0
        self.missed_heartbeats = 0
    
    def pulse(self):
        """更新心跳"""
        self.last_heartbeat = time.monotonic()
        self.total_heartbeats += 1
        self.missed_heartbeats = 0
    
    def mark_missed(self):
        """标记丢失"""
        self.missed_heartbeats += 1
    
    @property
    def is_alive(self) -> bool:
        """是否存活"""
        return self.missed_heartbeats < 3


class HeartbeatManager:
    """
    心跳管理器
    
    管理各种类型的心跳：
    1. SSE 心跳
    2. WebSocket 心跳
    3. 任务进度心跳
    4. Gateway 健康检查
    
    Attributes:
        config: 心跳配置
        _records: 心跳记录
        _tasks: 活跃的心跳任务
        _cleanup_task: 清理任务
    
    Example:
        >>> manager = HeartbeatManager()
        >>> 
        >>> # SSE 心跳
        >>> async def sse_with_heartbeat(event_stream, client_id):
        ...     async for line in manager.sse_heartbeat(event_stream, client_id):
        ...         yield line
        >>> 
        >>> # WebSocket 心跳
        >>> manager.start_ws_heartbeat(ws_client, "client_123")
    """
    
    def __init__(self, config: Optional[HeartbeatConfig] = None):
        """
        初始化心跳管理器
        
        Args:
            config: 心跳配置
        """
        self.config = config or HeartbeatConfig()
        self._records: Dict[str, HeartbeatRecord] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        
        # SSE comment 行（不会触发客户端事件）
        self._sse_comment = ": heartbeat\n\n"
    
    async def start(self):
        """启动心跳管理器"""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("心跳管理器已启动")
    
    async def stop(self):
        """停止心跳管理器"""
        # 取消所有心跳任务
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None
        
        logger.info("心跳管理器已停止")
    
    async def _cleanup_loop(self):
        """定期清理过期记录"""
        while True:
            try:
                await asyncio.sleep(60)
                await self._cleanup()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳清理异常: {e}")
    
    async def _cleanup(self):
        """清理过期的心跳记录"""
        async with self._lock:
            now = time.monotonic()
            to_remove = []
            
            for key, record in self._records.items():
                # 超过3分钟无心跳视为断开
                if now - record.last_heartbeat > 180:
                    to_remove.append(key)
            
            for key in to_remove:
                del self._records[key]
            
            if to_remove:
                logger.debug(f"清理了 {len(to_remove)} 条心跳记录")
    
    def _get_key(self, client_id: str, hb_type: HeartbeatType) -> str:
        """生成心跳记录 key"""
        return f"{hb_type.value}:{client_id}"
    
    async def register(self, client_id: str, hb_type: HeartbeatType) -> HeartbeatRecord:
        """
        注册心跳
        
        Args:
            client_id: 客户端 ID
            hb_type: 心跳类型
        
        Returns:
            心跳记录
        """
        key = self._get_key(client_id, hb_type)
        
        async with self._lock:
            if key not in self._records:
                self._records[key] = HeartbeatRecord(client_id, hb_type)
            return self._records[key]
    
    async def unregister(self, client_id: str, hb_type: HeartbeatType):
        """
        注销心跳
        
        Args:
            client_id: 客户端 ID
            hb_type: 心跳类型
        """
        key = self._get_key(client_id, hb_type)
        
        async with self._lock:
            if key in self._records:
                del self._records[key]
        
        # 取消心跳任务
        task_key = f"{client_id}:{hb_type.value}"
        if task_key in self._tasks:
            self._tasks[task_key].cancel()
            del self._tasks[task_key]
    
    async def pulse(self, client_id: str, hb_type: HeartbeatType):
        """
        发送心跳
        
        Args:
            client_id: 客户端 ID
            hb_type: 心跳类型
        """
        key = self._get_key(client_id, hb_type)
        
        async with self._lock:
            if key in self._records:
                self._records[key].pulse()
    
    async def sse_heartbeat(self, event_stream: AsyncGenerator, 
                           client_id: str) -> AsyncGenerator[str, None]:
        """
        SSE 心跳生成器
        
        在原始事件流中插入心跳 comment 行。
        
        Args:
            event_stream: 原始事件流
            client_id: 客户端 ID
        
        Yields:
            SSE 格式的事件行
        
        Example:
            >>> async def events():
            ...     yield "data: hello\\n\\n"
            ...     yield "data: world\\n\\n"
            >>> 
            >>> async for line in heartbeat_manager.sse_heartbeat(events(), "client_1"):
            ...     print(line, end="")
        """
        record = await self.register(client_id, HeartbeatType.SSE)
        last_heartbeat = time.monotonic()
        
        async for item in event_stream:
            yield item
            
            # 检查是否需要发送心跳
            now = time.monotonic()
            if now - last_heartbeat >= self.config.sse_interval:
                yield self._sse_comment
                last_heartbeat = now
                await self.pulse(client_id, HeartbeatType.SSE)
        
        # 清理
        await self.unregister(client_id, HeartbeatType.SSE)
    
    async def ws_heartbeat_loop(self, ws_client, client_id: str):
        """
        WebSocket 心跳循环
        
        定期向 WebSocket 客户端发送 ping。
        
        Args:
            ws_client: WebSocket 客户端
            client_id: 客户端 ID
        """
        record = await self.register(client_id, HeartbeatType.WEBSOCKET_PING)
        
        try:
            while True:
                await asyncio.sleep(self.config.ws_interval)
                
                try:
                    await ws_client.send("ping", {
                        "timestamp": datetime.now().isoformat(),
                        "type": "heartbeat"
                    })
                    await self.pulse(client_id, HeartbeatType.WEBSOCKET_PING)
                except Exception as e:
                    logger.warning(f"WebSocket ping 失败: {client_id}, {e}")
                    break
        except asyncio.CancelledError:
            pass
        finally:
            await self.unregister(client_id, HeartbeatType.WEBSOCKET_PING)
    
    def start_ws_heartbeat(self, ws_client, client_id: str):
        """
        启动 WebSocket 心跳
        
        Args:
            ws_client: WebSocket 客户端
            client_id: 客户端 ID
        """
        task_key = f"{client_id}:ws"
        if task_key not in self._tasks:
            self._tasks[task_key] = asyncio.create_task(
                self.ws_heartbeat_loop(ws_client, client_id)
            )
    
    async def task_progress_heartbeat(self, task_id: str, 
                                       elapsed: int,
                                       partial: str = "") -> str:
        """
        生成任务进度心跳事件
        
        Args:
            task_id: 任务 ID
            elapsed: 已用时间（秒）
            partial: 部分结果（可选）
        
        Returns:
            SSE 格式的心跳事件
        """
        await self.pulse(task_id, HeartbeatType.TASK_PROGRESS)
        
        data = {
            "taskId": task_id,
            "type": "heartbeat",
            "elapsed": elapsed,
            "timestamp": datetime.now().isoformat()
        }
        
        if partial:
            data["partial"] = partial[-100:]  # 只保留最后100字符
        
        return f"data: {json.dumps(data)}\n\n"
    
    def get_record(self, client_id: str, hb_type: HeartbeatType) -> Optional[HeartbeatRecord]:
        """获取心跳记录"""
        key = self._get_key(client_id, hb_type)
        return self._records.get(key)
    
    def get_all_records(self) -> list:
        """获取所有心跳记录"""
        return [
            {
                "client_id": r.client_id,
                "type": r.hb_type.value,
                "is_alive": r.is_alive,
                "total_heartbeats": r.total_heartbeats,
                "missed_heartbeats": r.missed_heartbeats,
            }
            for r in self._records.values()
        ]


# ─────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────

# 全局心跳管理器
heartbeat_manager = HeartbeatManager()


async def sse_with_heartbeat(event_stream: AsyncGenerator, 
                              client_id: str) -> AsyncGenerator[str, None]:
    """
    SSE 心跳便捷函数
    
    Example:
        >>> async for line in sse_with_heartbeat(events(), "client_1"):
        ...     yield line
    """
    async for line in heartbeat_manager.sse_heartbeat(event_stream, client_id):
        yield line


# 需要导入 json
import json
