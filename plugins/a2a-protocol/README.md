# A2A Protocol - 标准实现

基于 A2A (Agent-to-Agent) 协议规范的标准实现，桥接 OpenClaw Gateway。

## 特性

| 特性 | 说明 |
|------|------|
| **JSON-RPC 2.0** | 标准 RPC 协议 |
| **SSE** | 服务器推送，流式输出 |
| **WebSocket** | 双向通信 |
| **ULID 会话队列** | 任务排队和自动调度 |
| **Gateway 桥接** | 与 OpenClaw Gateway 集成 |
| **任务取消传播** | 取消 A2A 任务时同时取消 Gateway 调用 |
| **超时控制** | 任务超时自动取消（默认 5 分钟） |
| **重试机制** | Gateway 调用失败自动重试（指数退避） |
| **认证授权** | API Key / Token 认证 |
| **限流保护** | 防止恶意请求 |
| **心跳保活** | SSE/WebSocket 长连接保活 |
| **Prometheus 监控** | 指标采集 |
| **CLI 工具** | 命令行测试客户端 |

## 目录结构

```
a2a-protocol/
├── server/
│   ├── __init__.py          # 模块导出
│   ├── main.py              # 主服务器（端口 13666）
│   ├── agent_card.py        # Agent Card 管理
│   ├── jsonrpc.py           # JSON-RPC 2.0 处理
│   ├── task_manager.py      # 任务状态机（含超时/取消）
│   ├── session.py           # 会话管理
│   ├── session_store.py     # ULID 会话队列
│   ├── sse.py              # SSE 流式推送
│   ├── websocket.py         # WebSocket 处理
│   ├── gateway.py           # Gateway 桥接器（含取消传播）
│   ├── retry.py             # 重试机制
│   ├── ratelimit.py         # 限流保护
│   ├── auth.py              # 认证授权
│   ├── heartbeat.py         # 心跳保活
│   └── metrics.py           # Prometheus 监控
├── agent/
│   ├── __init__.py
│   ├── base.py              # 基础 Agent 类
│   ├── card.py              # Agent Card
│   └── client.py            # A2A 客户端
├── utils/
│   └── config.py            # 配置
├── cli.py                   # CLI 工具
├── test_*.py               # 测试脚本
├── pyproject.toml
├── .env                     # 配置文件
└── README.md
```

## 快速开始

### 1. 安装

```bash
cd plugins/a2a-protocol
uv sync
```

### 2. 配置

复制 `.env.example` 为 `.env` 并修改：

```env
# 服务端口
A2A_SERVER_PORT=13666

# Gateway 配置
GATEWAY_URL=http://127.0.0.1:18789
TASK_TIMEOUT=300           # 任务超时（秒）

# ULID 会话队列
ULID_ENABLED=true

# 认证（可选）
AUTH_ENABLED=false
# API_KEYS=key1,key2

# 限流（可选）
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_MINUTE=60
```

### 3. 启动

```bash
uv run python server/main.py
```

### 4. 测试

```bash
# 发送任务
uv run python cli.py send "写一个小说开头"

# 流式模式
uv run python cli.py send "写一个小说开头" --stream

# 查询任务
uv run python cli.py status <task_id>

# 获取 Agent Card
uv run python cli.py card
```

## 核心组件

### 1. Gateway 桥接器 (`gateway.py`)

桥接 A2A 和 OpenClaw Gateway，支持：
- 消息转发
- 流式输出
- **任务取消传播** - 取消 A2A 任务时同时取消 Gateway 调用
- **超时控制** - 任务超时自动取消

```python
from server.gateway import gateway_bridge

# 同步执行
result = await gateway_bridge.execute(
    content="任务内容",
    session_key="a2a:client123",
    context={"name": "钱多多"}
)

# 流式执行
async for chunk in gateway_bridge.execute_stream(content, session_key):
    print(chunk, end="")

# 取消任务
gateway_bridge.cancel_task("a2a:client123")
```

### 2. 重试机制 (`retry.py`)

Gateway 调用失败时自动重试，支持指数退避：

