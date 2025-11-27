"""
配置管理模块
支持环境变量和数据库配置的动态加载
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """应用配置"""
    # 应用设置
    APP_NAME: str = "Binance Futures Bot"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # 数据库
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/bot.db"
    
    # 币安API配置（可通过Web界面动态配置）
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""
    BINANCE_TESTNET: bool = False
    
    # Telegram配置
    TG_BOT_TOKEN: str = ""
    TG_CHAT_ID: str = ""
    TG_API_ID: int = 0
    TG_API_HASH: str = ""
    TG_CHANNEL: str = "https://t.me/BWE_OI_Price_monitor"
    
    # 交易默认参数
    DEFAULT_LEVERAGE: int = 10
    DEFAULT_STRATEGY_INTERVAL: str = "15m"
    DEFAULT_STOP_LOSS_PERCENT: float = 2.0
    POSITION_SIZE_PERCENT: float = 10.0  # 账户余额的10%
    
    # 振幅过滤
    AMPLITUDE_CHECK_KLINES: int = 200
    MIN_AMPLITUDE_PERCENT: float = 7.0
    
    # WebSocket自愈
    WS_HEALTH_CHECK_INTERVAL: int = 60  # 秒
    WS_NO_DATA_TIMEOUT: int = 300  # 5分钟
    WS_FULL_RESTART_HOURS: int = 20  # 每20小时全量重启
    
    # 移动止损参数
    TRAILING_STOP_LEVELS: dict = Field(default_factory=lambda: {
        "level_1": {"profit_min": 2.5, "profit_max": 5.0, "stop_at_cost": True},
        "level_2": {"profit_min": 5.0, "profit_max": 10.0, "lock_profit": 3.0},
        "level_3": {"profit_min": 10.0, "lock_profit": 5.0, "trailing_percent": 3.0}
    })
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# 全局配置实例
settings = Settings()


class ConfigManager:
    """配置管理器 - 用于动态更新配置"""
    _instance: Optional["ConfigManager"] = None
    _observers: list = []
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._observers = []
        return cls._instance
    
    def add_observer(self, callback):
        """添加配置变更观察者"""
        if callback not in self._observers:
            self._observers.append(callback)
    
    def remove_observer(self, callback):
        """移除观察者"""
        if callback in self._observers:
            self._observers.remove(callback)
    
    async def notify_observers(self, change_type: str, data: dict):
        """通知所有观察者配置已变更"""
        for observer in self._observers:
            try:
                if asyncio.iscoroutinefunction(observer):
                    await observer(change_type, data)
                else:
                    observer(change_type, data)
            except Exception as e:
                print(f"Observer notification error: {e}")
    
    def update_binance_config(self, api_key: str, api_secret: str, testnet: bool = False):
        """更新币安API配置"""
        settings.BINANCE_API_KEY = api_key
        settings.BINANCE_API_SECRET = api_secret
        settings.BINANCE_TESTNET = testnet
    
    def update_telegram_config(self, bot_token: str, chat_id: str, 
                                api_id: int = 0, api_hash: str = ""):
        """更新Telegram配置"""
        settings.TG_BOT_TOKEN = bot_token
        settings.TG_CHAT_ID = chat_id
        settings.TG_API_ID = api_id
        settings.TG_API_HASH = api_hash


import asyncio
config_manager = ConfigManager()
