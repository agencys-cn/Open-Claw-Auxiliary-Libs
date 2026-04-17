"""
JSON-RPC 2.0 消息处理
A2A 协议核心组件

概述:
    实现 JSON-RPC 2.0 协议规范，用于 A2A 消息的请求/响应处理。
    支持标准方法调用和错误处理。

规范:
    - JSON-RPC 2.0 规范: https://www.jsonrpc.org/specification
    - 请求必须是 POST + JSON
    - 响应包含 result 或 error，不能同时包含两者

错误码:
    - 标准错误码: -32700 到 -32603
    - 业务错误码: -32001 到 -32099

示例:
    >>> request = JSONRPCRequest(method="tasks/send", params={"content": "hello"})
    >>> response = JSONRPCResponse.success(id=request.id, result={"taskId": "123"})
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, Callable, Dict, List
from datetime import datetime
import uuid

logger = logging.getLogger("a2a")


@dataclass
class JSONRPCRequest:
    """
    JSON-RPC 2.0 请求对象
    
    Attributes:
        jsonrpc: JSON-RPC 版本，必须是 "2.0"
        id: 请求标识符，用于匹配响应，可以是字符串或数字
        method: 要调用的方法名
        params: 方法参数对象
    
    Example:
        >>> req = JSONRPCRequest(
        ...     method="tasks/send",
        ...     params={"sessionId": "s1", "message": {"content": "hello"}}
        ... )
        >>> print(req.to_json())
    """
    jsonrpc: str = "2.0"
    id: str = ""
    method: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """自动生成请求 ID"""
        if not self.id:
            self.id = str(uuid.uuid4())
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JSONRPCRequest":
        """
        从字典创建请求对象
        
        Args:
            data: 包含请求数据的字典
        
        Returns:
            JSONRPCRequest 实例
        """
        return cls(
            jsonrpc=data.get("jsonrpc", "2.0"),
            id=str(data.get("id", "")),
            method=data.get("method", ""),
            params=data.get("params", {})
        )
    
    @classmethod
    def from_json(cls, json_str: str) -> "JSONRPCRequest":
        """
        从 JSON 字符串创建请求对象
        
        Args:
            json_str: JSON 格式字符串
        
        Returns:
            JSONRPCRequest 实例
        """
        return cls.from_dict(json.loads(json_str))
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)
    
    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class JSONRPCResponse:
    """
    JSON-RPC 2.0 响应对象
    
    响应必须包含 id（与请求匹配），以及 result 或 error 之一。
    
    Attributes:
        jsonrpc: JSON-RPC 版本，必须是 "2.0"
        id: 请求标识符
        result: 方法执行结果（成功时）
        error: 错误信息（失败时）
    
    Example:
        >>> # 成功响应
        >>> resp = JSONRPCResponse.success(id="req-123", result={"taskId": "task-1"})
        >>> 
        >>> # 错误响应
        >>> resp = JSONRPCResponse.error(id="req-123", code=-32601, message="Method not found")
    """
    jsonrpc: str = "2.0"
    id: str = ""
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        """验证响应有效性"""
        if self.result is not None and self.error is not None:
            raise ValueError("result and error cannot be both set")
    
    @classmethod
    def success(cls, id: str, result: Any) -> "JSONRPCResponse":
        """
        创建成功响应
        
        Args:
            id: 请求标识符
            result: 方法执行结果
        
        Returns:
            JSONRPCResponse 实例
        """
        return cls(id=id, result=result)
    
    @classmethod
    def error_resp(cls, id: str, code: int, message: str, data: Any = None) -> "JSONRPCResponse":
        """
        创建错误响应
        
        Args:
            id: 请求标识符
            code: 错误码
            message: 错误消息
            data: 额外错误数据（可选）
        
        Returns:
            JSONRPCResponse 实例
        """
        error = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return cls(id=id, error=error)
    
    def to_dict(self) -> Dict[str, Any]:
        """
        转换为字典
        
        只包含 result 或 error 之一，符合 JSON-RPC 2.0 规范。
        """
        base = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            base["error"] = self.error
        else:
            base["result"] = self.result
        return base
    
    def to_json(self) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)
    
    @property
    def is_success(self) -> bool:
        """是否是成功响应"""
        return self.error is None


class JSONRPCError:
    """
    JSON-RPC 2.0 错误码定义
    
    错误码范围:
        -32768 至 -32000: 保留用于错误定义
        -32099 至 -32001: 应用程序自定义错误
    
    Attributes:
        PARSE_ERROR: -32700 - 解析错误
        INVALID_REQUEST: -32600 - 无效请求
        METHOD_NOT_FOUND: -32601 - 方法未找到
        INVALID_PARAMS: -32602 - 无效参数
        INTERNAL_ERROR: -32603 - 内部错误
        TASK_NOT_FOUND: -32001 - 任务未找到
        TASK_INVALID_STATE: -32002 - 任务状态无效
        AGENT_NOT_FOUND: -32003 - Agent 未找到
        SESSION_NOT_FOUND: -32004 - 会话未找到
        CAPABILITY_NOT_SUPPORTED: -32005 - 能力不支持
        STREAMING_ERROR: -32006 - 流式错误
    
    Example:
        >>> raise JSONRPCException(JSONRPCError.METHOD_NOT_FOUND, "Method not found")
    """
    
    # 标准错误码（JSON-RPC 规范定义）
    PARSE_ERROR = -32700          # 解析错误 - 无效的 JSON
    INVALID_REQUEST = -32600      # 无效请求 - 不是有效的 JSON-RPC 请求对象
    METHOD_NOT_FOUND = -32601     # 方法未找到 - 方法不存在或不可用
    INVALID_PARAMS = -32602       # 无效参数 - 方法参数无效
    INTERNAL_ERROR = -32603      # 内部错误 - 内部错误
    
    # 应用程序错误码（可自定义）
    TASK_NOT_FOUND = -32001       # 任务未找到
    TASK_INVALID_STATE = -32002   # 任务状态无效
    AGENT_NOT_FOUND = -32003      # Agent 未找到
    SESSION_NOT_FOUND = -32004   # 会话未找到
    CAPABILITY_NOT_SUPPORTED = -32005  # 能力不支持
    STREAMING_ERROR = -32006     # 流式错误


class JSONRPCException(Exception):
    """
    JSON-RPC 异常
    
    用于在方法执行过程中抛出错误。
    
    Attributes:
        code: 错误码
        message: 错误消息
        data: 额外数据
    
    Example:
        >>> raise JSONRPCException(JSONRPCError.TASK_NOT_FOUND, "Task task-123 not found")
    """
    
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"[{code}] {message}")


class JSONRPCMethod:
    """
    JSON-RPC 方法注册表
    
    管理和调用已注册的 JSON-RPC 方法。
    
    Attributes:
        _methods: 方法名到处理函数的映射
    
    Example:
        >>> methods = JSONRPCMethod()
        >>> methods.register("tasks/send", handle_tasks_send)
        >>> result = await methods.call("tasks/send", {"sessionId": "s1"})
    """
    
    def __init__(self):
        """初始化方法注册表"""
        self._methods: Dict[str, Callable] = {}
    
    def register(self, name: str, handler: Callable) -> None:
        """
        注册方法处理器
        
        Args:
            name: 方法名称，如 "tasks/send"
            handler: 异步处理函数，接受 params dict，返回结果
        
        Example:
            >>> async def handle_tasks_send(params):
            ...     return {"taskId": "123"}
            >>> methods.register("tasks/send", handle_tasks_send)
        """
        self._methods[name] = handler
        logger.debug(f"JSON-RPC 方法注册: {name}")
    
    def unregister(self, name: str) -> None:
        """
        注销方法
        
        Args:
            name: 方法名称
        """
        if name in self._methods:
            del self._methods[name]
    
    async def call(self, name: str, params: Dict[str, Any]) -> Any:
        """
        调用方法
        
        Args:
            name: 方法名称
            params: 方法参数
        
        Returns:
            方法执行结果
        
        Raises:
            JSONRPCException: 如果方法不存在
        """
        if name not in self._methods:
            raise JSONRPCException(
                JSONRPCError.METHOD_NOT_FOUND,
                f"Method not found: {name}"
            )
        handler = self._methods[name]
        if asyncio.iscoroutinefunction(handler):
            return await handler(params)
        return handler(params)
    
    def has_method(self, name: str) -> bool:
        """
        检查方法是否存在
        
        Args:
            name: 方法名称
        
        Returns:
            如果方法存在返回 True
        """
        return name in self._methods
    
    def list_methods(self) -> List[str]:
        """
        列出所有已注册的方法
        
        Returns:
            方法名列表
        """
        return list(self._methods.keys())


# 需要在文件顶部导入 asyncio
import asyncio

# 全局方法注册表
methods = JSONRPCMethod()


# ─────────────────────────────────────────────
# 便捷函数
# ─────────────────────────────────────────────

def jsonrpc_request(method: str, params: Dict[str, Any] = None, id: str = None) -> JSONRPCRequest:
    """
    创建 JSON-RPC 请求的便捷函数
    
    Args:
        method: 方法名称
        params: 方法参数
        id: 请求 ID（可选，自动生成）
    
    Returns:
        JSONRPCRequest 实例
    
    Example:
        >>> req = jsonrpc_request("tasks/send", {"sessionId": "s1"})
    """
    return JSONRPCRequest(
        id=id or str(uuid.uuid4()),
        method=method,
        params=params or {}
    )


def jsonrpc_response_success(result: Any, id: str) -> JSONRPCResponse:
    """
    创建成功响应的便捷函数
    
    Args:
        result: 执行结果
        id: 请求 ID
    
    Returns:
        JSONRPCResponse 实例
    """
    return JSONRPCResponse.success(id=id, result=result)


def jsonrpc_response_error(code: int, message: str, id: str, data: Any = None) -> JSONRPCResponse:
    """
    创建错误响应的便捷函数
    
    Args:
        code: 错误码
        message: 错误消息
        id: 请求 ID
        data: 额外数据
    
    Returns:
        JSONRPCResponse 实例
    """
    return JSONRPCResponse.error_resp(id=id, code=code, message=message, data=data)
