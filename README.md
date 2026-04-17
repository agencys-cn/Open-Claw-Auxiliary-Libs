# Open-Claw-Auxiliary-Libs

OpenClaw 辅助库和插件集合。

---

## 📁 目录结构

```
Open-Claw-Auxiliary-Libs/
├── plugins/              # 代码项目
│   └── a2a-protocol/     # A2A 协议标准实现
├── skills/               # 技能文件
├── prompt/               # 学习指令文件
├── other/                # 暂时未归类的内容
└── imgs/                 # 图片资源
```

---

## 🚀 plugins

代码项目目录，以项目为单位组织。

### 📌 a2a-protocol

**A2A (Agent-to-Agent) Protocol 标准实现**

| 项目 | 说明 |
|------|------|
| 协议 | JSON-RPC 2.0 |
| 通信 | SSE / WebSocket |
| 会话 | ULID 唯一标识 |
| 任务 | 状态机管理 |
| 桥接 | OpenClaw Gateway |

**特性：**
- ✅ JSON-RPC 2.0 标准协议
- ✅ SSE 服务器推送
- ✅ WebSocket 双向通信
- ✅ ULID 会话队列
- ✅ Gateway 桥接

**详细文档：** → [plugins/a2a-protocol/README.md](plugins/a2a-protocol/README.md)

**快速开始：**
```bash
cd plugins/a2a-protocol
uv sync
uv run python server/main.py
```

---

## 🛠️ skills

技能文件目录，以技能名称组织。

> 暂无技能文件

---

## 📝 prompt

学习指令文件目录。

> 暂无学习指令

---

## 📦 other

暂时未归类的内容。

> 暂无内容

---

## 📸 图片资源

| 交流群 | 赞助 |
|:---:|:---:|
| ![交流群](./imgs/group.png) | ![赞助](./imgs/sponsor.png) |
| 欢迎进群沟通 | 给作者续一杯 1M的Token |

---

## 许可证

MIT License

---

*最后更新：2026-04-17*
