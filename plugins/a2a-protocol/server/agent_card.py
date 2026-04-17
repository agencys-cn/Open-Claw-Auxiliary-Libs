"""
Agent Card - Agent 的能力描述/注册表
A2A 协议核心组件

概述:
    Agent Card 是 Agent 的"名片"，描述 Agent 的能力、端点、支持的技能等。
    用于 Agent 发现机制，让其他 Agent 或客户端能够了解如何与该 Agent 交互。

规范:
    - 参考 Google A2A (Agent-to-Agent) 协议规范
    - Agent Card 托管在 /.well-known/agent.json

示例:
    >>> card = AgentCard(
    ...     name="writer",
    ...     description="AI写作助手",
    ...     url="http://localhost:13666"
    ... )
    >>> registry.register(card)

    >>> card = registry.get("writer")
    >>> print(card.to_json())
"""

import json
import logging
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any
from datetime import datetime

logger = logging.getLogger("a2a")


@dataclass
class Skill:
    """
    Agent 技能定义
    
    Attributes:
        id: 技能唯一标识符，如 "novel", "copywriting"
        name: 技能显示名称
        description: 技能详细描述
        tags: 技能标签列表，用于分类检索
    """
    id: str
    name: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Skill":
        """从字典创建技能实例"""
        return cls(**data)


@dataclass
class Capabilities:
    """
    Agent 能力描述
    
    Attributes:
        streaming: 是否支持流式推送（Server-Sent Events）
        push_notifications: 是否支持推送通知
        state_transitions: 是否支持任务状态转换
        sessions: 是否支持会话管理
    """
    streaming: bool = True
    push_notifications: bool = True
    state_transitions: bool = True
    sessions: bool = True
    
    def to_dict(self) -> Dict[str, bool]:
        """转换为字典格式"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Capabilities":
        """从字典创建能力实例"""
        return cls(**data)


@dataclass
class AgentCard:
    """
    Agent Card - Agent 的名片
    
    Agent Card 描述了一个 Agent 的全部信息，包括：
    - 基本信息（名称、描述、版本）
    - 端点地址
    - 支持的能力
    - 技能列表
    
    Attributes:
        name: Agent 名称，唯一标识符
        description: Agent 功能描述
        url: Agent 服务地址，如 "http://localhost:13666"
        version: Agent 版本号，遵循语义化版本
        capabilities: Agent 支持的能力
        skills: Agent 提供的技能列表
        owner: Agent 所有者/创建者
        created_at: 创建时间（ISO 8601 格式）
    
    Example:
        >>> card = AgentCard(
        ...     name="writer",
        ...     description="AI写作助手",
        ...     url="http://localhost:13666",
        ...     capabilities=Capabilities(streaming=True),
        ...     skills=[Skill(id="novel", name="小说写作")]
        ... )
        >>> print(card.to_json())
    """
    name: str
    description: str
    url: str
    version: str = "1.0"
    capabilities: Capabilities = field(default_factory=Capabilities)
    skills: List[Skill] = field(default_factory=list)
    owner: str = ""
    created_at: str = ""
    
    def __post_init__(self):
        """初始化后处理，自动设置创建时间"""
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典
        
        Returns:
            包含所有字段的字典，capabilities 会被递归转换
        
        Example:
            >>> card.to_dict()
            {'name': 'writer', 'description': '...', 'capabilities': {...}, ...}
        """
        data = asdict(self)
        data["capabilities"] = self.capabilities.to_dict()
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentCard":
        """
        从字典创建 AgentCard 实例
        
        Args:
            data: 包含 AgentCard 数据的字典
        
        Returns:
            AgentCard 实例
        
        Example:
            >>> data = {'name': 'writer', 'capabilities': {'streaming': True}}
            >>> card = AgentCard.from_dict(data)
        """
        if "capabilities" in data and isinstance(data["capabilities"], dict):
            data["capabilities"] = Capabilities.from_dict(data["capabilities"])
        if "skills" in data:
            data["skills"] = [Skill.from_dict(s) if isinstance(s, dict) else s for s in data["skills"]]
        return cls(**data)
    
    def to_json(self) -> str:
        """
        转换为 JSON 字符串
        
        Returns:
            格式化的 JSON 字符串
        
        Example:
            >>> card.to_json()
            '{\n  "name": "writer",\n  ...\n}'
        """
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> "AgentCard":
        """
        从 JSON 字符串创建 AgentCard 实例
        
        Args:
            json_str: JSON 格式字符串
        
        Returns:
            AgentCard 实例
        """
        return cls.from_dict(json.loads(json_str))


class AgentCardRegistry:
    """
    Agent Card 注册表
    
    负责管理所有已注册的 Agent Card，支持注册、注销、查询等操作。
    注册表是单例模式，全局只有一个实例。
    
    Attributes:
        _cards: 存储已注册 Agent Card 的字典，key 为 Agent 名称
    
    Example:
        >>> registry = AgentCardRegistry()
        >>> registry.register(my_card)
        >>> card = registry.get("writer")
    """
    
    def __init__(self):
        """初始化注册表"""
        self._cards: Dict[str, AgentCard] = {}
    
    def register(self, card: AgentCard) -> None:
        """
        注册 Agent Card
        
        Args:
            card: 要注册的 AgentCard 实例
        
        Raises:
            ValueError: 如果 card 参数不是 AgentCard 类型
        
        Example:
            >>> card = AgentCard(name="writer", description="...", url="...")
            >>> registry.register(card)
        """
        if not isinstance(card, AgentCard):
            raise ValueError("card must be an AgentCard instance")
        self._cards[card.name] = card
        logger.info(f"Agent 注册: {card.name} @ {card.url}")
    
    def unregister(self, name: str) -> None:
        """
        注销 Agent
        
        Args:
            name: 要注销的 Agent 名称
        
        Example:
            >>> registry.unregister("writer")
        """
        if name in self._cards:
            del self._cards[name]
            logger.info(f"Agent 注销: {name}")
    
    def get(self, name: str) -> Optional[AgentCard]:
        """
        获取 Agent Card
        
        Args:
            name: Agent 名称
        
        Returns:
            如果找到返回 AgentCard 实例，否则返回 None
        
        Example:
            >>> card = registry.get("writer")
            >>> if card:
            ...     print(card.description)
        """
        return self._cards.get(name)
    
    def list_all(self) -> List[AgentCard]:
        """
        列出所有已注册的 Agent Card
        
        Returns:
            AgentCard 实例列表
        
        Example:
            >>> all_agents = registry.list_all()
            >>> for agent in all_agents:
            ...     print(agent.name)
        """
        return list(self._cards.values())
    
    def clear(self) -> None:
        """
        清空注册表
        
        Example:
            >>> registry.clear()
        """
        self._cards.clear()
        logger.info("Agent 注册表已清空")
    
    def __len__(self) -> int:
        """返回已注册 Agent 数量"""
        return len(self._cards)
    
    def __contains__(self, name: str) -> bool:
        """检查 Agent 是否已注册"""
        return name in self._cards


# 全局注册表实例
# 使用模块级单例，确保全局只有一个注册表
registry = AgentCardRegistry()
