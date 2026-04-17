"""
A2A Server - 标准 A2A 协议实现
主服务器文件
"""
import asyncio
import json
import logging
import os
import sys
import ulid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Query, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_card import AgentCard, Capabilities, Skill, registry, init_registry
from jsonrpc import JSONRPCRequest, JSONRPCResponse, JSONRPCError, methods
from task_manager import Task, TaskState, TaskStatus, task_store, Artifact, Message
from session import Session, session_store
from session_store import session_store as gateway_session_store, init_session_store, get_session_store  # ULID 会话队列
from sse import SSEClient, SSEStream, broadcaster
from websocket import ws_server, WSClient
from db import init_database, get_database, A2ADatabase

# Gateway 后端选择（支持 OpenClaw 和 Hermes）
from gateway_selector import GATEWAY_TYPE, get_gateway_bridge, gateway_bridge, gateway_config, gateway_health

if GATEWAY_TYPE.value == "hermes":
    from gateway_pool import get_gateway_pool, start_gateway_pool, GatewayPool
    logger.info(f"使用 Hermes Gateway 后端")
else:
    from gateway_pool import get_gateway_pool, start_gateway_pool, GatewayPool
    logger.info(f"使用 OpenClaw Gateway 后端")

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("a2a")

# 全局变量
PORT = 13666
HOST = "0.0.0.0"


# ─────────────────────────────────────────────
# FastAPI 应用
# ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    logger.info(f"A2A Server 启动 @ {HOST}:{PORT}")
    
    # 初始化数据库
    db = init_database()
    logger.info(f"数据库初始化: {db.db_path}")
    
    # 初始化会话存储（使用数据库）
    session_store_instance = await init_session_store()
    logger.info("会话存储已初始化")
    
    # 初始化 Agent Card 注册表（使用数据库缓存）
    init_registry(db)
    logger.info("Agent Card 注册表已初始化")
    
    # 启动 Gateway 连接池（自动重连）
    gateway_pool = await start_gateway_pool()
    logger.info(f"Gateway 连接池已启动: {gateway_pool.urls}")
    
    # 注册 JSON-RPC 方法
    register_jsonrpc_methods()
    register_default_agent()
    
    yield
    
    # 关闭时保存会话
    await session_store_instance.shutdown()
    await gateway_pool.stop()
    logger.info("Gateway 连接池已关闭")
    logger.info("会话存储已关闭")
    logger.info("A2A Server 关闭")


app = FastAPI(
    title="A2A Protocol Server",
    description="标准 A2A 协议实现 - 端口 13666",
    version="1.0.0",
    lifespan=lifespan
)


# ─────────────────────────────────────────────
# 路由：Agent Card
# ─────────────────────────────────────────────
@app.get("/.well-known/agent.json", response_model=dict)
async def get_agent_card():
    """获取 Agent Card (Agent 发现)"""
    card = registry.get("writer")
    if not card:
        raise HTTPException(status_code=404, detail="Agent not found")
    return card.to_dict()


