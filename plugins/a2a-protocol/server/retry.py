"""
重试机制
A2A 协议扩展 - Gateway 调用重试

概述:
    当 Gateway 调用超时或失败时，自动重试。
    支持指数退避、限流检测、最大重试次数控制。

使用场景:
    1. Gateway 响应超时
    2. Gateway 暂时不可用
    3. 网络抖动

示例:
    >>> retry = RetryPolicy(max_retries=3, base_delay=1.0, max_delay=30.0)
    >>> 
    >>> async def call_with_retry():
    ...     async for attempt in retry:
    ...         try:
    ...             return await gateway_bridge.execute(content, session_key)
    ...         except GatewayError as e:
    ...             retry.on_failure(e)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, AsyncGenerator, Callable, Any
from datetime import datetime, timedelta
from enum import Enum

logger = logging.getLogger("a2a")


class RetryStrategy(Enum):
    """重试策略"""
    EXPONENTIAL = "exponential"  # 指数退避
    LINEAR = "linear"            # 线性退避
    CONSTANT = "constant"        # 固定间隔


@dataclass
class RetryConfig:
    """
    重试配置
    
    Attributes:
        max_retries: 最大重试次数，默认 3
        base_delay: 基础延迟（秒），默认 1.0
        max_delay: 最大延迟（秒），默认 30.0
        strategy: 重试策略
        enable_rate_limit_detection: 是否启用限流检测，默认 True
        rate_limit_cooldown: 限流冷却时间（秒），默认 60.0
    """
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    enable_rate_limit_detection: bool = True
    rate_limit_cooldown: float = 60.0


class RetryState:
    """重试状态"""
    
    def __init__(self, config: RetryConfig):
        self.config = config
        self.attempt = 0
        self.total_failures = 0
        self.last_failure: Optional[Exception] = None
        self.last_failure_time: Optional[datetime] = None
        self._rate_limited_until: Optional[datetime] = None
    
    @property
    def is_rate_limited(self) -> bool:
        """是否处于限流冷却中"""
        if self._rate_limited_until is None:
            return False
        return datetime.now() < self._rate_limited_until
    
    def set_rate_limited(self):
        """设置限流状态"""
        self._rate_limited_until = datetime.now() + timedelta(
            seconds=self.config.rate_limit_cooldown
        )
        logger.warning(f"触发限流保护，冷却 {self.config.rate_limit_cooldown} 秒")
    
    def calculate_delay(self) -> float:
        """计算延迟时间"""
        if self.strategy == RetryStrategy.EXPONENTIAL:
            delay = self.config.base_delay * (2 ** self.attempt)
        elif self.strategy == RetryStrategy.LINEAR:
            delay = self.config.base_delay * (self.attempt + 1)
        else:  # CONSTANT
            delay = self.config.base_delay
        
        return min(delay, self.config.max_delay)
    
    def __repr__(self):
        return (f"RetryState(attempt={self.attempt}, "
                f"failures={self.total_failures}, "
                f"rate_limited={self.is_rate_limited})")


class RetryPolicy:
    """
    重试策略上下文管理器
    
    支持 async for 语法：
    
    Example:
        >>> retry = RetryPolicy(max_retries=3)
        >>> async for attempt in retry:
        ...     try:
        ...         result = await do_something()
        ...         retry.on_success()
        ...         return result
        ...     except RetryableError as e:
        ...         retry.on_failure(e)
    
    Attributes:
        config: 重试配置
        state: 当前重试状态
    
    Properties:
        attempt: 当前尝试次数（从 1 开始）
        remaining_retries: 剩余重试次数
        should_retry: 是否应该继续重试
    """
    
    def __init__(self, config: Optional[RetryConfig] = None, **kwargs):
        """
        初始化重试策略
        
        Args:
            config: 重试配置（优先使用）
            **kwargs: 配置参数（config 为 None 时使用）
        """
        self.config = config or RetryConfig(**kwargs)
        self.state = RetryState(self.config)
    
    def __aiter__(self):
        """异步迭代器入口"""
        self.state.attempt = 0
        return self
    
    async def __anext__(self) -> int:
        """异步迭代下一个"""
        # 检查限流
        if self.state.is_rate_limited:
            raise StopAsyncIteration
        
        self.state.attempt += 1
        
        if self.state.attempt > self.config.max_retries + 1:
            raise StopAsyncIteration
        
        return self.state.attempt
    
    @property
    def should_retry(self) -> bool:
        """是否应该继续重试"""
        return (
            self.state.attempt <= self.config.max_retries
            and not self.state.is_rate_limited
        )
    
    @property
    def remaining_retries(self) -> int:
        """剩余重试次数"""
        return max(0, self.config.max_retries - self.state.attempt + 1)
    
    def on_success(self):
        """
        标记成功
        
        重置重试状态。
        """
        logger.debug(f"Retry success after {self.state.attempt} attempts")
        self.state.attempt = 0
    
    def on_failure(self, error: Exception, is_retryable: bool = True):
        """
        标记失败
        
        Args:
            error: 异常对象
            is_retryable: 是否可重试
        """
        self.state.total_failures += 1
        self.state.last_failure = error
        self.state.last_failure_time = datetime.now()
        
        # 检测限流（HTTP 429）
        if self.config.enable_rate_limit_detection:
            error_str = str(error).lower()
            if "429" in error_str or "rate limit" in error_str or "too many requests" in error_str:
                self.state.set_rate_limited()
        
        if is_retryable and self.should_retry:
            delay = self.state.calculate_delay()
            logger.warning(
                f"Retryable failure (attempt {self.state.attempt}, "
                f"delay={delay:.1f}s): {error}"
            )
        else:
            logger.error(f"Non-retryable failure: {error}")
    
    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        """
        执行函数（带重试）
        
        Args:
            func: 异步函数
            *args: 位置参数
            **kwargs: 关键字参数
        
        Returns:
            函数返回值
        
        Raises:
            最后一次失败的异常
        
        Example:
            >>> result = await retry.execute(
            ...     gateway_bridge.execute,
            ...     content="hello",
            ...     session_key="test"
            ... )
        """
        last_error = None
        
        async for attempt in self:
            try:
                result = await func(*args, **kwargs)
                self.on_success()
                return result
            except Exception as e:
                last_error = e
                is_retryable = self._is_retryable_error(e)
                self.on_failure(e, is_retryable)
                
                if self.should_retry:
                    delay = self.state.calculate_delay()
                    await asyncio.sleep(delay)
        
        raise last_error
    
    @staticmethod
    def _is_retryable_error(error: Exception) -> bool:
        """判断错误是否可重试"""
        error_str = str(error).lower()
        
        # 不可重试的错误
        non_retryable = [
            "401", "403", "404",  # 认证/权限/资源问题
            "invalid request",
            "invalid json",
            "parse error",
        ]
        
        for keyword in non_retryable:
            if keyword in error_str:
                return False
        
        # 超时、连接问题、限流都可重试
        retryable = [
            "timeout", "timed out",
            "connection", "connect",
            "429", "rate limit",
            "500", "502", "503", "504",  # 服务器错误
            "temporary", "unavailable",
        ]
        
        for keyword in retryable:
            if keyword in error_str:
                return True
        
        # 默认可重试
        return True


# ─────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────

async def retry_call(func: Callable, *args, max_retries: int = 3, **kwargs) -> Any:
    """
    快捷重试函数
    
    Args:
        func: 异步函数
        *args: 位置参数
        max_retries: 最大重试次数
        **kwargs: 关键字参数
    
    Returns:
        函数返回值
    
    Example:
        >>> result = await retry_call(
        ...     gateway_bridge.execute,
        ...     content="hello",
        ...     session_key="test",
        ...     max_retries=3
        ... )
    """
    policy = RetryPolicy(max_retries=max_retries)
    return await policy.execute(func, *args, **kwargs)


# 全局默认重试策略
default_retry_policy = RetryPolicy()
