# Open-Claw-Auxiliary-Libs

OpenClaw 辅助库和插件集合。

## 目录结构

```
├── plugins/              # 代码项目（以项目区分）
│   └── a2a-protocol/  # A2A 协议标准实现
│       ├── server/      # 服务端
│       ├── agent/      # Agent 客户端
│       └── pyproject.toml
├── skills/             # 技能文件（以技能名区分）
├── prompt/             # 学习指令文件
└── other/              # 暂时未归类的内容
```

## plugins/a2a-protocol

A2A (Agent-to-Agent) Protocol 标准实现，支持：

- **JSON-RPC 2.0** - 标准 RPC 协议
- **SSE/Server-Sent Events** - 服务器推送
- **WebSocket** - 双向通信
- **ULID 会话队列** - 任务排队和自动调度
- **Gateway 桥接** - 与 OpenClaw Gateway 集成

### 快速开始

```bash
cd plugins/a2a-protocol
uv sync                    # 安装依赖
uv run python server/main.py  # 启动服务器
```

### 配置

复制 `.env.example` 为 `.env` 并修改：

```env
A2A_SERVER_PORT=13666
GATEWAY_URL=http://127.0.0.1:18789
GATEWAY_TIMEOUT=0
TASK_TIMEOUT=0
ULID_ENABLED=true
```

## 交流群

欢迎进群沟通

![交流群](./imgs/group.png)

## 赞助

如果你觉得有用，给作者续一杯 1M的Token

![赞助](./imgs/sponsor.png)

## 许可证

MIT License

---

*最后更新：2026-04-17*