@app.post("/agent/register")
async def register_agent(card_data: dict):
    """注册 Agent"""
    try:
        agent_card = AgentCard.from_dict(card_data)
        registry.register(agent_card)
        return {"status": "ok", "agent": agent_card.name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/agent/{name}")
async def unregister_agent(name: str):
    """注销 Agent"""
    registry.unregister(name)
    return {"status": "ok"}


@app.get("/agents")
async def list_agents():
    """列出所有 Agent"""
    agents = registry.list_all()
    return {"agents": [a.to_dict() for a in agents]}


# ─────────────────────────────────────────────
# 路由：JSON-RPC 2.0
# ─────────────────────────────────────────────
@app.post("/rpc")
async def handle_jsonrpc(request_data: dict):
    """处理 JSON-RPC 请求"""
    try:
        req = JSONRPCRequest.from_dict(request_data)
        
        if req.jsonrpc != "2.0":
            return JSONRPCResponse.error_resp(
                req.id,
                JSONRPCError.INVALID_REQUEST,
                "Invalid JSON-RPC version"
            ).to_dict()
        
        if not methods.has_method(req.method):
            return JSONRPCResponse.error_resp(
                req.id,
                JSONRPCError.METHOD_NOT_FOUND,
                f"Method not found: {req.method}"
            ).to_dict()
        
        result = await methods.call(req.method, req.params)
        return JSONRPCResponse.success(req.id, result).to_dict()
        
    except Exception as e:
        logger.error(f"JSON-RPC 处理异常: {e}")
        return JSONRPCResponse.error_resp(
            request_data.get("id", ""),
            JSONRPCError.INTERNAL_ERROR,
            str(e)
        ).to_dict()


# ─────────────────────────────────────────────
# 路由：SSE 流式
# ─────────────────────────────────────────────
@app.get("/sse/{client_id}")
async def sse_endpoint(client_id: str, request: Request):
    """SSE 流式端点"""
    sse_client = SSEClient(client_id)
    broadcaster.register(client_id, sse_client)
    
    async def event_generator():
        stream = SSEStream(sse_client)
        try:
            async for event in stream.event_stream():
                yield event
        finally:
            broadcaster.unregister(client_id)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ─────────────────────────────────────────────
# 路由：WebSocket
# ─────────────────────────────────────────────
@app.websocket("/ws/{client_id}")
async def websocket_endpoint(ws: WebSocket, client_id: str):
    """WebSocket 端点"""
    await ws.accept()
    
    client = ws_server.register_client(client_id, ws, str(ws.client))
    logger.info(f"WebSocket 连接: {client_id}")
    
    try:
        await client.send("welcome", {
            "client_id": client_id,
            "timestamp": datetime.now().isoformat(),
            "methods": methods.list_methods()
        })
        
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
                msg_type = msg.get("type", "")
                
                if msg_type == "ping":
                    await client.send("pong", {"timestamp": datetime.now().isoformat()})
                else:
                    await ws_server.handler.handle(msg_type, msg, client)
                    
            except json.JSONDecodeError:
                await client.send("error", {"error": "Invalid JSON"})
            except Exception as e:
                logger.error(f"WS 消息处理异常: {e}")
                await client.send("error", {"error": str(e)})
                
    except WebSocketDisconnect:
        logger.info(f"WebSocket 断开: {client_id}")
    except Exception as e:
        logger.error(f"WebSocket 异常: {e}")
    finally:
        ws_server.unregister_client(client_id)


# ─────────────────────────────────────────────
# 路由：任务管理
# ─────────────────────────────────────────────
@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """获取任务"""
    task = task_store.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task.to_dict()


@app.get("/tasks")
async def list_tasks(state: Optional[str] = None, session_id: Optional[str] = None):
    """列出任务"""
    if session_id:
        tasks = task_store.list_by_session(session_id)
    elif state:
        tasks = task_store.list_by_state(TaskState(state))
    else:
        tasks = list(task_store._tasks.values())
    return {"tasks": [t.to_dict() for t in tasks]}


@app.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """删除任务"""
    if task_store.delete(task_id):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Task not found")


# ─────────────────────────────────────────────
# 路由：会话管理
# ─────────────────────────────────────────────
@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """获取会话"""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session.to_dict()


@app.get("/sessions")
async def list_sessions():
    """列出所有会话"""
    sessions = session_store.list_active()
    return {"sessions": [s.to_dict() for s in sessions]}


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话"""
    # 清除 Gateway 会话
    gateway_bridge.clear_session(f"a2a:{session_id}")
    if session_store.delete(session_id):
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Session not found")


# ─────────────────────────────────────────────
# 路由：Gateway 状态
# ─────────────────────────────────────────────
@app.get("/gateway/status")
async def gateway_status():
    """Gateway 状态"""
    return {
        "is_alive": gateway_health.is_alive,
        "url": gateway_config.url,
        "clients": len(gateway_health._clients)
    }


@app.get("/queue/{ulid}")
async def get_queue_status(ulid: str):
    """获取 ULID 会话的队列状态"""
    status = await gateway_session_store.get_queue_status(ulid)
    return status


@app.get("/queues")
async def list_queues():
    """列出所有会话队列"""
    sessions = await gateway_session_store.list_sessions()
    return {"sessions": sessions}


@app.post("/gateway/health-check")
async def gateway_health_check():
    """Gateway 健康检查"""
    is_alive = await gateway_health.health_check()
    return {"is_alive": is_alive}


# ─────────────────────────────────────────────
# 路由：数据库和统计
# ─────────────────────────────────────────────
@app.get("/db/stats")
async def get_db_stats():
    """获取数据库统计"""
    db = get_database()
    store = get_session_store()
    return {
        "database": db.get_stats(),
        "memory_sessions": len(store._sessions) if hasattr(store, '_sessions') else 0,
        "active_tasks": len(task_store.list_active()),
        "total_tasks": len(task_store),
        "registered_agents": len(registry.list_all()),
    }


# ─────────────────────────────────────────────
# 路由：Gateway 状态
# ─────────────────────────────────────────────
@app.get("/gateway/pool")
async def get_gateway_pool_status():
    """获取 Gateway 连接池状态"""
    pool = get_gateway_pool()
    return pool.get_stats()


# ─────────────────────────────────────────────
# 路由：健康检查
# ─────────────────────────────────────────────
@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "A2A Protocol Server",
        "version": "1.0.0",
        "port": PORT,
        "agents": len(registry.list_all()),
        "tasks": len(task_store._tasks),
        "sessions": len(session_store.list_active()),
        "sse_clients": broadcaster.client_count,
        "ws_clients": ws_server.client_count,
        "gateway": {
            "url": gateway_config.url,
            "is_alive": gateway_health.is_alive
        }
    }


# ─────────────────────────────────────────────
# JSON-RPC 方法注册
# ─────────────────────────────────────────────
def register_jsonrpc_methods():
    """注册 JSON-RPC 方法"""
    
    async def tasks_send(params: dict) -> dict:
        """发送任务 - tasks/send
        
        支持 ULID 会话队列:
        - 首次调用自动生成 ULID
        - 同一 ULID 的任务排队执行
        - Gateway 忙时自动排队，完成后自动处理下一个
        """
        # 获取或生成 ULID
        ulid_str = params.get("ulid") or ulid.ulid()
        message = params.get("message", {})
        streaming = params.get("streaming", True)
        
        # 获取任务内容
        content = message.get("content", "") if message else ""
        
        # 加入队列（会自动判断是立即执行还是排队）
        gateway_task, is_first = await gateway_session_store.queue_task(ulid_str, content)
        
        # 保存 gateway_task 的 ulid（用于后续匹配）
        gateway_task.a2a_task_id = None  # 等待创建 A2A 任务后填充
        
        # 创建 A2A 任务（关联到 ULID）
        task = task_store.create(session_id=ulid_str)
        
        # 添加消息
        if message:
            task.add_message(message.get("role", "user"), content)
        
        # 状态转换
        if is_first:
            task.transition(TaskState.IN_PROGRESS, "开始处理")
        else:
            task.transition(TaskState.QUEUED, "排队等待")
        
        # 保存 ULID 到任务元数据
        task.metadata["ulid"] = ulid_str
        task.metadata["is_first"] = is_first
        task.metadata["queue_position"] = 0 if is_first else None
        
        # 关联 gateway_task 和 a2a_task
        gateway_task.a2a_task_id = task.id
        
        # 推送初始状态
        if streaming:
            await broadcaster.send_to(ulid_str, "task/status", {
                "taskId": task.id,
                "status": task.status.to_dict(),
                "ulid": ulid_str,
                "queue_position": task.metadata.get("queue_position"),
                "is_queued": not is_first
            })
        
        # 如果是第一个任务，立即执行
        if is_first:
            asyncio.create_task(execute_task(task, ulid_str, streaming))
        else:
            # 推送排队信息
            await broadcaster.send_to(ulid_str, "task/queued", {
                "taskId": task.id,
                "ulid": ulid_str,
                "message": "任务已排队，等待上一个任务完成"
            })
        
        return {
            "taskId": task.id, 
            "status": task.status.to_dict(),
            "ulid": ulid_str,
            "is_first": is_first
        }
    
    def is_self_intro_request(content: str) -> bool:
        """检查是否是自我介绍请求"""
        keywords = [
            "介绍", "自我介绍", "你是谁", "你是啥",
            "introduce", "who are you", "what are you",
            "介绍自己", "自我介绍一下", "说说你自己",
            "什么身份", "什么角色", "什么Agent"
        ]
        content_lower = content.lower()
        return any(kw in content_lower for kw in keywords)

    def get_self_intro() -> str:
        """获取自我介绍内容"""
        return """
🤖 **作家 (Writer) - AI 写作助手**

**基本信息**
- **角色**：首席写作专家，兼职首席技术官
- **上级**：钱多多 (GM Agent)
- **语言**：中文为主，专业场景中英混用

**核心能力**
- 📝 小说创作（番茄小说、网文写作）
- ✍️ 内容编辑（润色、改写、校对）
- 🎯 任务委派（A2A 协议）
- 💻 技术开发（Python、JavaScript）
- 📊 自媒体运营

**协作方式**
- 支持 A2A 协议（端口 13666）
- 可接收任务委派并返回结果
- 支持流式输出（SSE）

**当前状态**
- 🟢 运行中
- 📡 端口：13666
- 🔗 连接方式：WebSocket / SSE / HTTP JSON-RPC

---
如需帮助，请发送具体任务！
"""

    async def execute_task(task: Task, ulid: str, streaming: bool = True):
        """
        执行任务
        
        支持:
        - ULID 会话队列（完成后自动处理下一个）
        - 任务超时自动取消（默认 5 分钟）
        - 定期心跳推送
        - 任务取消
        - 自我介绍（直接响应）
        
        Args:
            task: 任务对象
            ulid: ULID 会话标识
            streaming: 是否流式执行
        """
        session_key = f"a2a:{ulid}"
        context = {
            "name": task.metadata.get("name", ""),
            "history": [m.to_dict() for m in task.messages[:-1]] if task.messages else []
        }
        
        # 获取任务内容
        content = task.messages[-1].content if task.messages else ""
        
        # 检查是否是自我介绍请求
        if is_self_intro_request(content):
            intro = get_self_intro()
            task.add_artifact(Artifact(type="text", content=intro))
            task.transition(TaskState.COMPLETED, "自我介绍完成")
            await broadcaster.send_to(task.session_id or task.id, "task/completed", {
                "taskId": task.id,
                "status": task.status.to_dict(),
                "artifacts": [a.to_dict() for a in task.artifacts]
            })
            
            # 通知队列处理下一个任务
            try:
                next_task = await gateway_session_store.complete_current_task(ulid, result=intro)
            except Exception as e:
                logger.error(f"队列处理异常: {e}")
            return
        
        try:
            if streaming:
                # 流式执行
                full_result = ""
                last_heartbeat = asyncio.get_event_loop().time()
                heartbeat_interval = 30  # 每 30 秒发一次心跳
                
                async for chunk in gateway_bridge.execute_stream(
                    content,
                    session_key,
                    context,
                    cancel_event=task.cancel_event
                ):
                    full_result += chunk
                    
                    # 推送部分结果
                    await broadcaster.send_to(task.session_id or task.id, "task/progress", {
                        "taskId": task.id,
                        "partial": chunk,
                        "accumulated": full_result
                    })
                    
                    # 定期心跳
                    current_time = asyncio.get_event_loop().time()
                    if current_time - last_heartbeat > heartbeat_interval:
                        await broadcaster.send_to(task.session_id or task.id, "task/heartbeat", {
                            "taskId": task.id,
                            "elapsed": int(current_time - last_heartbeat)
                        })
                        last_heartbeat = current_time
                    
                    # 检查超时
                    if task.is_expired:
                        logger.warning(f"Task {task.id} 超时 ({task.timeout_seconds}s)")
                        task.transition(TaskState.FAILED, f"任务超时 ({task.timeout_seconds}秒)")
                        await broadcaster.send_to(task.session_id or task.id, "task/timeout", {
                            "taskId": task.id,
                            "timeout": task.timeout_seconds
                        })
                        return
                    
                    # 检查取消
                    if task.is_canceled:
                        logger.info(f"Task {task.id} 被取消")
                        task.transition(TaskState.CANCELED, "任务被取消")
                        await broadcaster.send_to(task.session_id or task.id, "task/canceled", {
                            "taskId": task.id
                        })
                        return
                
                # 完成
                task.add_artifact(Artifact(type="text", content=full_result))
                task.transition(TaskState.COMPLETED, "处理完成")
                
                await broadcaster.send_to(task.session_id or task.id, "task/completed", {
                    "taskId": task.id,
                    "status": task.status.to_dict(),
                    "artifacts": [a.to_dict() for a in task.artifacts]
                })
                
                # 通知队列处理下一个任务
                logger.info(f"Task {task.id} 完成，ulid={ulid}，触发队列检查")
                try:
                    next_task = await gateway_session_store.complete_current_task(ulid, result=full_result)
                    if next_task:
                        logger.info(f"队列返回: SessionTask content={next_task.content[:30]}, a2a_task_id={next_task.a2a_task_id}")
                    else:
                        logger.info(f"队列返回: None (无更多任务)")
                except Exception as e:
                    logger.error(f"队列处理异常: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    next_task = None
                if next_task and next_task.a2a_task_id:
                    # 有排队的任务，找到关联的 A2A 任务并执行
                    logger.info(f"队列触发: 开始执行下一个任务")
                    a2a_task = task_store.get(next_task.a2a_task_id)
                    if a2a_task:
                        a2a_task.transition(TaskState.IN_PROGRESS, "队列任务开始")
                        asyncio.create_task(execute_task(a2a_task, ulid, streaming))
                    else:
                        logger.error(f"找不到关联的 A2A 任务: {next_task.a2a_task_id}")
                else:
                    logger.info(f"队列为空或 a2a_task_id 为空")
            else:
                # 同步执行
                result = await gateway_bridge.execute(content, session_key, context)
                
                task.add_artifact(Artifact(type="text", content=result))
                task.transition(TaskState.COMPLETED, "处理完成")
                
                await broadcaster.send_to(task.session_id or task.id, "task/completed", {
                    "taskId": task.id,
                    "status": task.status.to_dict(),
                    "artifacts": [a.to_dict() for a in task.artifacts]
                })
                
                # 通知队列处理下一个任务
                await gateway_session_store.complete_current_task(ulid, result=result)
                
        except Exception as e:
            logger.error(f"Task {task.id} 执行失败: {e}")
            task.transition(TaskState.FAILED, str(e))
            
            await broadcaster.send_to(task.session_id or task.id, "task/failed", {
                "taskId": task.id,
                "status": task.status.to_dict(),
                "error": str(e)
            })
            
            # 通知队列处理下一个任务（即使失败）
            next_task = await gateway_session_store.complete_current_task(ulid, error=str(e))
            if next_task:
                # 有排队的任务，自动启动执行
                logger.info(f"队列触发: 开始执行下一个任务（从失败恢复）")
                a2a_task = task_store.create(session_id=ulid)
                a2a_task.add_message("user", next_task.content)
                a2a_task.transition(TaskState.IN_PROGRESS, "队列任务开始")
                a2a_task.metadata["ulid"] = ulid
                a2a_task.metadata["is_queued"] = True
                asyncio.create_task(execute_task(a2a_task, ulid, streaming))
    
    async def tasks_get(params: dict) -> dict:
        """获取任务状态 - tasks/get"""
        task_id = params.get("taskId")
        task = task_store.get(task_id)
        if not task:
            raise Exception(f"Task not found: {task_id}")
        return task.to_dict()
    
    async def tasks_cancel(params: dict) -> dict:
        """取消任务 - tasks/cancel
        
        支持取消正在执行的任务（通过设置取消事件）。
        """
        task_id = params.get("taskId")
        task = task_store.get(task_id)
        if not task:
            raise Exception(f"Task not found: {task_id}")
        
        # 如果任务还在执行中，触发取消
        if task.is_active:
            task.cancel()  # 设置取消事件
            task.transition(TaskState.CANCELED, "用户取消")
        
        return {"taskId": task.id, "status": task.status.to_dict()}
    
    async def tasks_send_subscribe(params: dict) -> dict:
        """发送任务（订阅模式） - tasks/sendSubscribe"""
        # 与 tasks_send 类似，但确保流式
        params["streaming"] = True
        return await tasks_send(params)
    
    async def tasks_history(params: dict) -> dict:
        """获取任务历史 - tasks/history"""
        task_id = params.get("taskId")
        limit = params.get("limit", 10)
        task = task_store.get(task_id)
        if not task:
            raise Exception(f"Task not found: {task_id}")
        
        messages = task.messages[-limit:] if limit > 0 else task.messages
        return {"taskId": task_id, "messages": [m.to_dict() for m in messages]}
    
    async def sessions_create(params: dict) -> dict:
        """创建会话 - sessions/create"""
        session_id = params.get("sessionId")
        metadata = params.get("metadata", {})
        session = session_store.create(session_id, metadata)
        return session.to_dict()
    
    async def sessions_get(params: dict) -> dict:
        """获取会话 - sessions/get"""
        session_id = params.get("sessionId")
        session = session_store.get(session_id)
        if not session:
            raise Exception(f"Session not found: {session_id}")
        return session.to_dict()
    
    async def sessions_history(params: dict) -> dict:
        """获取会话历史 - sessions/history"""
        session_id = params.get("sessionId")
        limit = params.get("limit", 10)
        
        history = gateway_bridge.get_history(f"a2a:{session_id}", limit)
        return {"sessionId": session_id, "messages": history}
    
    async def agents_list(params: dict) -> dict:
        """列出所有 Agent - agents/list"""
        agents = registry.list_all()
        return {"agents": [a.to_dict() for a in agents]}
    
    async def agents_get(params: dict) -> dict:
        """获取 Agent - agents/get"""
        name = params.get("name")
        agent = registry.get(name)
        if not agent:
            raise Exception(f"Agent not found: {name}")
        return agent.to_dict()
    
    async def gateway_execute(params: dict) -> dict:
        """直接执行（不创建任务）- gateway/execute"""
        content = params.get("content", "")
        session_id = params.get("sessionId", "")
        name = params.get("name", "")
        
        session_key = f"a2a:{session_id}" if session_id else f"a2a:direct"
        context = {"name": name}
        
        result = await gateway_bridge.execute(content, session_key, context)
        return {"result": result}
    
    # 注册方法
    methods.register("tasks/send", tasks_send)
    methods.register("tasks/get", tasks_get)
    methods.register("tasks/cancel", tasks_cancel)
    methods.register("tasks/sendSubscribe", tasks_send_subscribe)
    methods.register("tasks/history", tasks_history)
    
    methods.register("sessions/create", sessions_create)
    methods.register("sessions/get", sessions_get)
    methods.register("sessions/history", sessions_history)
    
    methods.register("agents/list", agents_list)
    methods.register("agents/get", agents_get)
    
    methods.register("gateway/execute", gateway_execute)
    
    logger.info(f"JSON-RPC 方法注册完成: {methods.list_methods()}")


def register_default_agent():
    """注册默认 Agent Card"""
    default_card = AgentCard(
        name="writer",
        description="AI写作助手 - 首席写作专家，专精番茄小说和自媒体内容",
        url=f"http://0.0.0.0:{PORT}",
        version="1.0.0",
        capabilities=Capabilities(
            streaming=True,
            push_notifications=True,
            state_transitions=True,
            sessions=True
        ),
        skills=[
            Skill(id="novel", name="小说写作", description="番茄小说创作，穿越/重生/系统流"),
            Skill(id="copywriting", name="文案撰写", description="各类文案、营销内容"),
            Skill(id="editing", name="文章润色", description="润色、改稿、优化"),
        ],
        owner="Hermes Agents"
    )
    registry.register(default_card)
    logger.info(f"默认 Agent 已注册: {default_card.name}")


# ─────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────
def main():
    logger.info("=" * 50)
    logger.info(f"A2A Protocol Server")
    logger.info(f"端口: {PORT}")
    logger.info(f"Agent: writer")
    logger.info(f"Endpoint: http://0.0.0.0:{PORT}")
    logger.info(f"Gateway: {gateway_config.url}")
    logger.info("=" * 50)
    
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
