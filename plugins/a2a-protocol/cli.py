#!/usr/bin/env python3
"""
A2A CLI 工具
命令行测试客户端

概述:
    用于测试 A2A Server 的命令行工具。
    支持发送任务、查询状态、流式输出等功能。

使用方法:
    # 发送任务
    python cli.py send "写一个小说开头"
    
    # 发送任务（指定会话）
    python cli.py send "写一个小说开头" --ulid <ulid>
    
    # 查询任务状态
    python cli.py status <task_id>
    
    # 流式模式
    python cli.py stream "写一个小说开头"
    
    # 列出任务
    python cli.py list
    
    # 取消任务
    python cli.py cancel <task_id>
    
    # 获取 Agent Card
    python cli.py card
    
    # WebSocket 测试
    python cli.py ws "写一个小说开头"

帮助:
    python cli.py --help
"""

import asyncio
import argparse
import json
import sys
import time
from typing import Optional, Dict, Any

# 添加父目录到路径
sys.path.insert(0, "../..")

from agent.client import A2AClient


# ─────────────────────────────────────────────
# 颜色输出
# ─────────────────────────────────────────────
class Colors:
    """终端颜色"""
    RESET = "\033[0m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"


def color(text: str, c: str) -> str:
    """给文本上色"""
    return f"{c}{text}{Colors.RESET}"


def success(text: str) -> str:
    return color(text, Colors.GREEN)


def error(text: str) -> str:
    return color(text, Colors.RED)


def info(text: str) -> str:
    return color(text, Colors.BLUE)


def warning(text: str) -> str:
    return color(text, Colors.YELLOW)


def header(text: str) -> str:
    return color(text, Colors.CYAN + Colors.BOLD)


# ─────────────────────────────────────────────
# 打印函数
# ─────────────────────────────────────────────
def print_json(data: Dict[str, Any], indent: int = 2):
    """格式化打印 JSON"""
    print(json.dumps(data, indent=indent, ensure_ascii=False))


def print_task(task: Dict[str, Any]):
    """打印任务信息"""
    print(header("=" * 50))
    print(header("Task ID:"), task.get("id"))
    print(header("Session ID:"), task.get("session_id"))
    print(header("Status:"), task.get("status", {}).get("state"))
    print(header("Message:"), task.get("status", {}).get("message"))
    
    if task.get("messages"):
        print(header("\nMessages:"))
        for msg in task["messages"]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            print(f"  [{role}]: {content[:100]}...")
    
    if task.get("artifacts"):
        print(header("\nArtifacts:"))
        for artifact in task["artifacts"]:
            atype = artifact.get("type", "text")
            content = artifact.get("content", "")
            print(f"  [{atype}]: {content[:200]}...")
    
    print(header("=" * 50))


# ─────────────────────────────────────────────
# 命令处理
# ─────────────────────────────────────────────
async def cmd_send(client: A2AClient, content: str, ulid: Optional[str] = None,
                  name: str = "CLI", streaming: bool = False):
    """发送任务"""
    print(info(f"\n>>> 发送任务: {content[:50]}..."))
    if ulid:
        print(info(f">>> Session: {ulid}"))
    
    try:
        if streaming:
            # 流式模式
            print(info("\n>>> 流式输出:"))
            print(header("-" * 50))
            
            full_result = ""
            
            # 发送任务
            task_result = await client.send_task(
                content, 
                session_id=ulid,
                name=name,
                streaming=True
            )
            
            task_id = task_result.get("taskId")
            print(info(f">>> Task ID: {task_id}"))
            
            # 等待结果
            while True:
                result = await client.wait_for_task(task_id, timeout=120)
                if result:
                    break
                await asyncio.sleep(0.5)
            
            # 打印结果
            if result.get("artifacts"):
                for artifact in result["artifacts"]:
                    content = artifact.get("content", "")
                    print(content)
            
            print(header("-" * 50))
            print(success(f"\n>>> 任务完成!"))
            
        else:
            # 同步模式
            print(info("\n>>> 等待结果..."))
            result = await client.send_and_wait(content, name=name, session_id=ulid)
            
            print(header("\n>>> 结果:"))
            print(result.first_text if hasattr(result, 'first_text') else str(result))
            print()
    
    except Exception as e:
        print(error(f"\n>>> 错误: {e}"))
        sys.exit(1)


async def cmd_status(client: A2AClient, task_id: str):
    """查询任务状态"""
    try:
        task = await client.get_task(task_id)
        print_task(task)
    except Exception as e:
        print(error(f"错误: {e}"))
        sys.exit(1)


async def cmd_list(client: A2AClient, state: Optional[str] = None):
    """列出任务"""
    try:
        tasks = await client.list_tasks(state=state)
        print(header(f"\n>>> 任务列表 (共 {len(tasks)} 个):"))
        
        for task in tasks[-10:]:  # 只显示最近10个
            task_id = task.get("id", "")[:20]
            status = task.get("status", {}).get("state", "unknown")
            session_id = task.get("session_id", "")[:20]
            
            status_color = Colors.GREEN if status == "completed" else Colors.YELLOW
            print(f"  [{color(status, status_color):15}] {task_id}... | {session_id}...")
        
        print()
    
    except Exception as e:
        print(error(f"错误: {e}"))
        sys.exit(1)