```python
from server.retry import RetryPolicy, retry_call

# 使用重试策略
policy = RetryPolicy(max_retries=3, base_delay=1.0, max_delay=30.0)
result = await policy.execute(gateway_bridge.execute, content, session_key)

# 或使用快捷函数
result = await retry_call(
    gateway_bridge.execute,
    content="hello",
    session_key="test",
    max_retries=3
)
```

### 3. 限流保护 (`ratelimit.py`)

防止恶意请求，保护 Gateway 资源：

```python
from server.ratelimit import rate_limiter, RateLimitExceeded

# 检查请求
allowed, remaining = await rate_limiter.check(
    ip="192.168.1.1",
    client_id="client_123"
)

if not allowed:
    raise RateLimitExceeded("请求过于频繁", retry_after=60)
```

### 4. 认证授权 (`auth.py`)

API Key / Token 认证：

```python
from server.auth import auth_manager, AuthLevel

# 创建 API Key
key = auth_manager.create_api_key("my_client", level=AuthLevel.WRITE)
print(f"新 Key: {key}")  # 只在创建时返回一次

# 验证请求
result = await auth_manager.authenticate(
    api_key="sk_xxx",
    ip="192.168.1.1"
)

if not result.is_valid:
    raise AuthenticationError(result.error)
```

### 5. 心跳保活 (`heartbeat.py`)

SSE/WebSocket 长连接保活：

```python
from server.heartbeat import heartbeat_manager

# SSE 心跳
async for line in heartbeat_manager.sse_heartbeat(event_stream, client_id):
    yield line

# WebSocket 心跳
heartbeat_manager.start_ws_heartbeat(ws_client, client_id)
```

### 6. Prometheus 监控 (`metrics.py`)

指标采集：

```python
from server.metrics import metrics, metrics_middleware

# 记录请求
await metrics.record_request("/rpc", 200, 0.05)

# 获取指标（Prometheus 格式）
metrics_text = await metrics.get_metrics()
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
| `/gateway/status` | GET | Gateway 状态 |
| `/gateway/health-check` | POST | 健康检查 |
| `/metrics` | GET | Prometheus 指标 |
| `/health` | GET | 服务健康检查 |

## JSON-RPC 方法

| 方法 | 说明 |
|------|------|
| `tasks/send` | 发送任务（支持 ULID 队列） |
| `tasks/get` | 获取任务状态 |
| `tasks/cancel` | 取消任务（传播到 Gateway） |
| `tasks/sendSubscribe` | 发送任务（订阅模式） |
| `tasks/history` | 获取任务历史 |
| `sessions/create` | 创建会话 |
| `sessions/get` | 获取会话 |
| `sessions/history` | 获取会话历史 |
| `agents/list` | 列出所有 Agent |
| `agents/get` | 获取 Agent |
| `gateway/execute` | 直接执行 |

## 任务状态机

```
submitted → in_progress → completed
                              ↓
                          failed
                              ↓
                          canceled
                                    ↓
                          input_required
```

## ULID 会话队列

首次请求自动生成 ULID，同一会话的任务排队执行：

```python
# 发送任务（自动生成 ULID）
result = await client.send_task("第一个任务")
ulid = result["ulid"]

# 后续任务使用相同 ULID（自动排队）
result = await client.send_task("第二个任务", ulid=ulid)
```

## 超时和取消

```python
# 任务超时（默认 5 分钟）
task = Task(session_id="test", timeout_seconds=300)

# 取消任务（传播到 Gateway）
task.cancel()
```

## 配置说明

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `A2A_SERVER_PORT` | 服务端口 | 13666 |
| `GATEWAY_URL` | Gateway 地址 | http://127.0.0.1:18789 |
| `TASK_TIMEOUT` | 任务超时（秒） | 300 |
| `ULID_ENABLED` | 启用 ULID 队列 | true |
| `AUTH_ENABLED` | 启用认证 | false |
| `RATE_LIMIT_ENABLED` | 启用限流 | true |
| `RATE_LIMIT_PER_MINUTE` | 每分钟限制 | 60 |

## 许可证

MIT License
