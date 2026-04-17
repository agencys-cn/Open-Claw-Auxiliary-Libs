"""
Gateway 后端选择器

根据环境变量自动选择使用 OpenClaw Gateway 或 Hermes Agent Gateway。

使用方式:
    from gateway_selector import gateway_bridge, gateway_config, gateway_health
    
    # 自动选择后端（兼容原来的接口）
    result = await gateway_bridge.execute("写一个小说", session_key)
    async for chunk in gateway_bridge.execute_stream("写一个小说", session_key):
        print(chunk, end="", flush=True)
    
    # 健康检查
    is_alive = await gateway_health.health_check()
    
    # 配置信息
    print(gateway_config.url)
"""

import os
import asyncio
import logging
from typing import Optional, AsyncGenerator, Dict, Any
from enum import Enum

logger = logging.getLogger("a2a")


class GatewayType(Enum):
    """Gateway 类型"""
    OPENCLAW = "openclaw"
    HERMES = "hermes"


# 检测 Gateway 类型
_GATEWAY_TYPE_STR = os.getenv("GATEWAY_TYPE", "openclaw").lower()
if _GATEWAY_TYPE_STR not in ("openclaw", "hermes"):
    _GATEWAY_TYPE_STR = "openclaw"

GATEWAY_TYPE = GatewayType(_GATEWAY_TYPE_STR)


class UnifiedConfig:
    """统一配置对象"""
    def __init__(self, url: str):
        self.url = url


class UnifiedHealth:
    """统一健康检查对象"""
    def __init__(self, bridge):
        self._bridge = bridge
        self._is_alive = False
        self._clients = 0
    
    @property
    def is_alive(self) -> bool:
        return self._is_alive
    
    @property
    def _clients(self) -> int:
        return self._clients
    
    async def health_check(self) -> bool:
        self._is_alive = await self._bridge.health_check_impl()
        return self._is_alive


class UnifiedBridge:
    """统一 Bridge 对象"""
    
    def __init__(self, bridge_impl):
        self._impl = bridge_impl
        self._config = UnifiedConfig(bridge_impl._url)
        self._health = UnifiedHealth(bridge_impl)
    
    @property
    def config(self) -> UnifiedConfig:
        return self._config
    
    @property
    def health(self) -> UnifiedHealth:
        return self._health
    
    async def execute(self, content: str, session_key: str, context: Dict[str, Any] = None, **kwargs) -> str:
        return await self._impl.execute(content, session_key, context, **kwargs)
    
    async def execute_stream(self, content: str, session_key: str, context: Dict[str, Any] = None,
                            cancel_event=None, **kwargs) -> AsyncGenerator[str, None]:
        async for chunk in self._impl.execute_stream(content, session_key, context,
                                                       cancel_event=cancel_event, **kwargs):
            yield chunk
    
    async def health_check(self) -> bool:
        return await self._impl.health_check()
    
    def clear_session(self, session_key: str):
        return self._impl.clear_session(session_key)
    
    def get_history(self, session_key: str, limit: int = 10):
        return self._impl.get_history(session_key, limit)


class OpenClawBridgeImpl:
    """OpenClaw Bridge 实现"""
    
    def __init__(self):
        from gateway import gateway_bridge as _bridge
        from gateway import gateway_config as _config
        from gateway import gateway_health as _health
        
        self._bridge = _bridge
        self._config = _config
        self._health = _health
        self._url = _config.url
    
    async def execute(self, content: str, session_key: str, context: Dict[str, Any] = None, **kwargs) -> str:
        return await self._bridge.execute(content, session_key, context, **kwargs)
    
    async def execute_stream(self, content: str, session_key: str, context: Dict[str, Any] = None,
                            cancel_event=None, **kwargs) -> AsyncGenerator[str, None]:
        async for chunk in self._bridge.execute_stream(content, session_key, context,
                                                       cancel_event=cancel_event, **kwargs):
            yield chunk
    
    async def health_check(self) -> bool:
        return await self._health.health_check()
    
    async def health_check_impl(self) -> bool:
        return await self._health.health_check()
    
    def clear_session(self, session_key: str):
        return self._bridge.clear_session(session_key)
    
    def get_history(self, session_key: str, limit: int = 10):
        return self._bridge.get_history(session_key, limit)


class HermesBridgeImpl:
    """Hermes Bridge 实现"""
    
    def __init__(self):
        from gateway_hermes import HermesGatewayBridge as _BridgeClass
        self._bridge = _BridgeClass()
        self._url = os.getenv("HERMES_GATEWAY_URL", "http://127.0.0.1:8000")
    
    async def execute(self, content: str, session_key: str, context: Dict[str, Any] = None, **kwargs) -> str:
        return await self._bridge.execute(content, session_key, context, **kwargs)
    
    async def execute_stream(self, content: str, session_key: str, context: Dict[str, Any] = None,
                            cancel_event=None, **kwargs) -> AsyncGenerator[str, None]:
        async for chunk in self._bridge.execute_stream(content, session_key, context,
                                                       cancel_event=cancel_event, **kwargs):
            yield chunk
    
    async def health_check(self) -> bool:
        return await self._bridge.health_check()
    
    async def health_check_impl(self) -> bool:
        return await self._bridge.health_check()
    
    def clear_session(self, session_key: str):
        """Hermes 不支持清除会话"""
        pass
    
    def get_history(self, session_key: str, limit: int = 10):
        """Hermes 不支持获取历史"""
        return []


# 全局单例
_gateway_bridge_instance: Optional[UnifiedBridge] = None


def get_gateway_bridge() -> UnifiedBridge:
    """获取 Gateway Bridge 实例（单例）"""
    global _gateway_bridge_instance
    
    if _gateway_bridge_instance is None:
        if GATEWAY_TYPE == "hermes":
            impl = HermesBridgeImpl()
            _gateway_bridge_instance = UnifiedBridge(impl)
            logger.info(f"Hermes Gateway 已初始化: {_gateway_bridge_instance._config.url}")
        else:
            impl = OpenClawBridgeImpl()
            _gateway_bridge_instance = UnifiedBridge(impl)
            logger.info(f"OpenClaw Gateway 已初始化: {_gateway_bridge_instance._config.url}")
    
    return _gateway_bridge_instance


# 兼容性别名（延迟初始化）
class _LazyProxy:
    """延迟代理，在第一次访问时才初始化"""
    _instance = None
    
    def __getattr__(self, name):
        if _LazyProxy._instance is None:
            _LazyProxy._instance = get_gateway_bridge()
        return getattr(_LazyProxy._instance, name)


gateway_bridge = _LazyProxy()
gateway_config = property(lambda self: get_gateway_bridge().config)
gateway_health = property(lambda self: get_gateway_bridge().health)


def reset_gateway():
    """重置 Gateway（用于切换后端或重新初始化）"""
    global _gateway_bridge_instance
    _gateway_bridge_instance = None
