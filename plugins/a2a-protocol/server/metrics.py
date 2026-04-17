"""
Prometheus 监控指标
A2A 协议扩展 - 指标采集

概述:
    采集和暴露 A2A Server 的运行指标：
    1. 请求计数
    2. 请求延迟
    3. 任务状态分布
    4. Gateway 健康状态
    5. 会话数量
    6. 连接数（SSE/WebSocket）

暴露方式:
    - /metrics 端点（Prometheus 抓取格式）
    - FastAPI 中间件自动采集

示例:
    >>> metrics = MetricsCollector()
    >>> 
    >>> # 记录请求
    >>> metrics.record_request("/rpc", 200, 0.05)
    >>> 
    >>> # 获取指标
    >>> metrics_text = await metrics.get_metrics()

Prometheus 配置:
    scrape_configs:
      - job_name: 'a2a-server'
        static_configs:
          - targets: ['localhost:13666']
        metrics_path: '/metrics'
"""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Callable
import threading

logger = logging.getLogger("a2a")


class MetricType(Enum):
    """指标类型"""
    COUNTER = "counter"      # 计数器
    GAUGE = "gauge"          # 仪表
    HISTOGRAM = "histogram"  # 直方图
    SUMMARY = "summary"      # 汇总


@dataclass
class MetricValue:
    """指标值"""
    name: str
    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: Optional[float] = None


class Counter:
    """计数器"""
    
    def __init__(self, name: str, description: str = "", labels: List[str] = None):
        self.name = name
        self.description = description
        self.labels = labels or []
        self._values: Dict[tuple, float] = defaultdict(float)
        self._lock = asyncio.Lock()
    
    def inc(self, value: float = 1, **label_values):
        """增加计数"""
        key = self._make_key(label_values)
        self._values[key] += value
    
    async def async_inc(self, value: float = 1, **label_values):
        """异步增加计数"""
        async with self._lock:
            self.inc(value, **label_values)
    
    def _make_key(self, label_values: Dict[str, str]) -> tuple:
        """生成 key"""
        return tuple(label_values.get(l, "") for l in self.labels)
    
    def get_value(self, **label_values) -> float:
        """获取值"""
        key = self._make_key(label_values)
        return self._values.get(key, 0)
    
    def collect(self) -> List[MetricValue]:
        """收集指标"""
        return [
            MetricValue(name=self.name, value=v, labels=dict(zip(self.labels, k)))
            for k, v in self._values.items()
            if v > 0
        ]


class Gauge:
    """仪表"""
    
    def __init__(self, name: str, description: str = "", labels: List[str] = None):
        self.name = name
        self.description = description
        self.labels = labels or []
        self._values: Dict[tuple, float] = defaultdict(float)
        self._lock = asyncio.Lock()
    
    def set(self, value: float, **label_values):
        """设置值"""
        key = self._make_key(label_values)
        self._values[key] = value
    
    async def async_set(self, value: float, **label_values):
        """异步设置值"""
        async with self._lock:
            self.set(value, **label_values)
    
    def inc(self, value: float = 1, **label_values):
        """增加"""
        key = self._make_key(label_values)
        self._values[key] += value
    
    def dec(self, value: float = 1, **label_values):
        """减少"""
        key = self._make_key(label_values)
        self._values[key] -= value
    
    def _make_key(self, label_values: Dict[str, str]) -> tuple:
        return tuple(label_values.get(l, "") for l in self.labels)
    
    def get_value(self, **label_values) -> float:
        key = self._make_key(label_values)
        return self._values.get(key, 0)
    
    def collect(self) -> List[MetricValue]:
        return [
            MetricValue(name=self.name, value=v, labels=dict(zip(self.labels, k)))
            for k, v in self._values.items()
        ]


