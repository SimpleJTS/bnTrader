"""
移动止损模块
实现分级止损和追踪止损
使用工厂模式支持多交易所
"""
import asyncio
import json
import logging
from typing import Dict, Optional
from datetime import datetime

from sqlalchemy import select

from app.config import settings, config_manager
from app.database import DatabaseManager
from app.services.exchange_factory import get_exchange_api
from app.services.position_manager import position_manager
from app.models import Position, StopLossLog, SystemConfig

logger = logging.getLogger(__name__)


# 默认止损配置
DEFAULT_TRAILING_CONFIG = {
    "level_1": {"profit_min": 2.5, "profit_max": 5.0, "lock_profit": 0, "trailing_enabled": False, "trailing_percent": 3.0},
    "level_2": {"profit_min": 5.0, "profit_max": 10.0, "lock_profit": 3.0, "trailing_enabled": False, "trailing_percent": 3.0},
    "level_3": {"profit_min": 10.0, "profit_max": None, "lock_profit": 5.0, "trailing_enabled": True, "trailing_percent": 3.0}
}


class TrailingStopManager:
    """移动止损管理器
    
    止损规则（基于价格变动百分比，不含杠杆）:
    - 各级别参数可通过Web界面配置
    - 默认: 2.5%~5%保本，5%~10%锁3%，≥10%锁5%并追踪回撤3%
    """
    
    def __init__(self):
        self._highest_prices: Dict[str, float] = {}  # 记录最高价(做多)或最低价(做空)
        self._running = False
        self._check_task: Optional[asyncio.Task] = None
        self._check_interval = 5  # 检查间隔(秒)
        self._config: Dict = DEFAULT_TRAILING_CONFIG.copy()  # 当前配置
    
    async def load_config(self):
        """从数据库加载止损配置"""
        session = await DatabaseManager.get_session()
        try:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key == "TRAILING_STOP_CONFIG")
            )
            config = result.scalar_one_or_none()
            
            if config and config.value:
                try:
                    self._config = json.loads(config.value)
                    logger.info(f"已加载移动止损配置: {self._config}")
                except json.JSONDecodeError:
                    logger.warning("移动止损配置解析失败，使用默认配置")
                    self._config = DEFAULT_TRAILING_CONFIG.copy()
            else:
                logger.info("未找到移动止损配置，使用默认配置")
                self._config = DEFAULT_TRAILING_CONFIG.copy()
        except Exception as e:
            logger.error(f"加载移动止损配置失败: {e}")
            self._config = DEFAULT_TRAILING_CONFIG.copy()
        finally:
            await session.close()
    
    async def on_config_change(self, change_type: str, data: dict):
        """处理配置变更"""
        if change_type == "trailing_stop_config_updated":
            self._config = data
            logger.info(f"移动止损配置已更新: {self._config}")
    
    def get_config(self) -> Dict:
        """获取当前配置"""
        return self._config
    
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
        
        # 从配置中读取各级别参数
        level_1_cfg = self._config.get("level_1", DEFAULT_TRAILING_CONFIG["level_1"])
        level_2_cfg = self._config.get("level_2", DEFAULT_TRAILING_CONFIG["level_2"])
        level_3_cfg = self._config.get("level_3", DEFAULT_TRAILING_CONFIG["level_3"])
        
        l1_min = level_1_cfg.get("profit_min", 2.5)
        l1_max = level_1_cfg.get("profit_max", 5.0)
        l1_lock = level_1_cfg.get("lock_profit", 0)
        
        l2_min = level_2_cfg.get("profit_min", 5.0)
        l2_max = level_2_cfg.get("profit_max", 10.0)
        l2_lock = level_2_cfg.get("lock_profit", 3.0)
        
        l3_min = level_3_cfg.get("profit_min", 10.0)
        l3_lock = level_3_cfg.get("lock_profit", 5.0)
        l3_trailing = level_3_cfg.get("trailing_enabled", True)
        l3_trailing_pct = level_3_cfg.get("trailing_percent", 3.0)
        
        # 级别1: 保本止损
        if l1_min <= profit_percent < (l1_max or float('inf')) and current_level < 1:
            if l1_lock == 0:
                new_stop_price = position.entry_price
                locked_profit = 0.0
                adjust_reason = "盈利保护 - 止损提至成本价"
                adjust_detail = f"当前价格变动{profit_percent:.2f}%（触发阈值{l1_min}%），止损从{position.stop_loss_price:.6f}提升至成本价{position.entry_price:.6f}，确保不亏损"
            else:
                if position.side == "LONG":
                    new_stop_price = position.entry_price * (1 + l1_lock / 100)
                else:
                    new_stop_price = position.entry_price * (1 - l1_lock / 100)
                locked_profit = l1_lock
                adjust_reason = f"盈利保护 - 锁定{l1_lock}%利润"
                adjust_detail = f"当前价格变动{profit_percent:.2f}%（触发阈值{l1_min}%），锁定{l1_lock}%利润，止损价设为{new_stop_price:.6f}"
            new_level = 1
            logger.info(f"[{symbol}] 触发级别1: 价格变动{profit_percent:.2f}%，止损设为 {new_stop_price}")
        
        # 级别2: 锁定利润
        elif l2_min <= profit_percent < (l2_max or float('inf')) and current_level < 2:
            if position.side == "LONG":
                new_stop_price = position.entry_price * (1 + l2_lock / 100)
            else:
                new_stop_price = position.entry_price * (1 - l2_lock / 100)
            new_level = 2
            locked_profit = l2_lock
            adjust_reason = f"锁定利润 - 保护{l2_lock}%价格收益"
            adjust_detail = f"当前价格变动{profit_percent:.2f}%（触发阈值{l2_min}%），锁定{l2_lock}%价格利润，止损价设为{new_stop_price:.6f}"
            logger.info(f"[{symbol}] 触发级别2: 价格变动{profit_percent:.2f}%，锁定{l2_lock}%利润，止损价 {new_stop_price}")
        
        # 级别3: 追踪止损
        elif profit_percent >= l3_min and current_level < 3:
            if position.side == "LONG":
                new_stop_price = position.entry_price * (1 + l3_lock / 100)
            else:
                new_stop_price = position.entry_price * (1 - l3_lock / 100)
            new_level = 3
            is_trailing = l3_trailing
            locked_profit = l3_lock
            trailing_desc = f"并启动追踪止损模式（回撤{l3_trailing_pct}%触发）" if l3_trailing else ""
            adjust_reason = f"{'启动追踪止损' if l3_trailing else '锁定利润'} - 锁定{l3_lock}%价格收益"
            adjust_detail = f"当前价格变动{profit_percent:.2f}%（触发阈值{l3_min}%），锁定{l3_lock}%价格利润{trailing_desc}，止损价设为{new_stop_price:.6f}"
            logger.info(f"[{symbol}] 触发级别3: 价格变动{profit_percent:.2f}%，锁定{l3_lock}%利润{'并启动追踪止损' if l3_trailing else ''}，止损价 {new_stop_price}")
        
        # 追踪止损逻辑
        if is_trailing and current_level >= 3:
            trailing_percent = l3_trailing_pct
            
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
                api = get_exchange_api()
                
                for position in positions:
                    if position.status != "OPEN":
                        continue
                    
                    try:
                        current_price = await api.get_current_price(position.symbol)
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
            api = get_exchange_api()
            exchange_positions = await api.get_position()
            exchange_symbols = {p.symbol for p in exchange_positions}
            
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
        
        # 加载配置
        await self.load_config()
        
        # 注册配置变更监听
        config_manager.add_observer(self.on_config_change)
        
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
