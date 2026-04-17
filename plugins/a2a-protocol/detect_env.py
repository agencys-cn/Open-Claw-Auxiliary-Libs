#!/usr/bin/env python3
"""
Gateway 环境检测工具

检测当前环境是 OpenClaw 还是 Hermes Agent，
并显示可用的 Gateway 配置。

使用:
    python detect_env.py
"""

import os
import sys
import json
import asyncio
import httpx


def detect_environment():
    """检测环境类型"""
    results = {
        "environment": "unknown",
        "openclaw": {
            "url": os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789"),
            "token_set": bool(os.getenv("OPENCLAW_GATEWAY_TOKEN")),
            "timeout": float(os.getenv("OPENCLAW_GATEWAY_TIMEOUT", "0")),
        },
        "hermes": {
            "url": os.getenv("HERMES_GATEWAY_URL", ""),
            "token_set": bool(os.getenv("HERMES_GATEWAY_TOKEN")),
        },
        "openclaw_available": False,
        "hermes_available": False,
    }
    
    # 判断环境
    if os.getenv("HERMES_GATEWAY_URL"):
        results["environment"] = "hermes"
    elif os.getenv("OPENCLAW_GATEWAY_URL"):
        results["environment"] = "openclaw"
    else:
        # 默认检测
        if os.getenv("HERMES_MODE"):
            results["environment"] = "hermes"
        else:
            results["environment"] = "openclaw"
    
    return results


async def check_gateway(url: str, timeout: float = 5.0) -> dict:
    """检查 Gateway 是否可用"""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{url}/health")
            if response.status_code == 200:
                return {"available": True, "status": "ok"}
            return {"available": False, "status": f"HTTP {response.status_code}"}
    except httpx.ConnectError:
        return {"available": False, "status": "连接失败"}
    except Exception as e:
        return {"available": False, "status": str(e)}


async def check_openclaw():
    """检查 OpenClaw Gateway"""
    url = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789")
    return await check_gateway(url)


async def check_hermes():
    """检查 Hermes Gateway"""
    url = os.getenv("HERMES_GATEWAY_URL", "")
    if not url:
        return {"available": False, "status": "未配置 HERMES_GATEWAY_URL"}
    return await check_gateway(url)


async def main():
    print("=" * 50)
    print("Gateway 环境检测")
    print("=" * 50)
    print()
    
    # 检测环境
    env = detect_environment()
    
    print(f"检测到环境: {env['environment'].upper()}")
    print()
    
    # 显示配置
    print("--- OpenClaw 配置 ---")
    print(f"  URL: {env['openclaw']['url']}")
    print(f"  Token: {'已设置' if env['openclaw']['token_set'] else '未设置'}")
    print(f"  Timeout: {env['openclaw']['timeout']}s")
    
    print()
    
    print("--- Hermes 配置 ---")
    if env['hermes']['url']:
        print(f"  URL: {env['hermes']['url']}")
        print(f"  Token: {'已设置' if env['hermes']['token_set'] else '未设置'}")
    else:
        print("  URL: 未配置")
    
    print()
    
    # 检查可用性
    print("--- Gateway 可用性检查 ---")
    
    openclaw_status = await check_openclaw()
    print(f"  OpenClaw: {'✅ 可用' if openclaw_status['available'] else '❌ 不可用'} ({openclaw_status['status']})")
    
    hermes_status = await check_hermes()
    print(f"  Hermes: {'✅ 可用' if hermes_status['available'] else '❌ 不可用'} ({hermes_status['status']})")
    
    print()
    
    # 建议
    print("--- 建议 ---")
    if env['environment'] == 'openclaw':
        if not openclaw_status['available']:
            print("  ⚠️ OpenClaw Gateway 不可用，请检查是否启动")
            print("  💡 可设置 HERMES_GATEWAY_URL 切换到 Hermes Agent")
        else:
            print("  ✅ OpenClaw Gateway 正常")
    elif env['environment'] == 'hermes':
        if not hermes_status['available']:
            print("  ⚠️ Hermes Gateway 不可用，请检查是否启动")
            print("  💡 可设置 OPENCLAW_GATEWAY_URL 切换到 OpenClaw")
        else:
            print("  ✅ Hermes Gateway 正常")
    
    print()
    print("=" * 50)
    
    # 返回码
    if env['environment'] == 'openclaw' and openclaw_status['available']:
        return 0
    if env['environment'] == 'hermes' and hermes_status['available']:
        return 0
    return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
