"""
认证授权
A2A 协议扩展 - API Key / Token 认证

概述:
    支持多种认证方式：
    1. API Key 认证（推荐）
    2. Bearer Token 认证
    3. IP 白名单（可选）

认证流程:
    1. 客户端携带 API Key 或 Token
    2. 服务器验证 Key/Token 的有效性
    3. 验证通过后允许请求，否则返回 401

示例:
    >>> auth = AuthManager()
    >>> 
    >>> # 添加 API Key
    >>> auth.add_api_key("key_abc123", "client_name", level="admin")
    >>> 
    >>> # 验证请求
    >>> result = await auth.authenticate(request)
    >>> if not result.is_valid:
    ...     raise AuthenticationError(result.error)

使用方法:
    1. 在 .env 中配置 AUTH_ENABLED=true
    2. 在 .env 中配置 API_KEYS=key1,key2,key3
    3. 或通过代码动态添加 Key
"""

import asyncio
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Set
from functools import wraps

logger = logging.getLogger("a2a")


class AuthLevel(Enum):
    """认证级别"""
    NONE = "none"       # 无限制
    READ = "read"       # 只读
    WRITE = "write"     # 读写
    ADMIN = "admin"     # 完全控制


class AuthenticationError(Exception):
    """
    认证异常
    
    认证失败时抛出。
    
    Attributes:
        error_code: 错误代码
        message: 错误消息
    """
    
    def __init__(self, message: str, error_code: str = "AUTH_FAILED"):
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class AuthorizationError(Exception):
    """
    授权异常
    
    权限不足时抛出。
    
    Attributes:
        required_level: 需要的权限级别
        current_level: 当前权限级别
    """
    
    def __init__(self, required: AuthLevel, current: AuthLevel):
        message = f"权限不足: 需要 {required.value}, 当前 {current.value}"
        super().__init__(message)
        self.required_level = required
        self.current_level = current


@dataclass
class AuthResult:
    """
    认证结果
    
    Attributes:
        is_valid: 认证是否成功
        client_id: 客户端 ID
        auth_level: 权限级别
        error: 错误消息（如果失败）
        error_code: 错误代码
    """
    is_valid: bool = False
    client_id: str = ""
    auth_level: AuthLevel = AuthLevel.NONE
    error: str = ""
    error_code: str = ""
    
    @classmethod
    def success(cls, client_id: str, level: AuthLevel = AuthLevel.READ) -> "AuthResult":
        """创建成功结果"""
        return cls(is_valid=True, client_id=client_id, auth_level=level)
    
    @classmethod
    def failure(cls, error: str, error_code: str = "AUTH_FAILED") -> "AuthResult":
        """创建失败结果"""
        return cls(is_valid=False, error=error, error_code=error_code)


@dataclass
class APIKey:
    """
    API Key 记录
    
    Attributes:
        key: API Key（原始或哈希）
        key_hint: Key 提示（用于识别，不含完整 Key）
        client_id: 客户端 ID
        auth_level: 权限级别
        created_at: 创建时间
        expires_at: 过期时间（可选）
        rate_limit: 速率限制（请求/分钟）
        is_active: 是否启用
        last_used: 最后使用时间
        request_count: 请求计数
    """
    key: str
    key_hint: str
    client_id: str
    auth_level: AuthLevel = AuthLevel.READ
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    rate_limit: int = 60
    is_active: bool = True
    last_used: Optional[datetime] = None
    request_count: int = 0
    
    def is_expired(self) -> bool:
        """是否已过期"""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at
    
    def is_valid(self) -> bool:
        """是否有效"""
        return self.is_active and not self.is_expired()
    
    def to_dict(self) -> dict:
        """转换为字典（不包含 key）"""
        return {
            "key_hint": self.key_hint,
            "client_id": self.client_id,
            "auth_level": self.auth_level.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "rate_limit": self.rate_limit,
            "is_active": self.is_active,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "request_count": self.request_count,
        }


