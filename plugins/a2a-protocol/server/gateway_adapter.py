"""
Gateway 适配器 - 支持 OpenClaw 和 Hermes 双后端

检测环境变量或自动探测后端类型，切换使用不同的 Gateway 实现。

使用场景:
    1. OPENCLAW_GATEWAY_URL → 使用 OpenClaw Gateway
    2. HERMES_GATEWAY_URL → 使用 Hermes Agent Gateway
    3. 自动检测（默认）

示例:
    >>> from gateway_adapter import get_gateway_adapter
    
    >>> # 自动检测
    >>> adapter = await get_gateway_adapter()
    
    >>> # 发送消息
    >>> result = await adapter.execute("写一个小说")
"""

import os
import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, AsyncGenerator, Dict, Any
from enum import Enum

import httpx

logger = logging.getLogger("a2a")


class GatewayType(Enum):
    """Gateway 类型"""
    OPENCLAW = "openclaw"
    HERMES = "hermes"
    UNKNOWN = "unknown"


@dataclass
class GatewayConfig:
    """Gateway 配置"""
    url: str
    timeout: float = 0.0
    token: str = ""
    type: GatewayType = GatewayType.UNKNOWN


class BaseGatewayAdapter(ABC):
    """Gateway 适配器基类"""
    
    @abstractmethod
    async def execute(
        self,
        content: str,
        session_key: str,
        context: Dict[str, Any] = None,
        **kwargs
    ) -> str:
        """同步执行，返回完整结果"""
        pass
    
    @abstractmethod
    async def execute_stream(
        self,
        content: str,
        session_key: str,
        context: Dict[str, Any] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """流式执行"""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        pass


class OpenClawGatewayAdapter(BaseGatewayAdapter):
    """OpenClaw Gateway 适配器"""
    
    def __init__(self, config: GatewayConfig):
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None
    
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.config.timeout)
        return self._client
    
    async def execute(
        self,
        content: str,
        session_key: str,
        context: Dict[str, Any] = None,
        **kwargs
    ) -> str:
        """OpenClaw Gateway 调用"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.token}",
            "x-openclaw-session-key": session_key,
        }
        
        messages = [{"role": "user", "content": content}]
        
        payload = {
            "model": "openclaw",
            "messages": messages,
            "stream": False,
        }
        
        async with self.client as client:
            response = await client.post(
                f"{self.config.url}/v1/chat/completions",
                json=payload,
                headers=headers
            )
            
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                raise Exception(f"OpenClaw Gateway error: {response.status_code}")
    
    async def execute_stream(
        self,
        content: str,
        session_key: str,
        context: Dict[str, Any] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """OpenClaw Gateway 流式调用"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.token}",
            "x-openclaw-session-key": session_key,
        }
        
        messages = [{"role": "user", "content": content}]
        
        payload = {
            "model": "openclaw",
            "messages": messages,
            "stream": True,
        }
        
        async with self.client.stream(
            "POST",
            f"{self.config.url}/v1/chat/completions",
            json=payload,
            headers=headers
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        import json
                        data = json.loads(data_str)
                        content_chunk = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if content_chunk:
                            yield content_chunk
                    except:
                        continue
    
    async def health_check(self) -> bool:
        """OpenClaw 健康检查"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.config.url}/health")
                return response.status_code == 200
        except:
            return False


class HermesGatewayAdapter(BaseGatewayAdapter):
    """Hermes Agent Gateway 适配器"""
    
    def __init__(self, config: GatewayConfig):
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None
    
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.config.timeout)
        return self._client
    
    async def execute(
        self,
        content: str,
        session_key: str,
        context: Dict[str, Any] = None,
        **kwargs
    ) -> str:
        """Hermes Gateway 调用"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.token}",
        }
        
        messages = [{"role": "user", "content": content}]
        
        # Hermes 使用类似的 API 格式
        payload = {
            "model": "auto",
            "messages": messages,
            "stream": False,
        }
        
        async with self.client as client:
            # Hermes 可能有不同的端点
            response = await client.post(
                f"{self.config.url}/v1/chat/completions",
                json=payload,
                headers=headers
            )
            
            if response.status_code == 200:
                data = response.json()
                return data["choices"][0]["message"]["content"]
            else:
                raise Exception(f"Hermes Gateway error: {response.status_code}")
    
    async def execute_stream(
        self,
        content: str,
        session_key: str,
        context: Dict[str, Any] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """Hermes Gateway 流式调用"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.token}",
        }
        
        messages = [{"role": "user", "content": content}]
        
        payload = {
            "model": "auto",
            "messages": messages,
            "stream": True,
        }
        
        async with self.client.stream(
            "POST",
            f"{self.config.url}/v1/chat/completions",
            json=payload,
            headers=headers
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        import json
                        data = json.loads(data_str)
                        content_chunk = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if content_chunk:
                            yield content_chunk
                    except:
                        continue
    
    async def health_check(self) -> bool:
        """Hermes 健康检查"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Hermes 可能使用不同端点
                response = await client.get(f"{self.config.url}/health")
                return response.status_code == 200
        except:
            return False


class GatewayAdapterFactory:
    """Gateway 适配器工厂"""
    
    @staticmethod
    def detect_gateway_type() -> GatewayType:
        """检测 Gateway 类型"""
        # 检查环境变量
        if os.getenv("OPENCLAW_GATEWAY_URL"):
            return GatewayType.OPENCLAW
        if os.getenv("HERMES_GATEWAY_URL"):
            return GatewayType.HERMES
        
        # 默认 OpenClaw
        return GatewayType.OPENCLAW
    
    @staticmethod
    def create_config() -> GatewayConfig:
        """创建 Gateway 配置"""
        # OpenClaw 配置
        openclaw_url = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")
        openclaw_token = os.getenv("OPENCLAW_GATEWAY_TOKEN", "")
        openclaw_timeout = float(os.getenv("OPENCLAW_GATEWAY_TIMEOUT", "0"))
        
        # Hermes 配置
        hermes_url = os.getenv("HERMES_GATEWAY_URL", "")
        hermes_token = os.getenv("HERMES_GATEWAY_TOKEN", "")
        
        # 检测类型
        gateway_type = GatewayAdapterFactory.detect_gateway_type()
        
        if gateway_type == GatewayType.HERMES and hermes_url:
            return GatewayConfig(
                url=hermes_url,
                timeout=120.0,
                token=hermes_token,
                type=GatewayType.HERMES
            )
        
        # 默认 OpenClaw
        return GatewayConfig(
            url=openclaw_url,
            timeout=openclaw_timeout,
            token=openclaw_token,
            type=GatewayType.OPENCLAW
        )
    
    @staticmethod
    def create_adapter(config: GatewayConfig = None) -> BaseGatewayAdapter:
        """创建适配器"""
        if config is None:
            config = GatewayAdapterFactory.create_config()
        
        if config.type == GatewayType.HERMES:
            return HermesGatewayAdapter(config)
        
        return OpenClawGatewayAdapter(config)


# 全局实例
_adapter: Optional[BaseGatewayAdapter] = None
_adapter_lock = asyncio.Lock()


async def get_gateway_adapter() -> BaseGatewayAdapter:
    """获取全局 Gateway 适配器（单例）"""
    global _adapter
    
    if _adapter is not None:
        return _adapter
    
    async with _adapter_lock:
        if _adapter is None:
            config = GatewayAdapterFactory.create_config()
            _adapter = GatewayAdapterFactory.create_adapter(config)
            logger.info(f"Gateway 适配器已初始化: {config.type.value} ({config.url})")
        
        return _adapter


async def close_gateway_adapter():
    """关闭全局 Gateway 适配器"""
    global _adapter
    
    if _adapter is not None and hasattr(_adapter, '_client') and _adapter._client:
        await _adapter._client.aclose()
        _adapter = None
