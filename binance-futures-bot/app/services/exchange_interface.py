"""
交易所抽象接口层
定义统一的交易所API和WebSocket接口
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Callable, Any
from dataclasses import dataclass
from enum import Enum


class ExchangeType(Enum):
    """支持的交易所类型"""
    BINANCE = "binance"
    HYPERLIQUID = "hyperliquid"


@dataclass
class KlineData:
    """K线数据结构（统一格式）"""
    symbol: str
    interval: str
    open_time: int
    close_time: int
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    is_closed: bool
    
    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "open_time": self.open_time,
            "close_time": self.close_time,
            "open": self.open_price,
            "high": self.high_price,
            "low": self.low_price,
            "close": self.close_price,
            "volume": self.volume,
            "is_closed": self.is_closed
        }


@dataclass
class SymbolPrecision:
    """交易对精度信息"""
    price_precision: int
    quantity_precision: int
    tick_size: str
    step_size: str
    min_qty: str
    min_notional: str


@dataclass
class AccountBalance:
    """账户余额信息"""
    asset: str
    balance: float
    available: float
    unrealized_pnl: float = 0.0


@dataclass
class PositionInfo:
    """持仓信息"""
    symbol: str
    side: str  # LONG/SHORT
    entry_price: float
    quantity: float
    leverage: int
    unrealized_pnl: float
    liquidation_price: float = 0.0


@dataclass
class OrderResult:
    """订单结果"""
    order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    price: float
    status: str
    executed_qty: float = 0.0
    avg_price: float = 0.0
    raw_data: dict = None


class ExchangeAPI(ABC):
    """交易所API抽象接口"""
    
    @property
    @abstractmethod
    def exchange_type(self) -> ExchangeType:
        """返回交易所类型"""
        pass
    
    @abstractmethod
    async def initialize(self) -> bool:
        """初始化API连接"""
        pass
    
    @abstractmethod
    async def close(self):
        """关闭API连接"""
        pass
    
    # ========== 市场数据 ==========
    
    @abstractmethod
    async def get_exchange_info(self) -> dict:
        """获取交易所信息"""
        pass
    
    @abstractmethod
    async def get_symbol_precision(self, symbol: str) -> SymbolPrecision:
        """获取交易对精度信息"""
        pass
    
    @abstractmethod
    async def get_current_price(self, symbol: str) -> float:
        """获取当前价格"""
        pass
    
    @abstractmethod
    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> List[list]:
        """获取K线数据
        
        Returns:
            K线列表，每个元素格式: [open_time, open, high, low, close, volume, close_time, ...]
        """
        pass
    
    @abstractmethod
    async def get_24hr_ticker(self, symbol: str = None) -> List[dict]:
        """获取24小时价格变化统计"""
        pass
    
    # ========== 账户数据 ==========
    
    @abstractmethod
    async def get_account_balance(self) -> Dict[str, AccountBalance]:
        """获取账户余额"""
        pass
    
    @abstractmethod
    async def get_usdt_balance(self) -> float:
        """获取USDT可用余额"""
        pass
    
    @abstractmethod
    async def get_position(self, symbol: str = None) -> List[PositionInfo]:
        """获取持仓信息"""
        pass
    
    # ========== 交易设置 ==========
    
    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        """设置杠杆"""
        pass
    
    @abstractmethod
    async def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> dict:
        """设置保证金模式 (CROSSED/ISOLATED)"""
        pass
    
    # ========== 下单 ==========
    
    @abstractmethod
    async def place_market_order(self, symbol: str, side: str, quantity: float,
                                  reduce_only: bool = False) -> OrderResult:
        """下市价单
        
        Args:
            symbol: 交易对
            side: BUY/SELL
            quantity: 数量
            reduce_only: 是否仅减仓
        """
        pass
    
    @abstractmethod
    async def place_stop_loss_order(self, symbol: str, side: str, quantity: float,
                                     stop_price: float, close_position: bool = False) -> OrderResult:
        """下止损单
        
        Args:
            symbol: 交易对
            side: BUY(空头止损)/SELL(多头止损)
            quantity: 数量
            stop_price: 触发价格
            close_position: 是否平全部仓位
        """
        pass
    
    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        """取消订单"""
        pass
    
    @abstractmethod
    async def cancel_all_orders(self, symbol: str) -> dict:
        """取消某个交易对的所有订单"""
        pass
    
    @abstractmethod
    async def get_open_orders(self, symbol: str = None) -> List[dict]:
        """获取当前挂单"""
        pass
    
    # ========== 工具方法 ==========
    
    @abstractmethod
    def format_symbol(self, symbol: str) -> str:
        """格式化交易对名称（各交易所格式可能不同）"""
        pass
    
    @abstractmethod
    def format_quantity(self, quantity: float, precision_info: SymbolPrecision) -> str:
        """格式化下单数量"""
        pass
    
    @abstractmethod
    def format_price(self, price: float, precision_info: SymbolPrecision) -> str:
        """格式化价格"""
        pass
    
    @abstractmethod
    async def calculate_order_quantity(self, symbol: str, leverage: int,
                                        position_percent: float = None) -> float:
        """计算下单数量"""
        pass


class ExchangeWebSocket(ABC):
    """交易所WebSocket抽象接口"""
    
    @property
    @abstractmethod
    def exchange_type(self) -> ExchangeType:
        """返回交易所类型"""
        pass
    
    @abstractmethod
    def add_callback(self, callback: Callable[[KlineData], Any]):
        """添加K线数据回调"""
        pass
    
    @abstractmethod
    def remove_callback(self, callback: Callable[[KlineData], Any]):
        """移除回调"""
        pass
    
    @abstractmethod
    async def subscribe(self, symbol: str, interval: str):
        """订阅交易对的K线"""
        pass
    
    @abstractmethod
    async def unsubscribe(self, symbol: str):
        """取消订阅"""
        pass
    
    @abstractmethod
    async def start(self):
        """启动WebSocket服务"""
        pass
    
    @abstractmethod
    async def stop(self):
        """停止WebSocket服务"""
        pass
    
    @abstractmethod
    def get_status(self) -> dict:
        """获取WebSocket状态
        
        Returns:
            {
                "connected": bool,
                "subscriptions": List[str],
                "reconnect_count": int,
                "start_time": str or None
            }
        """
        pass
