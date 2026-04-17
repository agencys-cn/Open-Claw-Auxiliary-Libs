#!/usr/bin/env python3
"""
测试 SSE 订阅功能
"""
import requests
import json
import time

SERVER = "http://127.0.0.1:13666"
CLIENT_ID = "test-sse-client"

def main():
    print("=== 1. 连接 SSE ===")
    response = requests.get(f"{SERVER}/sse/{CLIENT_ID}", stream=True)
    print(f"SSE 连接状态: {response.status_code}")
    
    # 启动 SSE 监听线程
    import threading
    events = []
    
    def listen():
        for line in response.iter_lines(decode_unicode=True):
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
                events.append((event_type, data))
                print(f"收到事件: {event_type} → {data.get('taskId', data.get('type', ''))}")
    
    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    
    time.sleep(1)  # 等待连接建立
    
    print("\n=== 2. 发送任务 ===")
    result = requests.post(f"{SERVER}/rpc", json={
        "jsonrpc": "2.0",
        "method": "tasks/send",
        "params": {
            "message": {"role": "user", "content": "讲一个1+1=3的笑话"}
        }
    }, timeout=5).json()
    
    task_id = result.get("result", {}).get("taskId")
    print(f"任务 ID: {task_id}")
    
    print("\n=== 3. 等待 SSE 推送... ===")
    # 等待最多 30 秒
    for i in range(30):
        time.sleep(1)
        # 检查是否有完成事件
        completed = [e for e in events if e[0] == "task/completed"]
        if completed:
            print(f"\n=== 4. 任务完成！ ===")
            print(f"结果: {completed[0][1].get('artifacts', [{}])[0].get('content', '')[:100]}...")
            break
        print(f"  ...等待 {i+1}秒，收到 {len(events)} 个事件")
    
    if not completed:
        print("\n=== 超时，事件列表 ===")
        for e in events:
            print(f"  {e[0]}: {e[1]}")
    
    response.close()

if __name__ == "__main__":
    main()
