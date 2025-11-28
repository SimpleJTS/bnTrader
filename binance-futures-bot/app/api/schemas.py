"""
API请求/响应模型定义
"""
from typing import Optional, List
from pydantic import BaseModel, Field
from datetime import datetime


# ========== Trading Pair Schemas ==========

class TradingPairBase(BaseModel):
    """交易对基础模型"""
    symbol: str = Field(..., description="交易对名称，如BTCUSDT")
    leverage: int = Field(default=10, ge=1, le=125, description="杠杆倍数")
    strategy_interval: str = Field(default="15m", description="K线周期")
    stop_loss_percent: float = Field(default=2.0, ge=0.1, le=50, description="止损百分比")
    is_active: bool = Field(default=True, description="是否启用交易")


class TradingPairCreate(TradingPairBase):
    """创建交易对"""
    pass


class TradingPairUpdate(BaseModel):
    """更新交易对"""
    leverage: Optional[int] = Field(None, ge=1, le=125)
    strategy_interval: Optional[str] = None
    stop_loss_percent: Optional[float] = Field(None, ge=0.1, le=50)
    is_active: Optional[bool] = None


class TradingPairResponse(TradingPairBase):
    """交易对响应"""
    id: int
    is_amplitude_disabled: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True


# ========== System Config Schemas ==========

class BinanceConfigUpdate(BaseModel):
    """币安API配置"""
    api_key: str = Field(..., description="API Key")
    api_secret: str = Field(..., description="API Secret")
    testnet: bool = Field(default=False, description="是否使用测试网")


class TelegramConfigUpdate(BaseModel):
    """Telegram配置"""
    bot_token: str = Field(..., description="Bot Token")
    chat_id: str = Field(..., description="Chat ID")
    api_id: Optional[int] = Field(None, description="API ID (用于频道监听)")
    api_hash: Optional[str] = Field(None, description="API Hash (用于频道监听)")


class SystemConfigResponse(BaseModel):
    """系统配置响应"""
    binance_configured: bool
    binance_testnet: bool
    telegram_configured: bool
    channel_listener_configured: bool
    encryption_enabled: bool = True  # 是否启用加密存储


# ========== Position Schemas ==========

class PositionResponse(BaseModel):
    """仓位响应"""
    id: int
    symbol: str
    side: str
    entry_price: float
    quantity: float
    leverage: int
    stop_loss_price: Optional[float]
    current_stop_level: int
    is_trailing_active: bool
    status: str
    pnl: Optional[float]
    pnl_percent: Optional[float]
    opened_at: Optional[datetime]
    closed_at: Optional[datetime]
    close_reason: Optional[str]
    
    class Config:
        from_attributes = True


# ========== WebSocket Status ==========

class WebSocketStatus(BaseModel):
    """WebSocket状态"""
    connected: bool
    subscriptions: List[str]
    reconnect_count: int
    start_time: Optional[str]


# ========== Trade Log ==========

class TradeLogResponse(BaseModel):
    """交易日志响应"""
    id: int
    symbol: str
    action: str
    price: Optional[float]
    quantity: Optional[float]
    order_id: Optional[str]
    message: Optional[str]
    created_at: Optional[datetime]
    
    class Config:
        from_attributes = True


# ========== Stop Loss Log ==========

class StopLossLogResponse(BaseModel):
    """止损调整记录响应"""
    id: int
    symbol: str
    side: str
    entry_price: float
    old_stop_price: Optional[float]
    new_stop_price: float
    current_price: Optional[float]
    profit_percent: Optional[float]
    locked_profit_percent: Optional[float]
    old_level: int
    new_level: int
    is_trailing: bool
    adjust_reason: str
    adjust_detail: Optional[str]
    created_at: Optional[datetime]
    
    class Config:
        from_attributes = True


# ========== General Response ==========

class MessageResponse(BaseModel):
    """通用消息响应"""
    success: bool
    message: str


class ErrorResponse(BaseModel):
    """错误响应"""
    error: str
    detail: Optional[str] = None


# ========== Trailing Stop Config ==========

class TrailingStopLevel(BaseModel):
    """单个止损级别配置"""
    profit_min: float = Field(..., ge=0, description="触发该级别的最小盈利百分比")
    profit_max: Optional[float] = Field(None, ge=0, description="该级别的最大盈利百分比（下一级别开始）")
    lock_profit: float = Field(default=0, ge=0, description="锁定的利润百分比（0表示止损提到成本价）")
    trailing_enabled: bool = Field(default=False, description="是否启用追踪止损")
    trailing_percent: float = Field(default=3.0, ge=0.1, le=50, description="追踪止损回撤百分比")


class TrailingStopConfig(BaseModel):
    """移动止损配置"""
    level_1: TrailingStopLevel = Field(
        default_factory=lambda: TrailingStopLevel(
            profit_min=2.5, profit_max=5.0, lock_profit=0, 
            trailing_enabled=False, trailing_percent=3.0
        ),
        description="级别1：保本止损"
    )
    level_2: TrailingStopLevel = Field(
        default_factory=lambda: TrailingStopLevel(
            profit_min=5.0, profit_max=10.0, lock_profit=3.0,
            trailing_enabled=False, trailing_percent=3.0
        ),
        description="级别2：锁定利润"
    )
    level_3: TrailingStopLevel = Field(
        default_factory=lambda: TrailingStopLevel(
            profit_min=10.0, profit_max=None, lock_profit=5.0,
            trailing_enabled=True, trailing_percent=3.0
        ),
        description="级别3：追踪止损"
    )


class TrailingStopConfigUpdate(BaseModel):
    """更新移动止损配置"""
    level_1: Optional[TrailingStopLevel] = None
    level_2: Optional[TrailingStopLevel] = None
    level_3: Optional[TrailingStopLevel] = None


# ========== TG Monitor Config ==========

class TGMonitorConfig(BaseModel):
    """TG频道监控配置"""
    min_price_change_percent: float = Field(
        default=30.0, 
        ge=1.0, 
        le=100.0, 
        description="24H价格变化阈值（绝对值），超过此阈值自动添加交易对"
    )
    is_running: bool = Field(default=False, description="监控是否运行中")


class TGMonitorConfigUpdate(BaseModel):
    """更新TG频道监控配置"""
    min_price_change_percent: float = Field(
        ..., 
        ge=1.0, 
        le=100.0, 
        description="24H价格变化阈值（绝对值）"
    )