class AuthManager:
    """
    认证管理器
    
    管理 API Key、Token 验证、权限控制。
    
    Attributes:
        _api_keys: API Key 字典（key_hash -> APIKey）
        _tokens: Token 字典（token -> client_id）
        _trusted_ips: 信任的 IP 列表
        _enabled: 是否启用认证
    
    Example:
        >>> manager = AuthManager()
        >>> 
        >>> # 添加 API Key
        >>> key = manager.create_api_key("my_client", level=AuthLevel.WRITE)
        >>> print(f"新 Key: {key}")  # 只在创建时返回一次
        >>> 
        >>> # 验证
        >>> result = await manager.authenticate(request)
        >>> if not result.is_valid:
        ...     raise AuthenticationError(result.error)
    """
    
    def __init__(self, enabled: bool = True):
        """
        初始化认证管理器
        
        Args:
            enabled: 是否启用认证（False 时跳过验证）
        """
        self._enabled = enabled
        self._api_keys: Dict[str, APIKey] = {}  # key_hash -> APIKey
        self._tokens: Dict[str, str] = {}  # token -> client_id
        self._trusted_ips: Set[str] = {"127.0.0.1", "::1"}
        self._lock = asyncio.Lock()
        
        # 从环境变量加载配置
        self._load_from_env()
    
    def _load_from_env(self):
        """从环境变量加载配置"""
        # 启用认证
        auth_enabled = os.getenv("AUTH_ENABLED", "false").lower()
        self._enabled = auth_enabled in ("true", "1", "yes")
        
        # 加载预定义的 API Keys
        api_keys_str = os.getenv("API_KEYS", "")
        if api_keys_str:
            for i, key in enumerate(api_keys_str.split(",")):
                key = key.strip()
                if key:
                    self.add_api_key(
                        key, 
                        f"client_{i}",
                        level=AuthLevel.WRITE
                    )
        
        # 加载信任的 IP
        trusted_ips_str = os.getenv("TRUSTED_IPS", "")
        if trusted_ips_str:
            self._trusted_ips.update(
                ip.strip() for ip in trusted_ips_str.split(",")
            )
        
        logger.info(f"认证配置: enabled={self._enabled}, "
                   f"keys={len(self._api_keys)}, "
                   f"trusted_ips={len(self._trusted_ips)}")
    
    @staticmethod
    def hash_key(key: str) -> str:
        """对 Key 进行哈希"""
        return hashlib.sha256(key.encode()).hexdigest()[:16]
    
    @staticmethod
    def generate_key(prefix: str = "sk_", length: int = 32) -> str:
        """
        生成随机 API Key
        
        Args:
            prefix: Key 前缀
            length: Key 长度（不含前缀）
        
        Returns:
            生成的 API Key
        """
        random_part = secrets.token_urlsafe(length)[:length]
        return f"{prefix}{random_part}"
    
    def create_api_key(self, client_id: str, 
                       level: AuthLevel = AuthLevel.READ,
                       expires_days: Optional[int] = None) -> str:
        """
        创建新的 API Key
        
        Args:
            client_id: 客户端 ID
            level: 权限级别
            expires_days: 过期天数（None 表示不过期）
        
        Returns:
            生成的 API Key（只返回一次）
        """
        raw_key = self.generate_key()
        key_hint = raw_key[:8] + "..." + raw_key[-4:]
        
        expires_at = None
        if expires_days:
            expires_at = datetime.now() + timedelta(days=expires_days)
        
        api_key = APIKey(
            key=self.hash_key(raw_key),
            key_hint=key_hint,
            client_id=client_id,
            auth_level=level,
            expires_at=expires_at,
        )
        
        self._api_keys[api_key.key] = api_key
        logger.info(f"API Key 创建: {key_hint} -> {client_id} ({level.value})")
        
        return raw_key
    
    def add_api_key(self, key: str, client_id: str,
                   level: AuthLevel = AuthLevel.READ):
        """
        添加已有的 API Key
        
        Args:
            key: API Key
            client_id: 客户端 ID
            level: 权限级别
        """
        key_hash = self.hash_key(key)
        key_hint = key[:8] + "..." + key[-4:]
        
        api_key = APIKey(
            key=key_hash,
            key_hint=key_hint,
            client_id=client_id,
            auth_level=level,
        )
        
        self._api_keys[key_hash] = api_key
        logger.info(f"API Key 添加: {key_hint} -> {client_id}")
    
    def revoke_api_key(self, key: str) -> bool:
        """
        撤销 API Key
        
        Args:
            key: API Key
        
        Returns:
            是否成功
        """
        key_hash = self.hash_key(key)
        if key_hash in self._api_keys:
            del self._api_keys[key_hash]
            logger.info(f"API Key 已撤销: {key[:8]}...")
            return True
        return False
    
    def add_trusted_ip(self, ip: str):
        """添加信任的 IP"""
        self._trusted_ips.add(ip)
        logger.info(f"信任 IP 添加: {ip}")
    
    def remove_trusted_ip(self, ip: str) -> bool:
        """移除信任的 IP"""
        if ip in self._trusted_ips:
            self._trusted_ips.discard(ip)
            logger.info(f"信任 IP 移除: {ip}")
            return True
        return False
    
    async def authenticate(self, 
                          api_key: Optional[str] = None,
                          token: Optional[str] = None,
                          ip: Optional[str] = None) -> AuthResult:
        """
        认证请求
        
        优先级：
        1. API Key（推荐）
        2. Bearer Token
        3. 信任 IP（如果配置）
        
        Args:
            api_key: API Key
            token: Bearer Token
            ip: 客户端 IP
        
        Returns:
            认证结果
        """
        if not self._enabled:
            return AuthResult.success("anonymous", AuthLevel.ADMIN)
        
        # 1. 尝试 API Key 认证
        if api_key:
            return await self._authenticate_api_key(api_key)
        
        # 2. 尝试 Token 认证
        if token:
            return await self._authenticate_token(token)
        
        # 3. 检查信任 IP
        if ip and ip in self._trusted_ips:
            return AuthResult.success(f"trusted_ip:{ip}", AuthLevel.READ)
        
        return AuthResult.failure(
            "缺少认证信息",
            "MISSING_CREDENTIALS"
        )
    
    async def _authenticate_api_key(self, api_key: str) -> AuthResult:
        """验证 API Key"""
        key_hash = self.hash_key(api_key)
        
        async with self._lock:
            if key_hash not in self._api_keys:
                return AuthResult.failure(
                    "无效的 API Key",
                    "INVALID_KEY"
                )
            
            api_key_record = self._api_keys[key_hash]
            
            if not api_key_record.is_valid():
                if api_key_record.is_expired():
                    return AuthResult.failure(
                        "API Key 已过期",
                        "KEY_EXPIRED"
                    )
                return AuthResult.failure(
                    "API Key 已禁用",
                    "KEY_DISABLED"
                )
            
            # 更新使用记录
            api_key_record.last_used = datetime.now()
            api_key_record.request_count += 1
            
            return AuthResult.success(
                api_key_record.client_id,
                api_key_record.auth_level
            )
    
    async def _authenticate_token(self, token: str) -> AuthResult:
        """验证 Bearer Token"""
        async with self._lock:
            if token not in self._tokens:
                return AuthResult.failure(
                    "无效的 Token",
                    "INVALID_TOKEN"
                )
            
            client_id = self._tokens[token]
            return AuthResult.success(client_id, AuthLevel.READ)
    
    async def authorize(self, result: AuthResult, 
                       required_level: AuthLevel) -> bool:
        """
        检查权限
        
        Args:
            result: 认证结果
            required_level: 需要的权限级别
        
        Returns:
            是否有权限
        
        Raises:
            AuthorizationError: 权限不足时抛出
        """
        if not result.is_valid:
            raise AuthenticationError(result.error, result.error_code)
        
        level_order = {
            AuthLevel.NONE: 0,
            AuthLevel.READ: 1,
            AuthLevel.WRITE: 2,
            AuthLevel.ADMIN: 3,
        }
        
        if level_order.get(result.auth_level, 0) < level_order.get(required_level, 0):
            raise AuthorizationError(required_level, result.auth_level)
        
        return True
    
    def list_api_keys(self) -> List[dict]:
        """列出所有 API Key（不包含密钥）"""
        return [k.to_dict() for k in self._api_keys.values()]
    
    def get_stats(self) -> dict:
        """获取认证统计"""
        total_requests = sum(k.request_count for k in self._api_keys.values())
        return {
            "enabled": self._enabled,
            "total_keys": len(self._api_keys),
            "active_keys": sum(1 for k in self._api_keys.values() if k.is_valid()),
            "total_requests": total_requests,
            "trusted_ips": len(self._trusted_ips),
        }


