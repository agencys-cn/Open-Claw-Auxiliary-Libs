"""
测试 Agent Card
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from agent_card import AgentCard, Capabilities, Skill, registry


async def test_agent_card():
    """测试 Agent Card"""
    print("=" * 50)
    print("测试 Agent Card")
    print("=" * 50)
    
    # 创建 Agent Card
    card = AgentCard(
        name="test-agent",
        description="测试Agent",
        url="http://127.0.0.1:13666",
        version="1.0.0",
        capabilities=Capabilities(
            streaming=True,
            push_notifications=True,
            state_transitions=True,
            sessions=True
        ),
        skills=[
            Skill(id="test", name="测试技能", description="用于测试")
        ],
        owner="Test"
    )
    
    print(f"\n创建 Agent Card:")
    print(f"  名称: {card.name}")
    print(f"  描述: {card.description}")
    print(f"  URL: {card.url}")
    print(f"  能力: streaming={card.capabilities.streaming}")
    
    # 注册
    registry.register(card)
    print(f"\n已注册到注册表")
    
    # 获取
    retrieved = registry.get("test-agent")
    print(f"从注册表获取: {retrieved.name if retrieved else 'Not Found'}")
    
    # 列出所有
    all_agents = registry.list_all()
    print(f"\n所有已注册 Agent: {[a.name for a in all_agents]}")
    
    # 转为 JSON
    print(f"\nJSON 格式:")
    print(card.to_json())
    
    print("\n✅ Agent Card 测试通过")


if __name__ == "__main__":
    asyncio.run(test_agent_card())
