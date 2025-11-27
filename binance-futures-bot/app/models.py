"""
数据库模型定义
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class TradingPair(Base):
    """交易币种配置"""
    __tablename__ = "trading_pairs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    leverage: Mapped[int] = mapped_column(Integer, default=10)
    strategy_interval: Mapped[str] = mapped_column(String(10), default="15m")  # K线周期
    stop_loss_percent: Mapped[float] = mapped_column(Float, default=2.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_amplitude_disabled: Mapped[bool] = mapped_column(Boolean, default=False)  # 振幅禁用标记
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def to_dict(self):
        return {
            "id": self.id,
            "symbol": self.symbol,
            "leverage": self.leverage,
            "strategy_interval": self.strategy_interval,
            "stop_loss_percent": self.stop_loss_percent,
            "is_active": self.is_active,
            "is_amplitude_disabled": self.is_amplitude_disabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }


class SystemConfig(Base):
    """系统配置"""
    __tablename__ = "system_config"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Position(Base):
    """持仓记录"""
    __tablename__ = "positions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # LONG/SHORT
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_loss_price: Mapped[float] = mapped_column(Float, nullable=True)
    stop_loss_order_id: Mapped[str] = mapped_column(String(50), nullable=True)
    take_profit_price: Mapped[float] = mapped_column(Float, nullable=True)
    current_stop_level: Mapped[int] = mapped_column(Integer, default=0)  # 当前止损级别
    is_trailing_active: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")  # OPEN/CLOSED
    pnl: Mapped[float] = mapped_column(Float, nullable=True)
    pnl_percent: Mapped[float] = mapped_column(Float, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    close_reason: Mapped[str] = mapped_column(String(50), nullable=True)  # SIGNAL/STOP_LOSS/TRAILING_STOP
    
    def to_dict(self):
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "leverage": self.leverage,
            "stop_loss_price": self.stop_loss_price,
            "stop_loss_order_id": self.stop_loss_order_id,
            "current_stop_level": self.current_stop_level,
            "is_trailing_active": self.is_trailing_active,
            "status": self.status,
            "pnl": self.pnl,
            "pnl_percent": self.pnl_percent,
            "opened_at": self.opened_at.isoformat() if self.opened_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "close_reason": self.close_reason
        }


class TradeLog(Base):
    """交易日志"""
    __tablename__ = "trade_logs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # OPEN_LONG/OPEN_SHORT/CLOSE/STOP_LOSS_ADJUST
    price: Mapped[float] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=True)
    order_id: Mapped[str] = mapped_column(String(50), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=True)
    extra_data: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StopLossLog(Base):
    """止损调整记录 - 记录每次止损价格变动的详细信息"""
    __tablename__ = "stop_loss_logs"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False)  # LONG/SHORT
    
    # 价格信息
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)  # 入场价
    old_stop_price: Mapped[float] = mapped_column(Float, nullable=True)  # 原止损价
    new_stop_price: Mapped[float] = mapped_column(Float, nullable=False)  # 新止损价
    current_price: Mapped[float] = mapped_column(Float, nullable=True)  # 触发时的当前价
    
    # 盈利信息
    profit_percent: Mapped[float] = mapped_column(Float, nullable=True)  # 触发时的盈利百分比
    locked_profit_percent: Mapped[float] = mapped_column(Float, nullable=True)  # 锁定的利润百分比
    
    # 止损级别
    old_level: Mapped[int] = mapped_column(Integer, default=0)  # 原级别
    new_level: Mapped[int] = mapped_column(Integer, default=0)  # 新级别
    is_trailing: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否追踪止损
    
    # 调整原因
    adjust_reason: Mapped[str] = mapped_column(String(100), nullable=False)  # 调整原因简述
    adjust_detail: Mapped[str] = mapped_column(Text, nullable=True)  # 详细说明
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            "id": self.id,
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "old_stop_price": self.old_stop_price,
            "new_stop_price": self.new_stop_price,
            "current_price": self.current_price,
            "profit_percent": self.profit_percent,
            "locked_profit_percent": self.locked_profit_percent,
            "old_level": self.old_level,
            "new_level": self.new_level,
            "is_trailing": self.is_trailing,
            "adjust_reason": self.adjust_reason,
            "adjust_detail": self.adjust_detail,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class KlineCache(Base):
    """K线缓存"""
    __tablename__ = "kline_cache"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    interval: Mapped[str] = mapped_column(String(10), nullable=False)
    open_time: Mapped[int] = mapped_column(Integer, nullable=False)
    open_price: Mapped[float] = mapped_column(Float, nullable=False)
    high_price: Mapped[float] = mapped_column(Float, nullable=False)
    low_price: Mapped[float] = mapped_column(Float, nullable=False)
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    close_time: Mapped[int] = mapped_column(Integer, nullable=False)
    
    __table_args__ = (
        # 复合索引
        {"sqlite_autoincrement": True},
    )
