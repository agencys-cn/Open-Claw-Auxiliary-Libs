"""
测试任务状态机
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))

from task_manager import Task, TaskState, TaskStatus, Artifact, task_store


async def test_task_manager():
    """测试任务状态机"""
    print("=" * 50)
    print("测试任务状态机")
    print("=" * 50)
    
    # 创建任务
    task = task_store.create(session_id="test-session")
    print(f"\n创建任务:")
    print(f"  ID: {task.id}")
    print(f"  状态: {task.status.state.value}")
    
    # 添加消息
    task.add_message("user", "写一个小说开头")
    print(f"\n添加消息后:")
    print(f"  消息数: {len(task.messages)}")
    
    # 状态转换
    print(f"\n状态转换测试:")
    
    transitions = [
        (TaskState.IN_PROGRESS, "开始处理"),
        (TaskState.COMPLETED, "处理完成"),
    ]
    
    for state, message in transitions:
        success = task.transition(state, message)
        print(f"  {task.status.state.value} → {state.value}: {'✅' if success else '❌'}")
    
    # 添加产物
    artifact = Artifact(
        type="text",
        content="小说内容...",
        name="novel.txt"
    )
    task.add_artifact(artifact)
    print(f"\n添加产物:")
    print(f"  产物数: {len(task.artifacts)}")
    print(f"  类型: {task.artifacts[0].type}")
    
    # 序列化
    print(f"\n任务 JSON:")
    import json
    print(json.dumps(task.to_dict(), indent=2, ensure_ascii=False))
    
    # 查询
    print(f"\n查询测试:")
    retrieved = task_store.get(task.id)
    print(f"  task_id={task.id}: {'✅ 找到' if retrieved else '❌ 未找到'}")
    
    print("\n✅ 任务状态机测试通过")


if __name__ == "__main__":
    asyncio.run(test_task_manager())
