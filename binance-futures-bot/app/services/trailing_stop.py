"""
移动止损模块
实现分级止损和追踪止损
"""
import asyncio
import logging
from typing import Dict, Optional
from datetime import datetime

from app.config import settings
from app.services.binance_api import binance_api
from app.services.position_manager import position_manager
from app.models import Position

logger = logging.getLogger(__name__)


class TrailingStopManager:
    """移动止损管理器
    
    止损规则:
    - 盈利 2.5%~5%: 止损提到成本价
    - 盈利 5%~10%: 锁定约3%的利润
    - 盈利 ≥10%: 锁定5%并启动追踪（价格回撤3%触发）
    """
    
    def __init__(self):
        self._highest_prices: Dict[str, float] = {}  # 记录最高价(做多)或最低价(做空)
        self._running = False
        self._check_task: Optional[asyncio.Task] = None
        self._check_interval = 5  # 检查间隔(秒)
    
    def calculate_profit_percent(self, position: Position, current_price: float) -> float:
        """计算盈利百分比(考虑杠杆)"""
        if position.side == "LONG":
            raw_profit = ((current_price - position.entry_price) / position.entry_price) * 100
        else:
            raw_profit = ((position.entry_price - current_price) / position.entry_price) * 100
        
        # 乘以杠杆得到实际盈利
        return raw_profit * position.leverage
    
    async def check_trailing_stop(self, position: Position, current_price: float):
        """检查并更新移动止损
        
        Args:
            position: 仓位对象
            current_price: 当前价格
        """
        symbol = position.symbol
        profit_percent = self.calculate_profit_percent(position, current_price)
        
        logger.debug(f"[{symbol}] 当前盈亏: {profit_percent:.2f}%, 现价: {current_price}")
        
        # 更新最高/最低价格
        if position.side == "LONG":
            if symbol not in self._highest_prices or current_price > self._highest_prices[symbol]:
                self._highest_prices[symbol] = current_price
            highest = self._highest_prices[symbol]
        else:
            if symbol not in self._highest_prices or current_price < self._highest_prices[symbol]:
                self._highest_prices[symbol] = current_price
            highest = self._highest_prices[symbol]
        
        current_level = position.current_stop_level
        new_stop_price = None
        new_level = current_level
        is_trailing = position.is_trailing_active
        
        # 级别1: 盈利2.5%~5%，止损提到成本价
        if 2.5 <= profit_percent < 5.0 and current_level < 1:
            new_stop_price = position.entry_price
            new_level = 1
            logger.info(f"[{symbol}] 触发级别1: 盈利{profit_percent:.2f}%，止损提升至成本价 {new_stop_price}")
        
        # 级别2: 盈利5%~10%，锁定约3%利润
        elif 5.0 <= profit_percent < 10.0 and current_level < 2:
            # 锁定3%利润意味着止损价格要锁定3%的盈利
            if position.side == "LONG":
                lock_profit = 3.0 / position.leverage  # 转换为价格变动百分比
                new_stop_price = position.entry_price * (1 + lock_profit / 100)
            else:
                lock_profit = 3.0 / position.leverage
                new_stop_price = position.entry_price * (1 - lock_profit / 100)
            new_level = 2
            logger.info(f"[{symbol}] 触发级别2: 盈利{profit_percent:.2f}%，锁定3%利润，止损价 {new_stop_price}")
        
        # 级别3: 盈利≥10%，锁定5%并启动追踪
        elif profit_percent >= 10.0 and current_level < 3:
            # 锁定5%利润
            if position.side == "LONG":
                lock_profit = 5.0 / position.leverage
                new_stop_price = position.entry_price * (1 + lock_profit / 100)
            else:
                lock_profit = 5.0 / position.leverage
                new_stop_price = position.entry_price * (1 - lock_profit / 100)
            new_level = 3
            is_trailing = True
            logger.info(f"[{symbol}] 触发级别3: 盈利{profit_percent:.2f}%，锁定5%利润并启动追踪止损，止损价 {new_stop_price}")
        
        # 追踪止损逻辑: 价格回撤3%触发
        if is_trailing and current_level >= 3:
            trailing_percent = 3.0 / position.leverage  # 转换为价格变动百分比
            
            if position.side == "LONG":
                # 做多：从最高价回撤trailing_percent
                trailing_stop = highest * (1 - trailing_percent / 100)
                # 只有新止损更高才更新
                if trailing_stop > position.stop_loss_price:
                    new_stop_price = trailing_stop
                    logger.info(f"[{symbol}] 追踪止损更新: 最高价={highest}, 原止损={position.stop_loss_price} -> 新止损={new_stop_price}")
            else:
                # 做空：从最低价反弹trailing_percent
                trailing_stop = highest * (1 + trailing_percent / 100)
                # 只有新止损更低才更新
                if trailing_stop < position.stop_loss_price:
                    new_stop_price = trailing_stop
                    logger.info(f"[{symbol}] 追踪止损更新: 最低价={highest}, 原止损={position.stop_loss_price} -> 新止损={new_stop_price}")
        
        # 更新止损
        if new_stop_price and new_stop_price != position.stop_loss_price:
            await position_manager.update_stop_loss(
                symbol=symbol,
                new_stop_price=new_stop_price,
                level=new_level,
                is_trailing=is_trailing
            )
    
    async def _check_loop(self):
        """止损检查循环"""
        while self._running:
            try:
                positions = position_manager.get_all_positions()
                
                for position in positions:
                    if position.status != "OPEN":
                        continue
                    
                    try:
                        current_price = await binance_api.get_current_price(position.symbol)
                        await self.check_trailing_stop(position, current_price)
                    except Exception as e:
                        logger.error(f"[{position.symbol}] 检查移动止损失败: {e}")
                
                await asyncio.sleep(self._check_interval)
                
            except Exception as e:
                logger.error(f"移动止损检查循环错误: {e}")
                await asyncio.sleep(self._check_interval)
    
    async def start(self):
        """启动移动止损检查"""
        if self._running:
            return
        
        self._running = True
        self._check_task = asyncio.create_task(self._check_loop())
        logger.info("移动止损管理器已启动")
    
    async def stop(self):
        """停止"""
        self._running = False
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        logger.info("移动止损管理器已停止")
    
    def reset_tracking(self, symbol: str):
        """重置追踪数据"""
        if symbol in self._highest_prices:
            del self._highest_prices[symbol]


# 全局实例
trailing_stop_manager = TrailingStopManager()
