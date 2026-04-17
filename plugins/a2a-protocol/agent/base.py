"""
Base Agent
A2A Agent 组件 - 基础 Agent 类
"""
import asyncio
import json
import logging
from abc import ABC, abstractmethod
from typing import Optional, Any
from dataclasses import dataclass

logger = logging.getLogger("a2a")


@dataclass
class AgentResponse:
    """Agent 响应"""
    content: str = ""
    artifacts: list = None
    metadata: dict = None
    
    def __post_init__(self):
        if self.artifacts is None:
            self.artifacts = []
        if self.metadata is None:
            self.metadata = {}


class BaseAgent(ABC):
    """基础 Agent 类"""
    
    def __init__(
        self,
        name: str,
        description: str,
        version: str = "1.0.0",
        capabilities: dict = None
    ):
        self.name = name
        self.description = description
        self.version = version
        self.capabilities = capabilities or {
            "streaming": True,
            "pushNotifications": True,
            "stateTransitions": True,
            "sessions": True
        }
        self._running = False
    
    @abstractmethod
    async def process(self, message: dict, context: dict = None) -> AgentResponse:
        """
        处理消息
        
        Args:
            message: 消息内容 {"role": "user", "content": "..."}
            context: 上下文信息
            
        Returns:
            AgentResponse: 响应内容
        """
        pass
    
    async def on_task_start(self, task_id: str, message: dict) -> None:
        """任务开始回调"""
        logger.info(f"[{self.name}] Task {task_id} started")
    
    async def on_task_progress(self, task_id: str, progress: float, message: str = "") -> None:
        """任务进度回调"""
        logger.debug(f"[{self.name}] Task {task_id} progress: {progress*100}%")
    
    async def on_task_complete(self, task_id: str, response: AgentResponse) -> None:
        """任务完成回调"""
        logger.info(f"[{self.name}] Task {task_id} completed")
    
    async def on_task_error(self, task_id: str, error: Exception) -> None:
        """任务错误回调"""
        logger.error(f"[{self.name}] Task {task_id} error: {error}")
    
    async def start(self) -> None:
        """启动 Agent"""
        self._running = True
        logger.info(f"Agent {self.name} started")
    
    async def stop(self) -> None:
        """停止 Agent"""
        self._running = False
        logger.info(f"Agent {self.name} stopped")
    
    @property
    def is_running(self) -> bool:
        return self._running


class WritingAgent(BaseAgent):
    """写作 Agent 示例"""
    
    def __init__(self):
        super().__init__(
            name="writer",
            description="AI写作助手",
            version="1.0.0"
        )
    
    async def process(self, message: dict, context: dict = None) -> AgentResponse:
        """处理写作任务"""
        content = message.get("content", "")
        
        # 模拟处理
        await asyncio.sleep(1)
        
        # 简单处理
        result = f"已处理写作任务: {content[:50]}..."
        
        return AgentResponse(
            content=result,
            artifacts=[{"type": "text", "content": result}],
            metadata={"agent": self.name}
        )


# 示例使用
if __name__ == "__main__":
    async def main():
        agent = WritingAgent()
        await agent.start()
        
        response = await agent.process({
            "role": "user",
            "content": "写一个小说开头"
        })
        
        print(f"Result: {response.content}")
        
        await agent.stop()
    
    asyncio.run(main())
