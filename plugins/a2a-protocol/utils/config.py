"""
配置模块
"""
import os


class Config:
    """配置"""
    # 服务器
    HOST = os.environ.get("A2A_HOST", "0.0.0.0")
    PORT = int(os.environ.get("A2A_PORT", "13666"))
    
    # Gateway
    GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:3000")
    GATEWAY_TOKEN = os.environ.get("GATEWAY_TOKEN", "")
    
    # 日志
    LOG_LEVEL = os.environ.get("A2A_LOG_LEVEL", "INFO")
    
    # 超时
    REQUEST_TIMEOUT = int(os.environ.get("A2A_REQUEST_TIMEOUT", "60"))
    SSE_TIMEOUT = int(os.environ.get("A2A_SSE_TIMEOUT", "300"))
    
    @classmethod
    def load_gateway_token(cls):
        """加载 Gateway token"""
        try:
            with open("/root/.openclaw/openclaw.json") as f:
                import json
                cls.GATEWAY_TOKEN = json.load(f).get("gateway", {}).get("auth", {}).get("token", "")
        except:
            pass


config = Config()
