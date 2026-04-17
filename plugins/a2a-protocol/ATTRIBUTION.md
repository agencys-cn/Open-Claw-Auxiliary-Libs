# 归属与致谢

## 第三方库

本项目使用了以下开源库：

| 库 | 版本 | 许可证 | 用途 |
|----|------|---------|------|
| [FastAPI](https://github.com/tiangolo/fastapi) | >=0.100.0 | MIT | Web 框架 |
| [Uvicorn](https://github.com/encode/uvicorn) | >=0.23.0 | BSD-3 | ASGI 服务器 |
| [WebSockets](https://github.com/aaugustin/websockets) | >=11.0.0 | BSD-3 | WebSocket 支持 |
| [HTTPX](https://github.com/encode/httpx) | >=0.25.0 | BSD-3 | HTTP 客户端 |
| [Pydantic](https://github.com/pydantic/pydantic) | >=2.0 | MIT | 数据验证 |
| [ulid](https://github.com/mhughesacm/python-ulid) | >=1.1 | MIT | ULID 生成 |

## A2A 协议

A2A (Agent-to-Agent) 协议规范由 **Anthropic** 提出，旨在实现 AI Agent 之间的标准化通信。

本实现参考了：
- [A2A Protocol Specification](https://github.com/anthropics/anthropic-cookbook) - Anthropic 官方规范
- [OpenClaw A2A Implementation](https://github.com/openclaw/openclaw) - OpenClaw 的 A2A 实现参考

## 项目架构

本实现基于以下理念设计：

1. **标准兼容性** - 遵循 JSON-RPC 2.0 规范
2. **流式优先** - 支持 SSE 和 WebSocket 实时推送
3. **会话隔离** - 使用 ULID 实现唯一会话标识
4. **任务队列** - 支持同一会话的任务排队和自动调度

## 许可证

本项目代码（除上述第三方库外）采用 MIT 许可证。

---

*最后更新：2026-04-17*
