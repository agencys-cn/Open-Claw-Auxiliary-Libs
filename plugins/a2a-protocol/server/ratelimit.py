"""
限流保护
A2A 协议扩展 - 请求频率控制

概述:
    防止恶意请求和误用，保护 Gateway 资源。
    支持按 IP、Client ID、API Key 等维度限流。

限流算法:
    - 令牌桶算法 (Token Bucket)
    - 滑动窗口 (Sliding Window)

示例:
    >>> limiter = RateLimiter()
    >>> 
    >>> # 检查请求是否允许
    >>> if await limiter.check("client_123"):
    ...     await process_request()
    ... else:
    ...     raise RateLimitExceeded("请求过于频繁")
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import hashlib

logger = logging.getLogger("a2a")


@dataclass
class RateLimitConfig:
    """
    限流配置
    
    Attributes:
        requests_per_minute: 每分钟允许请求数，默认 60
        requests_per_hour: 每小时允许请求数，默认 1000
        burst_size: 突发容量（短期额外允许），默认 10
        enable_per_ip: 是否启用按 IP 限流，默认 True
        enable_per_client: 是否启用按 Client ID 限流，默认 True
        enable_per_key: 是否启用按 API Key 限流，默认 True
        cleanup_interval: 清理过期记录间隔（秒），默认 300
    """
    requests_per_minute: int = 60
    requests_per_hour: int = 1000
    burst_size: int = 10
    enable_per_ip: bool = True
    enable_per_client: bool = True
    enable_per_key: bool = True
    cleanup_interval: int = 300


class RateLimitExceeded(Exception):
    """
    限流异常
    
    当请求超过限制时抛出。
    
    Attributes:
        retry_after: 多少秒后可重试
        limit_type: 限制类型 ("ip", "client", "key")
        identifier: 被限流的标识符
    """
    
    def __init__(self, message: str, retry_after: int = 60, 
                 limit_type: str = "ip", identifier: str = ""):
        super().__init__(message)
        self.retry_after = retry_after
        self.limit_type = limit_type
        self.identifier = identifier


class TokenBucket:
    """
    令牌桶算法实现
    
    令牌桶是一种流量控制算法，允许突发流量，但长期来看是平稳的。
    
    Attributes:
        capacity: 桶容量（最大令牌数）
        refill_rate: 每秒补充的令牌数
        tokens: 当前令牌数
        last_refill: 最后补充时间
    
    Example:
        >>> bucket = TokenBucket(capacity=10, refill_rate=1.0)
        >>> 
        >>> # 尝试获取令牌
        >>> if bucket.try_acquire():
        ...     print("请求通过")
        ... else:
        ...     print("请求被限流")
    """
    
    def __init__(self, capacity: int, refill_rate: float):
        """
        初始化令牌桶
        
        Args:
            capacity: 桶容量（最大令牌数）
            refill_rate: 每秒补充的令牌数
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
    
    def try_acquire(self, tokens: int = 1) -> bool:
        """
        尝试获取令牌
        
        Args:
            tokens: 需要获取的令牌数
        
        Returns:
            是否获取成功
        """
        self._refill()
        
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False
    
    def _refill(self):
        """补充令牌"""
        now = time.monotonic()
        elapsed = now - self.last_refill
        
        # 计算应补充的令牌数
        new_tokens = elapsed * self.refill_rate
        self.tokens = min(self.capacity, self.tokens + new_tokens)
        self.last_refill = now
    
    @property
    def available_tokens(self) -> float:
        """当前可用令牌数"""
        self._refill()
        return self.tokens
    
    def reset(self):
        """重置桶"""
        self.tokens = float(self.capacity)
        self.last_refill = time.monotonic()


