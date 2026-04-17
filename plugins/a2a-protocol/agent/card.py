"""
Agent Card
A2A Agent 组件
"""
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime

logger = logging.getLogger("a2a")


@dataclass
class Skill:
    """Agent 技能"""
    id: str
    name: str
    description: str = ""
    tags: list = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Capabilities:
    """Agent 能力"""
    streaming: bool = True
    push_notifications: bool = True
    state_transitions: bool = True
    sessions: bool = True
    
    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentCard:
    """Agent Card"""
    name: str
    description: str
    url: str
    version: str = "1.0"
    capabilities: Capabilities = field(default_factory=Capabilities)
    skills: list = field(default_factory=list)
    owner: str = ""
    created_at: str = ""
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
    
    def to_dict(self) -> dict:
        data = asdict(self)
        data["capabilities"] = self.capabilities.to_dict()
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> "AgentCard":
        if "capabilities" in data:
            data["capabilities"] = Capabilities(**data["capabilities"])
        return cls(**data)
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    @classmethod
    def from_url(cls, url: str) -> Optional["AgentCard"]:
        """从 URL 获取 Agent Card"""
        import httpx
        try:
            response = httpx.get(f"{url}/.well-known/agent.json", timeout=10)
            if response.status_code == 200:
                return cls.from_dict(response.json())
        except Exception as e:
            logger.error(f"获取 Agent Card 失败: {e}")
        return None
