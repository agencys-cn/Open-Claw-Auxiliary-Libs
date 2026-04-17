"""
Hermes Gateway Bridge

Hermes Agent 的 Gateway 桥接器。

Hermes Agent 是一个自改进 AI Agent，支持多种消息平台。
这个桥接器让 A2A 协议可以与 Hermes Gateway 通信。

API 格式:
    Hermes 使用与 OpenAI 兼容的 API 格式。

示例:
    >>> bridge = HermesGatewayBridge()
    >>> result = await bridge.execute("写一个小说", "a2a:session1")
    >>> async for chunk in bridge.execute_stream("写一个小说", "a2a:session1"):
    ...     print(chunk, end="", flush=True)
"""

import os
import asyncio
import json
import logging
from typing import Optional, AsyncGenerator, Dict, Any
from dataclasses import dataclass
import httpx

logger = logging.getLogger("a2a")


@dataclass
class HermesGatewayConfig:
    """Hermes Gateway 配置"""
    url: str = "http://127.0.0.1:8000"  # Hermes 默认端口
    token: str = ""
    timeout: float = 120.0


class HermesGatewayBridge:
    """
    Hermes Gateway 桥接器
    
    提供与 OpenClaw GatewayBridge 相同的接口，
    但调用 Hermes Agent 的 API。
    """
    
    def __init__(self, config: HermesGatewayConfig = None):
        """
        初始化 Hermes Gateway Bridge
        
        Args:
            config: Hermes Gateway 配置，默认从环境变量读取
        """
        if config is None:
            config = HermesGatewayConfig(
                url=os.getenv("HERMES_GATEWAY_URL", "http://127.0.0.1:8000"),
                token=os.getenv("HERMES_GATEWAY_TOKEN", ""),
                timeout=float(os.getenv("HERMES_GATEWAY_TIMEOUT", "120")),
            )
        
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None
    
    @property
    def client(self) -> httpx.AsyncClient:
        """获取 HTTP 客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.config.timeout)
        return self._client
    
    async def close(self):
        """关闭客户端"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    async def health_check(self) -> bool:
        """
        健康检查
        
        Returns:
            bool: Gateway 是否可用
        """
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.config.url}/health")
                return response.status_code == 200
        except Exception as e:
            logger.warning(f"Hermes Gateway 健康检查失败: {e}")
            return False
    
    async def execute(
        self,
        content: str,
        session_key: str,
        context: Dict[str, Any] = None,
        **kwargs
    ) -> str:
        """
        同步执行任务
        
        Args:
            content: 消息内容
            session_key: 会话标识
            context: 额外上下文
            
        Returns:
            str: 执行结果
        """
        headers = self._build_headers(session_key)
        
        messages = self._build_messages(content, context)
        
        payload = {
            "model": "auto",
            "messages": messages,
            "stream": False,
        }
        
        try:
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
                    raise Exception(f"Hermes Gateway error: {response.status_code} - {response.text}")
        
        except Exception as e:
            logger.error(f"Hermes Gateway 执行失败: {e}")
            raise
    
    async def execute_stream(
        self,
        content: str,
        session_key: str,
        context: Dict[str, Any] = None,
        cancel_event=None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        流式执行任务
        
        Args:
            content: 消息内容
            session_key: 会话标识
            context: 额外上下文
            cancel_event: 取消事件
            
        Yields:
            str: 内容片段
        """
        headers = self._build_headers(session_key)
        
        messages = self._build_messages(content, context)
        
        payload = {
            "model": "auto",
            "messages": messages,
            "stream": True,
        }
        
        try:
            async with self.client.stream(
                "POST",
                f"{self.config.url}/v1/chat/completions",
                json=payload,
                headers=headers
            ) as response:
                async for line in response.aiter_lines():
                    if cancel_event and cancel_event.is_set():
                        break
                    
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            content_chunk = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            if content_chunk:
                                yield content_chunk
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.error(f"Hermes Gateway 流式执行失败: {e}")
            raise
    
    def _build_headers(self, session_key: str) -> Dict[str, str]:
        """构建请求头"""
        headers = {
            "Content-Type": "application/json",
        }
        
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        
        if session_key:
            headers["X-Session-Key"] = session_key
        
        return headers
    
    def _build_messages(self, content: str, context: Dict[str, Any] = None) -> list:
        """
        构建消息列表
        
        Args:
            content: 用户输入
            context: 上下文（包含历史等）
            
        Returns:
            list: 消息列表
        """
        messages = []
        
        # 添加上下文（如果有）
        if context:
            name = context.get("name", "")
            history = context.get("history", [])
            
            # 添加系统提示
            system_content = "你是一个专业的 AI 助手。"
            if name:
                system_content = f"你的名字是 {name}，你是一个专业的 AI 助手。"
            
            messages.append({
                "role": "system",
                "content": system_content
            })
            
            # 添加历史消息
            for msg in history[-10:]:  # 限制最近 10 条
                if isinstance(msg, dict):
                    messages.append({
                        "role": msg.get("role", "user"),
                        "content": msg.get("content", "")
                    })
        
        # 添加用户消息
        messages.append({
            "role": "user",
            "content": content
        })
        
        return messages