class SlidingWindow:
    """
    滑动窗口算法实现
    
    滑动窗口是一种更精确的限流算法，可以限制任意时间窗口内的请求数。
    
    Attributes:
        window_size: 窗口大小（秒）
        max_requests: 窗口内最大请求数
    
    Example:
        >>> window = SlidingWindow(window_size=60, max_requests=60)
        >>> 
        >>> if window.try_request():
        ...     print("请求通过")
        ... else:
        ...     print("请求被限流")
    """
    
    def __init__(self, window_size: int, max_requests: int):
        """
        初始化滑动窗口
        
        Args:
            window_size: 窗口大小（秒）
            max_requests: 窗口内最大请求数
        """
        self.window_size = window_size
        self.max_requests = max_requests
        self.requests: list = []  # 时间戳列表
    
    def try_request(self) -> bool:
        """
        尝试记录请求
        
        Returns:
            是否成功（未超限）
        """
        now = datetime.now()
        cutoff = now - timedelta(seconds=self.window_size)
        
        # 清理过期的请求记录
        self.requests = [t for t in self.requests if t > cutoff]
        
        if len(self.requests) < self.max_requests:
            self.requests.append(now)
            return True
        return False
    
    def get_remaining(self) -> int:
        """获取剩余请求数"""
        now = datetime.now()
        cutoff = now - timedelta(seconds=self.window_size)
        self.requests = [t for t in self.requests if t > cutoff]
        return max(0, self.max_requests - len(self.requests))
    
    def reset(self):
        """重置窗口"""
        self.requests = []


class RateLimitRecord:
    """限流记录"""
    
    def __init__(self, identifier: str, limit_type: str, 
                 config: RateLimitConfig):
        self.identifier = identifier
        self.limit_type = limit_type
        self.config = config
        
        # 按分钟限流
        self.minute_window = SlidingWindow(60, config.requests_per_minute)
        # 按小时限流
        self.hour_window = SlidingWindow(3600, config.requests_per_hour)
        # 突发流量桶
        self.burst_bucket = TokenBucket(
            config.burst_size, 
            config.requests_per_minute / 60.0  # 每秒补充速率
        )
        
        self.total_requests = 0
        self.blocked_requests = 0
        self.first_request_time: Optional[datetime] = None
        self.last_request_time: Optional[datetime] = None
    
    def try_request(self) -> Tuple[bool, int]:
        """
        尝试请求
        
        Returns:
            (是否成功, 剩余请求数)
        """
        now = datetime.now()
        
        if self.first_request_time is None:
            self.first_request_time = now
        
        # 检查分钟限流
        if not self.minute_window.try_request():
            self.blocked_requests += 1
            return False, 0
        
        # 检查小时限流
        if not self.hour_window.try_request():
            self.blocked_requests += 1
            return False, 0
        
        # 检查突发桶
        if not self.burst_bucket.try_acquire():
            self.blocked_requests += 1
            return False, 0
        
        self.total_requests += 1
        self.last_request_time = now
        return True, self.minute_window.get_remaining()
    
    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "identifier": self.identifier,
            "limit_type": self.limit_type,
            "total_requests": self.total_requests,
            "blocked_requests": self.blocked_requests,
            "minute_remaining": self.minute_window.get_remaining(),
            "hour_remaining": self.hour_window.get_remaining(),
            "first_request": self.first_request_time.isoformat() if self.first_request_time else None,
            "last_request": self.last_request_time.isoformat() if self.last_request_time else None,
        }


