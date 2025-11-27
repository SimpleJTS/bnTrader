"""
仓位管理模块
负责开平仓、止损设置等
"""
import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, List
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import DatabaseManager
from app.models import Position, TradingPair, TradeLog
from app.services.binance_api import binance_api
from app.services.telegram import telegram_service
from app.config import settings

logger = logging.getLogger(__name__)


class PositionManager:
    """仓位管理器"""
    
    def __init__(self):
        self._positions: Dict[str, Position] = {}  # 内存缓存
    
    async def load_positions(self):
        """从数据库加载开放仓位"""
        session = await DatabaseManager.get_session()
        try:
            result = await session.execute(
                select(Position).where(Position.status == "OPEN")
            )
            positions = result.scalars().all()
            for pos in positions:
                self._positions[pos.symbol] = pos
            logger.info(f"Loaded {len(self._positions)} open positions")
        finally:
            await session.close()
    
    async def get_position(self, symbol: str) -> Optional[Position]:
        """获取仓位"""
        return self._positions.get(symbol)
    
    async def has_position(self, symbol: str) -> bool:
        """检查是否有仓位"""
        return symbol in self._positions
    
    async def open_position(self, symbol: str, side: str, entry_price: float,
                            quantity: float, leverage: int, 
                            stop_loss_percent: float) -> Optional[Position]:
        """开仓
        
        Args:
            symbol: 交易对
            side: LONG/SHORT
            entry_price: 入场价格
            quantity: 数量
            leverage: 杠杆
            stop_loss_percent: 止损百分比
        
        Returns:
            Position对象
        """
        session = await DatabaseManager.get_session()
        try:
            # 检查是否已有仓位
            if await self.has_position(symbol):
                logger.warning(f"Already have position for {symbol}")
                return None
            
            # 设置杠杆
            await binance_api.set_leverage(symbol, leverage)
            
            # 设置全仓模式
            await binance_api.set_margin_type(symbol, "CROSSED")
            
            # 下单方向
            order_side = "BUY" if side == "LONG" else "SELL"
            
            # 下市价单
            order_result = await binance_api.place_market_order(
                symbol=symbol,
                side=order_side,
                quantity=quantity
            )
            
            logger.info(f"Market order placed: {order_result}")
            
            # 获取实际成交价格
            # 优先使用avgPrice，如果为0则通过累计成交额/累计成交量计算，最后使用entry_price
            avg_price_str = order_result.get("avgPrice", "0")
            actual_price = float(avg_price_str) if avg_price_str else 0
            
            if actual_price <= 0:
                # 尝试通过累计成交额/成交量计算均价
                cum_quote = float(order_result.get("cumQuote", 0) or order_result.get("cummulativeQuoteQty", 0))
                executed_qty = float(order_result.get("executedQty", 0))
                if cum_quote > 0 and executed_qty > 0:
                    actual_price = cum_quote / executed_qty
                else:
                    actual_price = entry_price
                logger.warning(f"avgPrice not available, calculated price: {actual_price}")
            
            actual_qty = float(order_result.get("executedQty", 0))
            if actual_qty <= 0:
                actual_qty = quantity
                logger.warning(f"executedQty not available, using quantity: {actual_qty}")
            
            # 验证成交价格和数量
            if actual_price <= 0:
                raise ValueError(f"Invalid execution price: {actual_price}")
            if actual_qty <= 0:
                raise ValueError(f"Invalid execution quantity: {actual_qty}")
            
            # 验证止损百分比
            if stop_loss_percent <= 0 or stop_loss_percent >= 100:
                raise ValueError(f"Invalid stop_loss_percent: {stop_loss_percent} (must be between 0 and 100)")
            
            # 计算止损价格
            if side == "LONG":
                stop_loss_price = actual_price * (1 - stop_loss_percent / 100)
            else:
                stop_loss_price = actual_price * (1 + stop_loss_percent / 100)
            
            # 验证止损价格
            if stop_loss_price <= 0:
                raise ValueError(f"Invalid stop_loss_price: {stop_loss_price} (entry={actual_price}, percent={stop_loss_percent}%)")
            
            logger.info(f"Setting stop loss: symbol={symbol}, side={side}, price={stop_loss_price}, qty={actual_qty}")
            
            # 设置止损单
            stop_side = "SELL" if side == "LONG" else "BUY"
            stop_order = await binance_api.place_stop_loss_order(
                symbol=symbol,
                side=stop_side,
                quantity=actual_qty,
                stop_price=stop_loss_price
            )
            
            stop_order_id = str(stop_order.get("orderId", ""))
            
            # 创建仓位记录
            position = Position(
                symbol=symbol,
                side=side,
                entry_price=actual_price,
                quantity=actual_qty,
                leverage=leverage,
                stop_loss_price=stop_loss_price,
                stop_loss_order_id=stop_order_id,
                current_stop_level=0,
                is_trailing_active=False,
                status="OPEN",
                opened_at=datetime.utcnow()
            )
            
            session.add(position)
            await session.commit()
            await session.refresh(position)
            
            # 缓存
            self._positions[symbol] = position
            
            # 记录交易日志
            trade_log = TradeLog(
                symbol=symbol,
                action=f"OPEN_{side}",
                price=actual_price,
                quantity=actual_qty,
                order_id=str(order_result.get("orderId", "")),
                message=f"开{side}仓: 价格={actual_price}, 数量={actual_qty}, 杠杆={leverage}x, 止损={stop_loss_price:.4f}",
                extra_data={
                    "leverage": leverage,
                    "stop_loss_price": stop_loss_price,
                    "stop_order_id": stop_order_id
                }
            )
            session.add(trade_log)
            await session.commit()
            
            # TG通知
            msg = (
                f"🟢 **开仓通知**\n"
                f"交易对: {symbol}\n"
                f"方向: {'做多 📈' if side == 'LONG' else '做空 📉'}\n"
                f"价格: {actual_price:.4f}\n"
                f"数量: {actual_qty}\n"
                f"杠杆: {leverage}x\n"
                f"止损: {stop_loss_price:.4f} ({stop_loss_percent}%)"
            )
            await telegram_service.send_message(msg)
            
            return position
            
        except Exception as e:
            logger.error(f"Open position error: {e}")
            await session.rollback()
            await telegram_service.send_message(f"❌ 开仓失败: {symbol}\n错误: {str(e)}")
            raise
        finally:
            await session.close()
    
    async def close_position(self, symbol: str, reason: str = "SIGNAL") -> bool:
        """平仓
        
        Args:
            symbol: 交易对
            reason: 平仓原因 (SIGNAL/STOP_LOSS/TRAILING_STOP/MANUAL)
        """
        session = await DatabaseManager.get_session()
        try:
            position = self._positions.get(symbol)
            if not position:
                logger.warning(f"No position found for {symbol}")
                return False
            
            # 取消所有挂单
            try:
                await binance_api.cancel_all_orders(symbol)
            except Exception as e:
                logger.warning(f"Cancel orders error: {e}")
            
            # 获取当前价格
            current_price = await binance_api.get_current_price(symbol)
            
            # 平仓方向
            close_side = "SELL" if position.side == "LONG" else "BUY"
            
            # 下市价平仓单
            order_result = await binance_api.place_market_order(
                symbol=symbol,
                side=close_side,
                quantity=position.quantity,
                reduce_only=True
            )
            
            # 计算盈亏
            if position.side == "LONG":
                pnl = (current_price - position.entry_price) * position.quantity
                pnl_percent = ((current_price - position.entry_price) / position.entry_price) * 100 * position.leverage
            else:
                pnl = (position.entry_price - current_price) * position.quantity
                pnl_percent = ((position.entry_price - current_price) / position.entry_price) * 100 * position.leverage
            
            # 更新仓位记录
            await session.execute(
                update(Position)
                .where(Position.id == position.id)
                .values(
                    status="CLOSED",
                    pnl=pnl,
                    pnl_percent=pnl_percent,
                    closed_at=datetime.utcnow(),
                    close_reason=reason
                )
            )
            await session.commit()
            
            # 从缓存移除
            del self._positions[symbol]
            
            # 记录交易日志
            trade_log = TradeLog(
                symbol=symbol,
                action=f"CLOSE_{reason}",
                price=current_price,
                quantity=position.quantity,
                order_id=str(order_result.get("orderId", "")),
                message=f"平仓: 价格={current_price}, 盈亏={pnl:.4f} USDT ({pnl_percent:.2f}%)",
                extra_data={
                    "entry_price": position.entry_price,
                    "pnl": pnl,
                    "pnl_percent": pnl_percent,
                    "reason": reason
                }
            )
            session.add(trade_log)
            await session.commit()
            
            # TG通知
            emoji = "🟢" if pnl >= 0 else "🔴"
            msg = (
                f"{emoji} **平仓通知**\n"
                f"交易对: {symbol}\n"
                f"方向: {'做多' if position.side == 'LONG' else '做空'}\n"
                f"入场价: {position.entry_price:.4f}\n"
                f"平仓价: {current_price:.4f}\n"
                f"盈亏: {pnl:.4f} USDT ({pnl_percent:.2f}%)\n"
                f"原因: {reason}"
            )
            await telegram_service.send_message(msg)
            
            return True
            
        except Exception as e:
            logger.error(f"Close position error: {e}")
            await session.rollback()
            await telegram_service.send_message(f"❌ 平仓失败: {symbol}\n错误: {str(e)}")
            raise
        finally:
            await session.close()
    
    async def update_stop_loss(self, symbol: str, new_stop_price: float,
                                level: int = None, is_trailing: bool = False) -> bool:
        """更新止损价格
        
        Args:
            symbol: 交易对
            new_stop_price: 新止损价格
            level: 止损级别
            is_trailing: 是否为追踪止损
        """
        session = await DatabaseManager.get_session()
        try:
            position = self._positions.get(symbol)
            if not position:
                return False
            
            # 取消原止损单
            if position.stop_loss_order_id:
                try:
                    await binance_api.cancel_order(symbol, position.stop_loss_order_id)
                    logger.info(f"Cancelled old stop loss order: {position.stop_loss_order_id}")
                except Exception as e:
                    logger.warning(f"Cancel old stop order error: {e}")
            
            # 获取精度信息
            precision_info = await binance_api.get_symbol_precision(symbol)
            formatted_price = binance_api.format_price(new_stop_price, precision_info)
            
            # 验证新止损价格
            if Decimal(formatted_price) <= 0:
                raise ValueError(f"Invalid new stop price: {new_stop_price} -> {formatted_price}")
            
            # 验证仓位数量
            if position.quantity <= 0:
                raise ValueError(f"Invalid position quantity: {position.quantity}")
            
            # 更新 new_stop_price 为格式化后的值
            new_stop_price = float(formatted_price)
            
            logger.info(f"Updating stop loss: symbol={symbol}, new_price={new_stop_price}, qty={position.quantity}")
            
            # 设置新止损单
            stop_side = "SELL" if position.side == "LONG" else "BUY"
            stop_order = await binance_api.place_stop_loss_order(
                symbol=symbol,
                side=stop_side,
                quantity=position.quantity,
                stop_price=new_stop_price
            )
            
            new_order_id = str(stop_order.get("orderId", ""))
            
            # 更新数据库
            update_values = {
                "stop_loss_price": new_stop_price,
                "stop_loss_order_id": new_order_id
            }
            if level is not None:
                update_values["current_stop_level"] = level
            if is_trailing:
                update_values["is_trailing_active"] = True
            
            await session.execute(
                update(Position)
                .where(Position.id == position.id)
                .values(**update_values)
            )
            await session.commit()
            
            # 更新缓存
            position.stop_loss_price = new_stop_price
            position.stop_loss_order_id = new_order_id
            if level is not None:
                position.current_stop_level = level
            if is_trailing:
                position.is_trailing_active = True
            
            # 记录日志
            old_stop = position.stop_loss_price
            trade_log = TradeLog(
                symbol=symbol,
                action="STOP_LOSS_ADJUST",
                price=new_stop_price,
                message=f"止损调整: {old_stop:.4f} -> {new_stop_price:.4f}, 级别={level}, 追踪={is_trailing}",
                extra_data={
                    "old_stop_price": old_stop,
                    "new_stop_price": new_stop_price,
                    "level": level,
                    "is_trailing": is_trailing
                }
            )
            session.add(trade_log)
            await session.commit()
            
            # TG通知
            msg = (
                f"🔔 **止损调整**\n"
                f"交易对: {symbol}\n"
                f"原止损: {old_stop:.4f}\n"
                f"新止损: {new_stop_price:.4f}\n"
                f"级别: {level if level else '初始'}\n"
                f"追踪止损: {'是' if is_trailing else '否'}"
            )
            await telegram_service.send_message(msg)
            
            return True
            
        except Exception as e:
            logger.error(f"Update stop loss error: {e}")
            await session.rollback()
            raise
        finally:
            await session.close()
    
    async def sync_with_exchange(self):
        """与交易所同步仓位状态"""
        try:
            exchange_positions = await binance_api.get_position()
            exchange_symbols = {p["symbol"] for p in exchange_positions}
            
            # 检查本地仓位是否还存在于交易所
            for symbol in list(self._positions.keys()):
                if symbol not in exchange_symbols:
                    logger.warning(f"Position {symbol} not found on exchange, marking as closed")
                    await self.close_position(symbol, reason="EXCHANGE_SYNC")
            
        except Exception as e:
            logger.error(f"Sync positions error: {e}")
    
    def get_all_positions(self) -> List[Position]:
        """获取所有仓位"""
        return list(self._positions.values())


# 全局实例
position_manager = PositionManager()