async def cmd_cancel(client: A2AClient, task_id: str):
    """取消任务"""
    try:
        print(info(f"\n>>> 取消任务: {task_id}"))
        result = await client.cancel_task(task_id)
        print(success(f">>> 任务已取消"))
        print_json(result)
    except Exception as e:
        print(error(f"错误: {e}"))
        sys.exit(1)


async def cmd_card(client: A2AClient):
    """获取 Agent Card"""
    try:
        card = await client.get_agent_card()
        print(header("\n>>> Agent Card:"))
        print_json(card)
    except Exception as e:
        print(error(f"错误: {e}"))
        sys.exit(1)


async def cmd_ws(client: A2AClient, content: str, name: str = "CLI"):
    """WebSocket 测试"""
    print(info(f"\n>>> WebSocket 测试: {content[:50]}..."))
    
    try:
        result = await client.send_and_wait(content, name=name, use_websocket=True)
        print(header("\n>>> 结果:"))
        print(result.first_text if hasattr(result, 'first_text') else str(result))
        print()
    
    except Exception as e:
        print(error(f"错误: {e}"))
        sys.exit(1)


async def cmd_history(client: A2AClient, session_id: str):
    """获取会话历史"""
    try:
        history = await client.get_history(session_id)
        print(header(f"\n>>> 会话历史 ({session_id}):"))
        
        for i, msg in enumerate(history):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:100]
            print(f"  {i+1}. [{role}]: {content}...")
        
        print()
    
    except Exception as e:
        print(error(f"错误: {e}"))
        sys.exit(1)


async def cmd_sessions(client: A2AClient):
    """列出会话"""
    try:
        sessions = await client.list_sessions()
        print(header(f"\n>>> 活跃会话 (共 {len(sessions)} 个):"))
        
        for session in sessions[-10:]:
            session_id = session.get("session_id", "")[:30]
            created = session.get("created_at", "")[:19]
            print(f"  {session_id}... | {created}")
        
        print()
    
    except Exception as e:
        print(error(f"错误: {e}"))
        sys.exit(1)


# ─────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="A2A CLI 工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s send "写一个小说开头"              # 发送任务
  %(prog)s send "写一个小说" --stream        # 流式输出
  %(prog)s status <task_id>                   # 查询状态
  %(prog)s list                               # 列出任务
  %(prog)s cancel <task_id>                   # 取消任务
  %(prog)s card                              # 获取 Agent Card
  %(prog)s ws "测试"                          # WebSocket 测试
  %(prog)s history <session_id>              # 获取会话历史
  %(prog)s sessions                          # 列出活跃会话
"""
    )
    
    parser.add_argument("--url", "-u", default="http://127.0.0.1:13666",
                       help="A2A Server 地址 (默认: http://127.0.0.1:13666)")
    parser.add_argument("--name", "-n", default="CLI",
                       help="发送者名称 (默认: CLI)")
    parser.add_argument("--ulid", "-s", default=None,
                       help="指定会话 ULID")
    parser.add_argument("--stream", "-S", action="store_true",
                       help="使用流式输出")
    
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # send
    send_parser = subparsers.add_parser("send", help="发送任务")
    send_parser.add_argument("content", help="任务内容")
    send_parser.add_argument("--no-stream", dest="no_stream", action="store_true",
                            help="禁用流式输出")
    
    # status
    subparsers.add_parser("status", help="查询任务状态").add_argument("task_id", help="任务 ID")
    
    # list
    list_parser = subparsers.add_parser("list", help="列出任务")
    list_parser.add_argument("--state", "-s", choices=["submitted", "in_progress", 
                          "completed", "failed", "canceled"],
                          help="按状态筛选")
    
    # cancel
    subparsers.add_parser("cancel", help="取消任务").add_argument("task_id", help="任务 ID")
    
    # card
    subparsers.add_parser("card", help="获取 Agent Card")
    
    # ws
    subparsers.add_parser("ws", help="WebSocket 测试").add_argument("content", help="测试内容")
    
    # history
    subparsers.add_parser("history", help="获取会话历史").add_argument("session_id", help="会话 ID")
    
    # sessions
    subparsers.add_parser("sessions", help="列出活跃会话")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # 创建客户端
    client = A2AClient(args.url, client_id="cli")
    
    # 执行命令
    try:
        if args.command == "send":
            asyncio.run(cmd_send(
                client, 
                args.content, 
                ulid=args.ulid,
                name=args.name,
                streaming=args.stream and not args.no_stream
            ))
        elif args.command == "status":
            asyncio.run(cmd_status(client, args.task_id))
        elif args.command == "list":
            asyncio.run(cmd_list(client, args.state))
        elif args.command == "cancel":
            asyncio.run(cmd_cancel(client, args.task_id))
        elif args.command == "card":
            asyncio.run(cmd_card(client))
        elif args.command == "ws":
            asyncio.run(cmd_ws(client, args.content, args.name))
        elif args.command == "history":
            asyncio.run(cmd_history(client, args.session_id))
        elif args.command == "sessions":
            asyncio.run(cmd_sessions(client))
    
    finally:
        asyncio.run(client.close())


if __name__ == "__main__":
    main()