# ─────────────────────────────────────────────
# 装饰器
# ─────────────────────────────────────────────

def require_auth(level: AuthLevel = AuthLevel.READ):
    """
    要求认证的装饰器
    
    用于 FastAPI 路由：
    
    Example:
        >>> @app.get("/protected")
        >>> @require_auth(AuthLevel.WRITE)
        >>> async def protected_endpoint():
        ...     return {"message": "protected"}
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 从 request 获取认证信息
            request = kwargs.get("request")
            if request is None:
                for arg in args:
                    if hasattr(arg, "headers"):
                        request = arg
                        break
            
            # TODO: 实现 FastAPI 依赖注入
            return await func(*args, **kwargs)
        return wrapper
    return decorator


# ─────────────────────────────────────────────
# 便捷函数和全局实例
# ─────────────────────────────────────────────

# 全局认证管理器
auth_manager = AuthManager()


async def authenticate(api_key: str = None, token: str = None,
                      ip: str = None) -> AuthResult:
    """
    快捷认证函数
    
    Example:
        >>> result = await authenticate(api_key="sk_xxx")
        >>> if not result.is_valid:
        ...     raise AuthenticationError(result.error)
    """
    return await auth_manager.authenticate(api_key, token, ip)


def create_api_key(client_id: str, level: AuthLevel = AuthLevel.WRITE) -> str:
    """
    快捷创建 API Key
    
    Example:
        >>> key = create_api_key("my_app", AuthLevel.WRITE)
    """
    return auth_manager.create_api_key(client_id, level)