class Histogram:
    """直方图"""
    
    BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)
    
    def __init__(self, name: str, description: str = "", labels: List[str] = None):
        self.name = name
        self.description = description
        self.labels = labels or []
        self._count: Dict[tuple, int] = defaultdict(int)
        self._sum: Dict[tuple, float] = defaultdict(float)
        self._buckets: Dict[tuple, Dict[float, int]] = defaultdict(
            lambda: {b: 0 for b in self.BUCKETS}
        )
        self._lock = asyncio.Lock()
    
    def observe(self, value: float, **label_values):
        """观察值"""
        key = self._make_key(label_values)
        self._count[key] += 1
        self._sum[key] += value
        
        for bucket in self.BUCKETS:
            if value <= bucket:
                self._buckets[key][bucket] += 1
    
    async def async_observe(self, value: float, **label_values):
        async with self._lock:
            self.observe(value, **label_values)
    
    def _make_key(self, label_values: Dict[str, str]) -> tuple:
        return tuple(label_values.get(l, "") for l in self.labels)
    
    def collect(self) -> List[MetricValue]:
        results = []
        for key in self._count.keys():
            labels = dict(zip(self.labels, key))
            
            # 输出每个 bucket
            cumulative = 0
            for bucket in self.BUCKETS:
                cumulative += self._buckets[key][bucket]
                results.append(MetricValue(
                    name=f"{self.name}_bucket",
                    value=float(cumulative),
                    labels={**labels, "le": str(bucket)}
                ))
            
            # +Inf bucket
            results.append(MetricValue(
                name=f"{self.name}_bucket",
                value=float(self._count[key]),
                labels={**labels, "le": "+Inf"}
            ))
            
            # sum 和 count
            results.append(MetricValue(
                name=f"{self.name}_sum",
                value=self._sum[key],
                labels=labels
            ))
            results.append(MetricValue(
                name=f"{self.name}_count",
                value=float(self._count[key]),
                labels=labels
            ))
        
        return results


