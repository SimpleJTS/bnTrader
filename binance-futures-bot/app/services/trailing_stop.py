"""
移动止损模块
实现分级止损和追踪止损
"""
import asyncio
import logging
from typing import Dict, Optional
from datetime import datetime

from app.config import settings
from app.database import DatabaseManager
from app.services.binance_api import binance_api
from app.services.position_manager import position_manager
from app.models import Position, StopLossLog

logger = logging.getLogger(__name__)


class TrailingStopManager:
    """移动止损管理器
    
    止损规则（基于价格变动百分比，不含杠杆）:
    - 价格变动 2.5%~5%: 止损提到成本价
    - 价格变动 5%~10%: 锁定3%价格利润
    - 价格变动 ≥10%: 锁定5%价格利润并启动追踪（价格回撤3%触发）
    """
    
    def __init__(self):
        self._highest_prices: Dict[str, float] = {}  # 记录最高价(做多)或最低价(做空)
        self._running = False
        self._check_task: Optional[asyncio.Task] = None
        self._check_interval = 5  # 检查间隔(秒)
    
    def calculate_profit_percent(self, position: Position, current_price: float) -> float:
        """计算盈利百分比(基于价格变动，不含杠杆)"""
        if position.side == "LONG":
            profit = ((current_price - position.entry_price) / position.entry_price) * 100
        else:
            profit = ((position.entry_price - current_price) / position.entry_price) * 100
        
        return profit
    
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
        adjust_reason = None
        adjust_detail = None
        locked_profit = None
        
        # 级别1: 价格变动2.5%~5%，止损提到成本价
        if 2.5 <= profit_percent < 5.0 and current_level < 1:
            new_stop_price = position.entry_price
            new_level = 1
            locked_profit = 0.0
            adjust_reason = "盈利保护 - 止损提至成本价"
            adjust_detail = f"当前价格变动{profit_percent:.2f}%（触发阈值2.5%），止损从{position.stop_loss_price:.6f}提升至成本价{position.entry_price:.6f}，确保不亏损"
            logger.info(f"[{symbol}] 触发级别1: 价格变动{profit_percent:.2f}%，止损提升至成本价 {new_stop_price}")
        
        # 级别2: 价格变动5%~10%，锁定3%价格利润
        elif 5.0 <= profit_percent < 10.0 and current_level < 2:
            # 锁定3%价格利润
            lock_profit = 3.0
            if position.side == "LONG":
                new_stop_price = position.entry_price * (1 + lock_profit / 100)
            else:
                new_stop_price = position.entry_price * (1 - lock_profit / 100)
            new_level = 2
            locked_profit = 3.0
            adjust_reason = "锁定利润 - 保护3%价格收益"
            adjust_detail = f"当前价格变动{profit_percent:.2f}%（触发阈值5%），锁定3%价格利润，止损价设为{new_stop_price:.6f}"
            logger.info(f"[{symbol}] 触发级别2: 价格变动{profit_percent:.2f}%，锁定3%利润，止损价 {new_stop_price}")
        
        # 级别3: 价格变动≥10%，锁定5%并启动追踪
        elif profit_percent >= 10.0 and current_level < 3:
            # 锁定5%价格利润
            lock_profit = 5.0
            if position.side == "LONG":
                new_stop_price = position.entry_price * (1 + lock_profit / 100)
            else:
                new_stop_price = position.entry_price * (1 - lock_profit / 100)
            new_level = 3
            is_trailing = True
            locked_profit = 5.0
            adjust_reason = "启动追踪止损 - 锁定5%价格收益"
            adjust_detail = f"当前价格变动{profit_percent:.2f}%（触发阈值10%），锁定5%价格利润并启动追踪止损模式，止损价设为{new_stop_price:.6f}，后续将跟随价格变动（回撤3%触发）"
            logger.info(f"[{symbol}] 触发级别3: 价格变动{profit_percent:.2f}%，锁定5%利润并启动追踪止损，止损价 {new_stop_price}")
        
        # 追踪止损逻辑: 价格回撤3%触发
        if is_trailing and current_level >= 3:
            trailing_percent = 3.0  # 价格回撤3%
            
            if position.side == "LONG":
                # 做多：从最高价回撤trailing_percent
                trailing_stop = highest * (1 - trailing_percent / 100)
                # 只有新止损更高才更新
                if trailing_stop > position.stop_loss_price:
                    new_stop_price = trailing_stop
                    # 计算锁定的价格利润百分比
                    locked_profit = ((trailing_stop - position.entry_price) / position.entry_price) * 100
                    adjust_reason = "追踪止损上移"
                    adjust_detail = f"价格创新高{highest:.6f}，止损跟随上移至{new_stop_price:.6f}（回撤{trailing_percent:.2f}%），当前锁定价格利润约{locked_profit:.2f}%"
                    logger.info(f"[{symbol}] 追踪止损更新: 最高价={highest}, 原止损={position.stop_loss_price} -> 新止损={new_stop_price}")
            else:
                # 做空：从最低价反弹trailing_percent
                trailing_stop = highest * (1 + trailing_percent / 100)
                # 只有新止损更低才更新
                if trailing_stop < position.stop_loss_price:
                    new_stop_price = trailing_stop
                    # 计算锁定的价格利润百分比
                    locked_profit = ((position.entry_price - trailing_stop) / position.entry_price) * 100
                    adjust_reason = "追踪止损下移"
                    adjust_detail = f"价格创新低{highest:.6f}，止损跟随下移至{new_stop_price:.6f}（反弹{trailing_percent:.2f}%），当前锁定价格利润约{locked_profit:.2f}%"
                    logger.info(f"[{symbol}] 追踪止损更新: 最低价={highest}, 原止损={position.stop_loss_price} -> 新止损={new_stop_price}")
        
        # 更新止损并记录日志
        if new_stop_price and new_stop_price != position.stop_loss_price and adjust_reason:
            # 先记录止损调整日志
            await self._log_stop_loss_adjustment(
                position=position,
                old_stop_price=position.stop_loss_price,
                new_stop_price=new_stop_price,
                current_price=current_price,
                profit_percent=profit_percent,
                locked_profit_percent=locked_profit,
                old_level=current_level,
                new_level=new_level,
                is_trailing=is_trailing,
                adjust_reason=adjust_reason,
                adjust_detail=adjust_detail
            )
            
            # 更新止损
            await position_manager.update_stop_loss(
                symbol=symbol,
                new_stop_price=new_stop_price,
                level=new_level,
                is_trailing=is_trailing
            )
    
    async def _log_stop_loss_adjustment(
        self,
        position: Position,
        old_stop_price: float,
        new_stop_price: float,
        current_price: float,
        profit_percent: float,
        locked_profit_percent: Optional[float],
        old_level: int,
        new_level: int,
        is_trailing: bool,
        adjust_reason: str,
        adjust_detail: str
    ):
        """记录止损调整日志到数据库"""
        session = await DatabaseManager.get_session()
        try:
            log = StopLossLog(
                symbol=position.symbol,
                side=position.side,
                entry_price=position.entry_price,
                old_stop_price=old_stop_price,
                new_stop_price=new_stop_price,
                current_price=current_price,
                profit_percent=profit_percent,
                locked_profit_percent=locked_profit_percent,
                old_level=old_level,
                new_level=new_level,
                is_trailing=is_trailing,
                adjust_reason=adjust_reason,
                adjust_detail=adjust_detail
            )
            session.add(log)
            await session.commit()
            logger.debug(f"[{position.symbol}] 止损调整日志已记录: {adjust_reason}")
        except Exception as e:
            logger.error(f"[{position.symbol}] 记录止损调整日志失败: {e}")
            await session.rollback()
        finally:
            await session.close()
    
    async def _check_loop(self):
        """止损检查循环"""
        while self._running:
            try:
                # 先同步交易所实际持仓状态，清理已平仓的仓位
                await self._sync_positions_with_exchange()
                
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
    
    async def _sync_positions_with_exchange(self):
        """同步交易所实际持仓，清理已平仓的仓位"""
        try:
            # 获取交易所实际持仓
            exchange_positions = await binance_api.get_position()
            exchange_symbols = {p["symbol"] for p in exchange_positions}
            
            # 获取本地缓存的仓位
            local_positions = position_manager.get_all_positions()
            
            for position in local_positions:
                if position.symbol not in exchange_symbols:
                    # 交易所已无持仓，可能是止损触发或手动平仓
                    logger.warning(f"[{position.symbol}] 检测到交易所已无持仓，触发同步平仓处理")
                    try:
                        # 使用 mark_position_closed 只更新本地状态，不再下单
                        await position_manager.mark_position_closed(position.symbol, reason="STOP_LOSS")
                        # 清理追踪数据
                        self.reset_tracking(position.symbol)
                    except Exception as e:
                        logger.error(f"[{position.symbol}] 同步平仓失败: {e}")
        except Exception as e:
            logger.error(f"同步交易所持仓状态失败: {e}")
    
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
