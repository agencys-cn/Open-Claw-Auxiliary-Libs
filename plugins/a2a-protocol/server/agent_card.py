"""
Agent Card - Agent 的能力描述/注册表
A2A 协议核心组件

概述:
    Agent Card 是 Agent 的"名片"，描述 Agent 的能力、端点、支持的技能等。
    用于 Agent 发现机制，让其他 Agent 或客户端能够了解如何与该 Agent 交互。

规范:
    - 参考 Google A2A (Agent-to-Agent) 协议规范
    - Agent Card 托管在 /.well-known/agent.json

数据库缓存:
    - Agent Card 会缓存到本地 SQLite 数据库
    - 缓存过期时间默认 1 小时
    - 支持远程 Agent Card 拉取和缓存

示例:
    >>> card = AgentCard(
    ...     name="writer",
    ...     description="AI写作助手",
    ...     url="http://localhost:13666"
    ... )
    >>> registry.register(card)

    >>> card = registry.get("writer")
    >>> print(card.to_json())
    
    >>> # 远程拉取
    >>> remote = await registry.fetch("http://remote:13666/.well-known/agent.json")
"""

import asyncio
import json
import logging
from dataclasses import dataclass, asdict, field
from typing import Optional, List, Dict, Any
from datetime import datetime

import httpx

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
    
    @property
    def card_url(self) -> str:
        """获取 Agent Card URL"""
        return f"{self.url}/.well-known/agent.json"
    
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
    
    async def fetch_from_url(self, timeout: float = 5.0) -> "AgentCard":
        """
        从 URL 拉取 Agent Card
        
        Args:
            timeout: 请求超时时间（秒）
        
        Returns:
            拉取的 AgentCard 实例
        
        Raises:
            Exception: 拉取失败时抛出
        """
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(self.card_url)
            response.raise_for_status()
            data = response.json()
            return AgentCard.from_dict(data)


class AgentCardRegistry:
    """
    Agent Card 注册表
    
    负责管理所有已注册的 Agent Card，支持注册、注销、查询等操作。
    
    Features:
        - 内存注册表：本地注册的 Agent
        - 数据库缓存：远程 Agent Card 缓存
        - 远程拉取：支持从远程 URL 拉取 Agent Card
    
    Attributes:
        _cards: 存储已注册 Agent Card 的字典，key 为 Agent 名称
        _db: 数据库实例（可选）
        _cache_ttl: 缓存过期时间（秒）
    
    Example:
        >>> registry = AgentCardRegistry()
        >>> registry.register(my_card)
        >>> card = registry.get("writer")
    """
    
    def __init__(self, db=None, cache_ttl: int = 3600):
        """
        初始化注册表
        
        Args:
            db: 数据库实例（可选）
            cache_ttl: 缓存过期时间（秒），默认 1 小时
        """
        self._cards: Dict[str, AgentCard] = {}
        self._db = db
        self._cache_ttl = cache_ttl
    
    def set_database(self, db):
        """设置数据库实例"""
        self._db = db
    
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
        
        # 缓存到数据库
        if self._db:
            self._db.save_agent_card(card.name, card.to_dict(), self._cache_ttl)
        
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
            
            # 从数据库删除缓存
            if self._db:
                self._db.delete_agent_card(name)
            
            logger.info(f"Agent 注销: {name}")
    
    def get(self, name: str) -> Optional[AgentCard]:
        """
        获取 Agent Card
        
        优先从内存注册表获取，如果不存在且配置了数据库，
        则尝试从数据库缓存获取。
        
        Args:
            name: Agent 名称
        
        Returns:
            如果找到返回 AgentCard 实例，否则返回 None
        
        Example:
            >>> card = registry.get("writer")
            >>> if card:
            ...     print(card.description)
        """
        # 优先从内存获取
        if name in self._cards:
            return self._cards[name]
        
        # 尝试从数据库缓存获取
        if self._db:
            cached = self._db.get_agent_card(name)
            if cached:
                card = AgentCard.from_dict(cached)
                # 放回内存注册表
                self._cards[name] = card
                logger.debug(f"Agent Card 从缓存获取: {name}")
                return card
        
        return None
    
    async def fetch(self, url: str) -> Optional[AgentCard]:
        """
        从远程 URL 拉取 Agent Card
        
        拉取后会缓存到数据库。
        
        Args:
            url: Agent Card 的 URL
        
        Returns:
            拉取的 AgentCard 实例，失败返回 None
        """
        try:
            card = AgentCard(name="", description="", url="")
            remote_card = await card.fetch_from_url(url)
            
            # 缓存到数据库
            if self._db:
                self._db.save_agent_card(remote_card.name, remote_card.to_dict(), self._cache_ttl)
            
            # 注册到本地
            self._cards[remote_card.name] = remote_card
            
            logger.info(f"远程 Agent 已注册: {remote_card.name} @ {remote_card.url}")
            return remote_card
        except Exception as e:
            logger.error(f"拉取远程 Agent Card 失败: {url}, {e}")
            return None
    
    async def fetch_and_cache(self, name: str, url: str) -> Optional[AgentCard]:
        """
        拉取远程 Agent Card 并缓存
        
        如果本地已存在则跳过拉取。
        
        Args:
            name: Agent 名称
            url: Agent 服务地址（不是 card URL）
        
        Returns:
            AgentCard 实例
        """
        # 检查本地是否存在
        if self.get(name):
            return self.get(name)
        
        # 构造 card URL 并拉取
        card_url = f"{url}/.well-known/agent.json"
        return await self.fetch(card_url)
    
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
    
    def list_cached(self) -> List[str]:
        """
        列出数据库中缓存的 Agent 名称
        
        Returns:
            名称列表
        """
        if not self._db:
            return []
        
        sessions = self._db.list_sessions(include_expired=True)
        # 这里需要查询 agent_cards 表，但目前 db.py 没有直接方法
        # 暂时返回空列表
        return []
    
    def clear(self) -> None:
        """
        清空注册表（不清除数据库缓存）
        
        Example:
            >>> registry.clear()
        """
        self._cards.clear()
        logger.info("Agent 注册表已清空（缓存未清除）")
    
    def clear_cache(self) -> None:
        """
        清除数据库缓存
        """
        if self._db:
            self._db.cleanup_expired_cards()
            logger.info("Agent Card 缓存已清除")
    
    def __len__(self) -> int:
        """返回已注册 Agent 数量"""
        return len(self._cards)
    
    def __contains__(self, name: str) -> bool:
        """检查 Agent 是否已注册"""
        return name in self._cards


# 全局注册表实例
# 使用模块级单例，确保全局只有一个注册表
registry = AgentCardRegistry()


def get_registry() -> AgentCardRegistry:
    """获取全局注册表实例"""
    return registry


def init_registry(db=None) -> AgentCardRegistry:
    """
    初始化全局注册表
    
    Args:
        db: 数据库实例
    
    Returns:
        注册表实例
    """
    if db:
        registry.set_database(db)
    return registry