class MetricsCollector:
    """
    指标采集器
    
    收集并暴露 A2A Server 的运行指标。
    
    Attributes:
        _counters: 计数器字典
        _gauges: 仪表字典
        _histograms: 直方图字典
        _start_time: 启动时间
    
    Example:
        >>> collector = MetricsCollector()
        >>> 
        >>> # 注册指标
        >>> collector.register_counter("requests_total", "Total requests")
        >>> 
        >>> # 记录请求
        >>> collector.record_request("/rpc", 200, 0.05)
        >>> 
        >>> # 获取 Prometheus 格式
        >>> metrics_text = await collector.get_metrics()
    """
    
    def __init__(self):
        self._counters: Dict[str, Counter] = {}
        self._gauges: Dict[str, Gauge] = {}
        self._histograms: Dict[str, Histogram] = {}
        self._start_time = time.time()
        self._lock = asyncio.Lock()
        
        # 注册默认指标
        self._register_default_metrics()
    
    def _register_default_metrics(self):
        """注册默认指标"""
        # 请求计数
        self.register_counter(
            "a2a_requests_total",
            "Total A2A requests",
            ["endpoint", "method", "status"]
        )
        
        # 请求延迟
        self.register_histogram(
            "a2a_request_duration_seconds",
            "Request duration in seconds",
            ["endpoint", "method"]
        )
        
        # 任务计数
        self.register_counter(
            "a2a_tasks_total",
            "Total tasks",
            ["state"]
        )
        
        # 活跃任务
        self.register_gauge(
            "a2a_tasks_active",
            "Active tasks",
            []
        )
        
        # 会话数量
        self.register_gauge(
            "a2a_sessions_active",
            "Active sessions",
            []
        )
        
        # SSE 连接数
        self.register_gauge(
            "a2a_sse_connections",
            "SSE connections",
            []
        )
        
        # WebSocket 连接数
        self.register_gauge(
            "a2a_ws_connections",
            "WebSocket connections",
            []
        )
        
        # Gateway 健康状态
        self.register_gauge(
            "a2a_gateway_up",
            "Gateway health (1=up, 0=down)",
            []
        )
        
        # Gateway 请求延迟
        self.register_histogram(
            "a2a_gateway_request_duration_seconds",
            "Gateway request duration",
            ["type"]
        )
        
        # 认证失败
        self.register_counter(
            "a2a_auth_failures_total",
            "Authentication failures",
            ["type"]
        )
        
        # 限流触发
        self.register_counter(
            "a2a_ratelimit_triggers_total",
            "Rate limit triggers",
            ["type"]
        )
    
    def register_counter(self, name: str, description: str = "", 
                        labels: List[str] = None):
        """注册计数器"""
        self._counters[name] = Counter(name, description, labels)
        logger.debug(f"Counter registered: {name}")
    
    def register_gauge(self, name: str, description: str = "",
                      labels: List[str] = None):
        """注册仪表"""
        self._gauges[name] = Gauge(name, description, labels)
        logger.debug(f"Gauge registered: {name}")
    
    def register_histogram(self, name: str, description: str = "",
                          labels: List[str] = None):
        """注册直方图"""
        self._histograms[name] = Histogram(name, description, labels)
        logger.debug(f"Histogram registered: {name}")
    
    async def record_request(self, endpoint: str, method: str, 
                            status: int, duration: float):
        """
        记录请求
        
        Args:
            endpoint: 端点路径
            method: HTTP 方法
            status: 状态码
            duration: 请求耗时（秒）
        """
        async with self._lock:
            # 请求计数
            if "a2a_requests_total" in self._counters:
                self._counters["a2a_requests_total"].inc(
                    1, endpoint=endpoint, method=method, status=str(status)
                )
            
            # 请求延迟
            if "a2a_request_duration_seconds" in self._histograms:
                self._histograms["a2a_request_duration_seconds"].observe(
                    duration, endpoint=endpoint, method=method
                )
    
    def record_task(self, state: str):
        """记录任务"""
        if "a2a_tasks_total" in self._counters:
            self._counters["a2a_tasks_total"].inc(1, state=state)
    
    def set_active_tasks(self, count: int):
        """设置活跃任务数"""
        if "a2a_tasks_active" in self._gauges:
            self._gauges["a2a_tasks_active"].set(count)
    
    def set_active_sessions(self, count: int):
        """设置活跃会话数"""
        if "a2a_sessions_active" in self._gauges:
            self._gauges["a2a_sessions_active"].set(count)
    
    def set_sse_connections(self, count: int):
        """设置 SSE 连接数"""
        if "a2a_sse_connections" in self._gauges:
            self._gauges["a2a_sse_connections"].set(count)
    
    def set_ws_connections(self, count: int):
        """设置 WebSocket 连接数"""
        if "a2a_ws_connections" in self._gauges:
            self._gauges["a2a_ws_connections"].set(count)
    
    def set_gateway_health(self, is_up: bool):
        """设置 Gateway 健康状态"""
        if "a2a_gateway_up" in self._gauges:
            self._gauges["a2a_gateway_up"].set(1 if is_up else 0)
    
    def record_gateway_request(self, duration: float, is_stream: bool = False):
        """记录 Gateway 请求"""
        if "a2a_gateway_request_duration_seconds" in self._histograms:
            req_type = "stream" if is_stream else "sync"
            self._histograms["a2a_gateway_request_duration_seconds"].observe(
                duration, type=req_type
            )
    
    def record_auth_failure(self, auth_type: str):
        """记录认证失败"""
        if "a2a_auth_failures_total" in self._counters:
            self._counters["a2a_auth_failures_total"].inc(1, type=auth_type)
    
    def record_ratelimit_trigger(self, limit_type: str):
        """记录限流触发"""
        if "a2a_ratelimit_triggers_total" in self._counters:
            self._counters["a2a_ratelimit_triggers_total"].inc(1, type=limit_type)
    
    async def get_metrics(self) -> str:
        """
        获取 Prometheus 格式的指标
        
        Returns:
            Prometheus 文本格式的指标
        """
        lines = []
        lines.append("# HELP a2a_server_info A2A Server info")
        lines.append("# TYPE a2a_server_info gauge")
        lines.append('a2a_server_info{version="1.0.0"} 1')
        
        # uptime
        uptime = time.time() - self._start_time
        lines.append(f"# HELP a2a_uptime_seconds A2A Server uptime")
        lines.append(f"# TYPE a2a_uptime_seconds gauge")
        lines.append(f"a2a_uptime_seconds {uptime}")
        
        async with self._lock:
            # counters
            for counter in self._counters.values():
                for mv in counter.collect():
                    labels = ",".join(
                        f'{k}="{v}"' for k, v in mv.labels.items()
                    )
                    if labels:
                        lines.append(f"{mv.name}{{{labels}}} {mv.value}")
                    else:
                        lines.append(f"{mv.name} {mv.value}")
            
            # gauges
            for gauge in self._gauges.values():
                for mv in gauge.collect():
                    labels = ",".join(
                        f'{k}="{v}"' for k, v in mv.labels.items()
                    )
                    if labels:
                        lines.append(f"{mv.name}{{{labels}}} {mv.value}")
                    else:
                        lines.append(f"{mv.name} {mv.value}")
            
            # histograms
            for histogram in self._histograms.values():
                for mv in histogram.collect():
                    labels = ",".join(
                        f'{k}="{v}"' for k, v in mv.labels.items()
                    )
                    if labels:
                        lines.append(f"{mv.name}{{{labels}}} {mv.value}")
                    else:
                        lines.append(f"{mv.name} {mv.value}")
        
        return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────
# FastAPI 中间件
# ─────────────────────────────────────────────

async def metrics_middleware(request, call_next):
    """指标收集中间件"""
    start_time = time.time()
    
    response = await call_next(request)
    
    duration = time.time() - start_time
    endpoint = request.url.path
    method = request.method
    status = response.status_code
    
    await metrics.record_request(endpoint, method, status, duration)
    
    return response


# ─────────────────────────────────────────────
# 全局实例
# ─────────────────────────────────────────────

metrics = MetricsCollector()


async def get_metrics() -> str:
    """获取指标"""
    return await metrics.get_metrics()
