"""
环境扫描器 - A2A Server 启动前检测当前 Agent 环境
"""
import os
import json
import socket
from pathlib import Path
from typing import Optional, Dict, Any


class EnvScanner:
    """扫描并识别当前 Agent 环境"""
    
    def __init__(self):
        self.workspace = Path(os.environ.get("OPENCLAW_WORKSPACE", "/root/.openclaw/workspace"))
        self.results: Dict[str, Any] = {}
    
    def scan_all(self) -> Dict[str, Any]:
        """执行完整扫描"""
        # 先收集所有数据
        hostname = self._get_hostname()
        identity = self._read_identity()
        memory = self._read_memory()
        config = self._read_config()
        gateway = self._detect_gateway()
        running_agents = self._detect_running_agents()
        ports = self._scan_ports()
        
        # 再组装 results（避免循环依赖）
        self.results = {
            "hostname": hostname,
            "identity": identity,
            "memory": memory,
            "config": config,
            "gateway": gateway,
            "running_agents": running_agents,
            "ports": ports,
        }
        # 最后确定 agent_mode（需要 results 已存在）
        self.results["agent_mode"] = self._determine_agent_mode(identity)
        return self.results
    
    def _get_hostname(self) -> str:
        """获取主机名"""
        return socket.gethostname()
    
    def _read_identity(self) -> Dict[str, str]:
        """读取 IDENTITY.md"""
        identity_file = self.workspace / "IDENTITY.md"
        if identity_file.exists():
            content = identity_file.read_text()
            # 解析 **Name:** 格式
            name = self._extract_bold_field(content, "Name:")
            role = self._extract_section(content, "定位")
            superior = self._extract_section(content, "上级")
            return {
                "name": name or "未知",
                "role": role or "未知",
                "superior": superior or "未知",
            }
        return {"name": "未找到 IDENTITY.md"}
    
    def _extract_bold_field(self, content: str, field: str) -> Optional[str]:
        """提取 **field:** 格式的值"""
        import re
        pattern = rf"\*\*{field}\*\*\s*(.+?)(?:\n|$)"
        match = re.search(pattern, content)
        return match.group(1).strip() if match else None
    
    def _extract_section(self, content: str, section: str) -> Optional[str]:
        """提取 Markdown 章节内容（## 标题 之后的段落）"""
        import re
        # 找到 ## section_name 然后取下一段（到下一个 ## 或文件结尾）
        pattern = rf"##\s+{re.escape(section)}[^\n]*\n(.+?)(?=\n##\s|\Z)"
        match = re.search(pattern, content, re.DOTALL)
        if match:
            text = match.group(1).strip()
            # 去掉列表标记
            text = re.sub(r"^[*-]\s*", "", text, flags=re.MULTILINE)
            # 去掉 **text:** 格式
            text = re.sub(r"\*\*[^:]+:\*\*", "", text)
            return text.strip()
        return None
    
    def _extract_field(self, content: str, field: str) -> Optional[str]:
        """提取字段值"""
        for line in content.split("\n"):
            if line.startswith(field):
                return line.split(":", 1)[1].strip()
        return None
    
    def _read_memory(self) -> Dict[str, Any]:
        """读取 MEMORY.md"""
        memory_file = self.workspace / "MEMORY.md"
        if memory_file.exists():
            content = memory_file.read_text()
            # 提取关键信息
            last_update = None
            for line in content.split("\n"):
                if line.startswith("> 上次更新"):
                    last_update = line.split("：", 1)[1].split(">")[0].strip() if "：" in line else None
            return {
                "last_update": last_update,
                "size_bytes": memory_file.stat().st_size,
            }
        return {"status": "未找到 MEMORY.md"}
    
    def _read_config(self) -> Dict[str, Any]:
        """读取配置文件"""
        config_file = Path("/root/.openclaw/openclaw.json")
        if config_file.exists():
            try:
                with open(config_file) as f:
                    config = json.load(f)
                # 只返回关键配置
                gateway = config.get("gateway", {})
                return {
                    "gateway_url": gateway.get("url", "未配置"),
                    "gateway_port": gateway.get("port", "未配置"),
                }
            except Exception as e:
                return {"error": str(e)}
        return {"status": "未找到配置文件"}
    
    def _detect_gateway(self) -> Dict[str, Any]:
        """检测 Gateway 状态"""
        import subprocess
        result = {
            "detected": False,
            "port": 18789,
            "status": "unknown",
        }
        
        # 尝试连接 Gateway
        try:
            import httpx
            response = httpx.get("http://127.0.0.1:18789/health", timeout=2)
            if response.status_code == 200:
                data = response.json()
                result["detected"] = True
                result["status"] = data.get("status", "unknown")
                result["message"] = data.get("message", "")
        except:
            result["status"] = "offline"
        
        return result
    
    def _detect_running_agents(self) -> Dict[str, bool]:
        """检测运行中的 Agent"""
        agents = {
            "writer": {"port": 13666, "running": False},
            "gm": {"port": 13666, "running": False},  # GM 也用 13666
        }
        
        # 检查端口
        for name, info in agents.items():
            if self._is_port_open("127.0.0.1", info["port"]):
                info["running"] = True
        
        return agents
    
    def _is_port_open(self, host: str, port: int) -> bool:
        """检查端口是否开放"""
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        try:
            result = sock.connect_ex((host, port))
            return result == 0
        except:
            return False
        finally:
            sock.close()
    
    def _scan_ports(self) -> Dict[str, int]:
        """扫描常用端口"""
        ports = {
            "gateway": 18789,
            "a2a_server": 13666,
        }
        
        open_ports = {}
        for name, port in ports.items():
            if self._is_port_open("127.0.0.1", port):
                open_ports[name] = port
        
        return open_ports
    
    def _determine_agent_mode(self, identity: Dict[str, str] = None) -> Dict[str, Any]:
        """确定 Agent 运行模式"""
        if identity is None:
            identity = self.results.get("identity", {})
        name = identity.get("name", "").lower()
        
        if "writer" in name:
            return {
                "mode": "writer",
                "description": "作家 Agent - 首席写作专家",
                "color": "🖊",
            }
        elif "gm" in name or "manager" in name:
            return {
                "mode": "gm",
                "description": "GM Agent - 总管理器",
                "color": "👑",
            }
        else:
            return {
                "mode": "unknown",
                "description": "未识别的 Agent 模式",
                "color": "❓",
            }
    
    def print_report(self):
        """打印环境扫描报告"""
        r = self.results
        
        print("\n" + "=" * 50)
        print("🔍 A2A Server 环境扫描报告")
        print("=" * 50)
        
        # 基础信息
        print(f"\n📍 主机: {r.get('hostname', 'unknown')}")
        
        # Agent 身份
        identity = r.get("identity", {})
        agent_mode = r.get("agent_mode", {})
        print(f"\n👤 Agent 身份:")
        print(f"   名称: {identity.get('name', '未知')}")
        print(f"   定位: {identity.get('role', '未知')}")
        print(f"   上级: {identity.get('superior', '未知')}")
        print(f"   模式: {agent_mode.get('color')} {agent_mode.get('description', '未知')}")
        
        # Gateway 状态
        gateway = r.get("gateway", {})
        status_icon = "✅" if gateway.get("detected") else "❌"
        print(f"\n🌐 Gateway 状态: {status_icon}")
        print(f"   检测: {'在线' if gateway.get('detected') else '离线'}")
        print(f"   端口: {gateway.get('port', 'N/A')}")
        
        # 开放端口
        ports = r.get("ports", {})
        print(f"\n🔌 开放端口:")
        if ports:
            for name, port in ports.items():
                print(f"   {name}: {port}")
        else:
            print("   无")
        
        # 配置
        config = r.get("config", {})
        print(f"\n⚙️ 配置:")
        print(f"   Gateway URL: {config.get('gateway_url', '未知')}")
        
        # 记忆
        memory = r.get("memory", {})
        print(f"\n🧠 记忆:")
        print(f"   最后更新: {memory.get('last_update', '未知')}")
        print(f"   文件大小: {memory.get('size_bytes', 0)} bytes")
        
        print("\n" + "=" * 50)


def main():
    scanner = EnvScanner()
    results = scanner.scan_all()
    scanner.print_report()
    return results


if __name__ == "__main__":
    main()