class RateLimiter:
    """
    限流器
    
    支持多种维度的限流：IP、Client ID、API Key。
    
    Attributes:
        config: 限流配置
        _records: 限流记录字典
        _cleanup_task: 清理任务
    
    Example:
        >>> limiter = RateLimiter()
        >>> 
        >>> # 中间件中使用
        >>> async def handle_request(request):
        ...     client_ip = request.client.host
        ...     allowed, remaining = await limiter.check(client_ip)
        ...     if not allowed:
        ...         raise RateLimitExceeded(
        ...             f"请求超过限制",
        ...             retry_after=60
        ...         )
    """
    
    def __init__(self, config: Optional[RateLimitConfig] = None):
        """
        初始化限流器
        
        Args:
            config: 限流配置
        """
        self.config = config or RateLimitConfig()
        self._records: Dict[str, RateLimitRecord] = {}
        self._cleanup_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
    
    async def start(self):
        """启动限流器（启动清理任务）"""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("限流器已启动")
    
    async def stop(self):
        """停止限流器"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            self._cleanup_task = None
            logger.info("限流器已停止")
    
    async def _cleanup_loop(self):
        """定期清理过期记录"""
        while True:
            try:
                await asyncio.sleep(self.config.cleanup_interval)
                await self._cleanup()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"限流记录清理异常: {e}")
    
    async def _cleanup(self):
        """清理过期记录"""
        async with self._lock:
            now = datetime.now()
            hour_ago = now - timedelta(hours=1)
            
            to_remove = []
            for key, record in self._records.items():
                # 删除1小时无活动的记录
                if (record.last_request_time is not None 
                    and record.last_request_time < hour_ago):
                    to_remove.append(key)
            
            for key in to_remove:
                del self._records[key]
            
            if to_remove:
                logger.debug(f"清理了 {len(to_remove)} 条限流记录")
    
    def _get_identifier(self, ip: str = "", client_id: str = "", 
                       api_key: str = "") -> str:
        """生成限流标识符"""
        parts = []
        
        if self.config.enable_per_ip and ip:
            parts.append(f"ip:{ip}")
        
        if self.config.enable_per_client and client_id:
            parts.append(f"client:{client_id}")
        
        if self.config.enable_per_key and api_key:
            key_hash = hashlib.md5(api_key.encode()).hexdigest()[:8]
            parts.append(f"key:{key_hash}")
        
        if not parts:
            parts = ["default"]
        
        return "|".join(parts)
    
    async def check(self, ip: str = "", client_id: str = "", 
                   api_key: str = "") -> Tuple[bool, int]:
        """
        检查请求是否允许
        
        Args:
            ip: 客户端 IP
            client_id: 客户端 ID
            api_key: API Key
        
        Returns:
            (是否允许, 剩余请求数)
        """
        identifier = self._get_identifier(ip, client_id, api_key)
        
        async with self._lock:
            if identifier not in self._records:
                self._records[identifier] = RateLimitRecord(
                    identifier,
                    "mixed",
                    self.config
                )
            
            record = self._records[identifier]
            allowed, remaining = record.try_request()
            
            if not allowed:
                logger.warning(
                    f"限流触发: {identifier}, "
                    f"type={record.limit_type}, "
                    f"total={record.total_requests}"
                )
            
            return allowed, remaining
    
    async def get_record(self, ip: str = "", client_id: str = "",
                        api_key: str = "") -> Optional[dict]:
        """
        获取限流记录
        
        Args:
            ip: 客户端 IP
            client_id: 客户端 ID
            api_key: API Key
        
        Returns:
            限流记录字典，如果不存在返回 None
        """
        identifier = self._get_identifier(ip, client_id, api_key)
        
        async with self._lock:
            if identifier in self._records:
                return self._records[identifier].to_dict()
        return None
    
    async def get_all_records(self) -> list:
        """获取所有限流记录"""
        async with self._lock:
            return [r.to_dict() for r in self._records.values()]
    
    async def reset(self, ip: str = "", client_id: str = "", 
                   api_key: str = ""):
        """
        重置限流记录
        
        Args:
            ip: 客户端 IP
            client_id: 客户端 ID
            api_key: API Key
        """
        identifier = self._get_identifier(ip, client_id, api_key)
        
        async with self._lock:
            if identifier in self._records:
                del self._records[identifier]
                logger.info(f"限流记录已重置: {identifier}")
    
    def get_retry_after(self) -> int:
        """
        获取建议的重试间隔（秒）
        
        Returns:
            重试间隔秒数
        """
        return 60  # 默认1分钟


# ─────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────

# 全局限流器实例
rate_limiter = RateLimiter()


async def check_rate_limit(ip: str = "", client_id: str = "", 
                          api_key: str = "") -> Tuple[bool, int]:
    """
    快捷限流检查函数
    
    Example:
        >>> allowed, remaining = await check_rate_limit(ip="192.168.1.1")
        >>> if not allowed:
        ...     raise RateLimitExceeded("请求过于频繁")
    """
    return await rate_limiter.check(ip, client_id, api_key)


# 限流异常快捷方式
RateLimitError = RateLimitExceeded
