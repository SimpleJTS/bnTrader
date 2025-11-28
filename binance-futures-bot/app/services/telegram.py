"""
Telegram服务模块
包含消息推送功能
"""
import logging
from app.config import settings, config_manager

logger = logging.getLogger(__name__)


class TelegramService:
    """Telegram服务 - 消息推送"""
    
    def __init__(self):
        self._bot = None
        self._initialized = False
    
    async def initialize(self):
        """初始化Telegram Bot"""
        if not settings.TG_BOT_TOKEN or not settings.TG_CHAT_ID:
            logger.warning("Telegram Bot Token 或 Chat ID 未配置")
            return False
        
        try:
            from telegram import Bot
            self._bot = Bot(token=settings.TG_BOT_TOKEN)
            self._initialized = True
            logger.info("Telegram Bot 已初始化")
            return True
        except Exception as e:
            logger.error(f"Telegram Bot 初始化失败: {e}")
            return False
    
    async def send_message(self, message: str, parse_mode: str = "Markdown"):
        """发送消息到Telegram"""
        if not self._initialized:
            await self.initialize()
        
        if not self._bot:
            logger.warning("Telegram Bot 未初始化，跳过消息发送")
            return False
        
        try:
            # 转义Markdown特殊字符
            # message = self._escape_markdown(message)
            await self._bot.send_message(
                chat_id=settings.TG_CHAT_ID,
                text=message,
                parse_mode=parse_mode
            )
            return True
        except Exception as e:
            logger.error(f"发送 Telegram 消息失败: {e}")
            # 尝试不使用parse_mode
            try:
                await self._bot.send_message(
                    chat_id=settings.TG_CHAT_ID,
                    text=message
                )
                return True
            except Exception as e2:
                logger.error(f"发送纯文本消息也失败: {e2}")
                return False
    
    def _escape_markdown(self, text: str) -> str:
        """转义Markdown特殊字符"""
        escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        for char in escape_chars:
            text = text.replace(char, f'\\{char}')
        return text


# 全局实例
telegram_service = TelegramService()


async def on_new_symbol_detected(symbol: str, change_percent: float):
    """当检测到新的符合条件的交易对时的处理函数"""
    from app.database import DatabaseManager
    from app.models import TradingPair
    from sqlalchemy import select
    
    logger.info(f"[{symbol}] 回调函数被调用，变化: {change_percent}%")
    
    session = await DatabaseManager.get_session()
    try:
        # 检查是否已存在
        result = await session.execute(
            select(TradingPair).where(TradingPair.symbol == symbol)
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            logger.info(f"[{symbol}] 交易对已存在（is_active={existing.is_active}），跳过添加")
            return
        
        logger.info(f"[{symbol}] 交易对不存在，准备添加...")
        
        # 添加新交易对
        new_pair = TradingPair(
            symbol=symbol,
            leverage=settings.DEFAULT_LEVERAGE,
            strategy_interval=settings.DEFAULT_STRATEGY_INTERVAL,
            stop_loss_percent=settings.DEFAULT_STOP_LOSS_PERCENT,
            is_active=True
        )
        session.add(new_pair)
        await session.commit()
        
        logger.info(f"[{symbol}] 已成功添加新交易对到数据库")
        
        # 通知配置变更
        await config_manager.notify_observers("trading_pair_added", {
            "symbol": symbol,
            "interval": settings.DEFAULT_STRATEGY_INTERVAL
        })
        logger.info(f"[{symbol}] 已通知观察者配置变更")
        
        # TG通知
        direction = "📈 涨幅" if change_percent > 0 else "📉 跌幅"
        msg = (
            f"🆕 **自动添加交易对**\n"
            f"交易对: {symbol}\n"
            f"24H变化: {direction} {abs(change_percent)}%\n"
            f"来源: TG频道监听"
        )
        await telegram_service.send_message(msg)
        
    except Exception as e:
        logger.error(f"[{symbol}] 添加新交易对失败: {e}", exc_info=True)
        await session.rollback()
    finally:
        await session.close()
