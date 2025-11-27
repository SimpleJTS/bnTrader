"""
EMA交易策略模块
实现EMA6和EMA51的金叉死叉策略
"""
import logging
from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import numpy as np

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """信号类型"""
    NONE = "NONE"
    LONG = "LONG"  # 做多
    SHORT = "SHORT"  # 做空


@dataclass
class StrategySignal:
    """策略信号"""
    signal_type: SignalType
    symbol: str
    price: float
    ema_fast: float
    ema_slow: float
    cross_count: int
    message: str


class EMAStrategy:
    """EMA交叉策略
    
    规则:
    1. 检测当前K线的EMA6和EMA51是否相交
    2. 如果当前K线没有相交，不开仓
    3. 如果相交，判断是金叉还是死叉
    4. 检查前20根K线中EMA6和EMA51的交叉次数
    5. 如果金叉且交叉次数>2，则做多
    6. 如果死叉且交叉次数>2，则做空
    """
    
    def __init__(self, fast_period: int = 6, slow_period: int = 51, lookback: int = 20):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.lookback = lookback
    
    @staticmethod
    def calculate_ema(prices: List[float], period: int) -> List[float]:
        """计算EMA
        
        EMA = 价格 * k + 昨日EMA * (1 - k)
        k = 2 / (period + 1)
        """
        if len(prices) < period:
            return []
        
        prices = np.array(prices)
        ema = np.zeros(len(prices))
        
        # 使用SMA作为第一个EMA值
        ema[period - 1] = np.mean(prices[:period])
        
        # 计算乘数
        multiplier = 2 / (period + 1)
        
        # 计算后续EMA值
        for i in range(period, len(prices)):
            ema[i] = prices[i] * multiplier + ema[i - 1] * (1 - multiplier)
        
        return ema.tolist()
    
    def detect_cross(self, ema_fast: List[float], ema_slow: List[float], 
                     index: int) -> Optional[str]:
        """检测交叉
        
        Returns:
            "GOLDEN": 金叉 (快线上穿慢线)
            "DEATH": 死叉 (快线下穿慢线)
            None: 无交叉
        """
        if index < 1 or index >= len(ema_fast) or index >= len(ema_slow):
            return None
        
        # 当前状态
        fast_above_now = ema_fast[index] > ema_slow[index]
        # 前一状态
        fast_above_prev = ema_fast[index - 1] > ema_slow[index - 1]
        
        # 检测交叉
        if fast_above_now and not fast_above_prev:
            return "GOLDEN"  # 金叉
        elif not fast_above_now and fast_above_prev:
            return "DEATH"  # 死叉
        
        return None
    
    def count_crosses(self, ema_fast: List[float], ema_slow: List[float], 
                      end_index: int, lookback: int = None) -> int:
        """统计交叉次数
        
        Args:
            ema_fast: 快速EMA列表
            ema_slow: 慢速EMA列表
            end_index: 结束位置(不包含当前K线)
            lookback: 回看K线数量
        
        Returns:
            交叉次数
        """
        if lookback is None:
            lookback = self.lookback
        
        start_index = max(1, end_index - lookback)
        cross_count = 0
        
        for i in range(start_index, end_index):
            if self.detect_cross(ema_fast, ema_slow, i):
                cross_count += 1
        
        return cross_count
    
    def analyze(self, symbol: str, klines: List[dict]) -> StrategySignal:
        """分析K线数据生成信号
        
        Args:
            symbol: 交易对
            klines: K线数据列表，每个元素包含 open, high, low, close, volume
                   格式: [open_time, open, high, low, close, volume, close_time, ...]
        
        Returns:
            StrategySignal
        """
        # 至少需要slow_period + lookback + 2根K线
        min_klines = self.slow_period + self.lookback + 2
        if len(klines) < min_klines:
            return StrategySignal(
                signal_type=SignalType.NONE,
                symbol=symbol,
                price=0,
                ema_fast=0,
                ema_slow=0,
                cross_count=0,
                message=f"K线数据不足: {len(klines)} < {min_klines}"
            )
        
        # 提取收盘价
        close_prices = [float(k[4]) for k in klines]
        
        # 计算EMA
        ema_fast = self.calculate_ema(close_prices, self.fast_period)
        ema_slow = self.calculate_ema(close_prices, self.slow_period)
        
        # 当前K线索引(最后一根已收盘的K线)
        current_index = len(close_prices) - 1
        current_price = close_prices[current_index]
        current_ema_fast = ema_fast[current_index] if ema_fast else 0
        current_ema_slow = ema_slow[current_index] if ema_slow else 0
        
        # 检测当前K线是否有交叉
        cross_type = self.detect_cross(ema_fast, ema_slow, current_index)
        
        if not cross_type:
            return StrategySignal(
                signal_type=SignalType.NONE,
                symbol=symbol,
                price=current_price,
                ema_fast=current_ema_fast,
                ema_slow=current_ema_slow,
                cross_count=0,
                message="无交叉信号"
            )
        
        # 统计前20根K线的交叉次数(不包含当前K线)
        cross_count = self.count_crosses(ema_fast, ema_slow, current_index)
        
        # 判断信号
        # 当前K线有交叉，且前20根K线交叉次数>2次，才开仓
        if cross_type == "GOLDEN" and cross_count > 2:
            return StrategySignal(
                signal_type=SignalType.LONG,
                symbol=symbol,
                price=current_price,
                ema_fast=current_ema_fast,
                ema_slow=current_ema_slow,
                cross_count=cross_count,
                message=f"金叉信号! EMA{self.fast_period}上穿EMA{self.slow_period}, 前{self.lookback}根K线交叉{cross_count}次(>2次)"
            )
        elif cross_type == "DEATH" and cross_count > 2:
            return StrategySignal(
                signal_type=SignalType.SHORT,
                symbol=symbol,
                price=current_price,
                ema_fast=current_ema_fast,
                ema_slow=current_ema_slow,
                cross_count=cross_count,
                message=f"死叉信号! EMA{self.fast_period}下穿EMA{self.slow_period}, 前{self.lookback}根K线交叉{cross_count}次(>2次)"
            )
        else:
            return StrategySignal(
                signal_type=SignalType.NONE,
                symbol=symbol,
                price=current_price,
                ema_fast=current_ema_fast,
                ema_slow=current_ema_slow,
                cross_count=cross_count,
                message=f"{cross_type}但前{self.lookback}根K线交叉次数不足({cross_count}次<=2次), 不开仓"
            )
    
    def calculate_amplitude(self, klines: List[dict], lookback: int = 200) -> float:
        """计算振幅
        
        振幅 = (最高价 - 最低价) / 最低价 * 100%
        
        Args:
            klines: K线数据
            lookback: 回看K线数量
        
        Returns:
            振幅百分比
        """
        if len(klines) < lookback:
            lookback = len(klines)
        
        recent_klines = klines[-lookback:]
        
        if not recent_klines:
            return 0
        
        # 计算区间最高价和最低价
        high_prices = [float(k[2]) for k in recent_klines]
        low_prices = [float(k[3]) for k in recent_klines]
        
        highest = max(high_prices)
        lowest = min(low_prices)
        
        if lowest == 0:
            return 0
        
        amplitude = ((highest - lowest) / lowest) * 100
        return round(amplitude, 2)


# 全局策略实例
ema_strategy = EMAStrategy()
