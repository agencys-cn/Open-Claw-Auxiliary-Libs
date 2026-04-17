"""
测试 A2A Client
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from agent.client import A2AClient, TaskStatus


async def test_client():
    """测试 A2A 客户端"""
    print("=" * 50)
    print("测试 A2A Client")
    print("=" * 50)
    
    # 创建客户端
    server_url = "http://127.0.0.1:13666"
    client = A2AClient(server_url, client_id="test-client", auto_subscribe=False)
    
    print(f"\n创建客户端:")
    print(f"  Server: {client.server_url}")
    print(f"  Client ID: {client.client_id}")
    
    # 测试 Agent Card
    print(f"\n获取 Agent Card...")
    try:
        card = await client.get_agent_card()
        print(f"  名称: {card.get('name')}")
        print(f"  描述: {card.get('description')}")
        print(f"  能力: {card.get('capabilities')}")
    except Exception as e:
        print(f"  ⚠️ 获取失败（服务器可能未启动）: {e}")
    
    # 测试 Gateway 执行
    print(f"\n测试 Gateway 执行...")
    try:
        result = await client.gateway_execute("你好，测试一下", name="测试")
        print(f"  结果: {result[:100]}..." if len(result) > 100 else f"  结果: {result}")
    except Exception as e:
        print(f"  ⚠️ 执行失败（服务器可能未启动）: {e}")
    
    print("\n✅ A2A Client 测试完成")


if __name__ == "__main__":
    asyncio.run(test_client())
