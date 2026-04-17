# A2A Protocol - 标准实现

基于当前简化版 A2A Server 的完整标准实现，桥接 OpenClaw Gateway。

## 目录结构

```
a2a-protocol/
├── server/
│   ├── __init__.py          # 模块导出
│   ├── main.py              # 主服务器（端口 13666）
│   ├── agent_card.py        # Agent Card 管理
│   ├── jsonrpc.py           # JSON-RPC 2.0 处理
│   ├── task_manager.py      # 任务状态机
│   ├── session.py           # 会话管理
│   ├── sse.py              # SSE 流式推送
│   ├── websocket.py         # WebSocket 处理
│   └── gateway.py           # Gateway 桥接器 ⭐
├── agent/
│   ├── __init__.py
│   ├── base.py              # 基础 Agent 类
│   ├── card.py              # Agent Card
│   └── client.py            # A2A 客户端
├── utils/
│   └── config.py            # 配置
├── test_agent_card.py       # 测试脚本
├── test_task_manager.py     # 测试脚本
├── test_client.py           # 测试脚本
├── pyproject.toml
└── README.md
```

## 核心组件

### 1. Gateway 桥接器 (`gateway.py`) ⭐

独立模块，桥接 A2A 和 OpenClaw Gateway：

```python
from gateway import gateway_bridge, gateway_health, gateway_config

# 同步执行
result = await gateway_bridge.execute(
    content="任务内容",
    session_key="a2a:client123",
    context={"name": "钱多多"}
)

# 流式执行
async for chunk in gateway_bridge.execute_stream(content, session_key, context):
    print(chunk, end="")
```

### 2. Agent Card

Agent 的能力描述/注册表：

```json
{
  "name": "writer",
  "description": "AI写作助手",
  "url": "http://localhost:13666",
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": true,
    "stateTransitions": true,
    "sessions": true
  },
  "skills": [
    {"id": "novel", "name": "小说写作"}
  ]
}
```

### 3. JSON-RPC 2.0

标准 JSON-RPC 2.0 消息格式：

```json
// 请求
{
  "jsonrpc": "2.0",
  "id": "msg_xxx",
  "method": "tasks/send",
  "params": {
    "sessionId": "session_xxx",
    "message": {"role": "user", "content": "任务内容"},
    "streaming": true
  }
}

// 响应
{
  "jsonrpc": "2.0",
  "id": "msg_xxx",
  "result": {
    "taskId": "task_xxx",
    "status": {"state": "in_progress"}
  }
}
```

### 4. 任务状态机

```
submitted → in_progress → completed
                              ↓
                          failed
                              ↓
                          canceled
                ↓
          input_required（需要更多信息）
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/.well-known/agent.json` | GET | 获取 Agent Card |
| `/agent/register` | POST | 注册 Agent |
| `/agents` | GET | 列出所有 Agent |
| `/rpc` | POST | JSON-RPC 接口 |
| `/sse/{client_id}` | GET | SSE 流式订阅 |
| `/ws/{client_id}` | WS | WebSocket |
| `/tasks/{task_id}` | GET | 获取任务 |
| `/tasks` | GET | 列出任务 |
| `/tasks/{task_id}` | DELETE | 删除任务 |
| `/sessions/{session_id}` | GET | 获取会话 |
| `/sessions` | GET | 列出会话 |
| `/gateway/status` | GET | Gateway 状态 |
| `/gateway/health-check` | POST | 健康检查 |
| `/health` | GET | 服务健康检查 |

## JSON-RPC 方法

| 方法 | 说明 |
|------|------|
| `tasks/send` | 发送任务 |
| `tasks/get` | 获取任务状态 |
| `tasks/cancel` | 取消任务 |
| `tasks/sendSubscribe` | 发送任务（订阅模式） |
| `tasks/history` | 获取任务历史 |
| `sessions/create` | 创建会话 |
| `sessions/get` | 获取会话 |
| `sessions/history` | 获取会话历史 |
| `agents/list` | 列出所有 Agent |
| `agents/get` | 获取 Agent |
| `gateway/execute` | 直接执行（不创建任务） |

## 启动

```bash
cd /root/.openclaw/workspace/scripts/python/a2a-protocol/server
uv run python main.py
```

## 客户端示例

```python
from agent.client import A2AClient

async def main():
    # 创建客户端
    client = A2AClient("http://127.0.0.1:13666", client_id="my-client")
    
    # 获取 Agent Card
    card = await client.get_agent_card()
    print(f"Agent: {card['name']}")
    
    # 发送任务并等待结果
    result = await client.send_and_wait("写一个小说开头", name="钱多多")
    print(f"结果: {result.first_text}")
    
    # 或者分步操作
    task_id = await client.send_task("写一个小说开头", name="钱多多")
    result = await client.wait_for_task(task_id)
    print(f"结果: {result.first_text}")
    
    await client.close()

asyncio.run(main())
```

## 事件订阅

```python
from agent.client import A2AClient

async def main():
    client = A2AClient("http://127.0.0.1:13666")
    
    # 注册事件处理器
    def on_completed(data):
        print(f"任务完成: {data}")
    
    client.on("task/completed", on_completed)
    
    # 发送任务
    await client.send_task("写一个小说开头")
    
    # 保持运行
    await asyncio.sleep(60)

asyncio.run(main())
```

## 测试

```bash
# 测试 Agent Card
uv run python test_agent_card.py

# 测试任务状态机
uv run python test_task_manager.py

# 测试客户端（需要服务器运行）
uv run python test_client.py
```

## 与简化版对比

| 特性 | 简化版 | 标准版 |
|------|--------|--------|
| 端口 | 13666 | 13666 |
| Agent Card | ❌ | ✅ |
| JSON-RPC 2.0 | ❌ | ✅ |
| 任务状态机 | 简化 | 完整 |
| SSE | ✅ | ✅ |
| WebSocket | ✅ | ✅ |
| Gateway 桥接 | 内嵌 | 独立模块 |
| 会话管理 | 基础 | 完整 |
