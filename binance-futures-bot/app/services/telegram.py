"""
Telegram服务模块
包含消息推送和频道监听功能
"""
import re
import asyncio
import logging
from typing import Optional, Callable, List
from datetime import datetime

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


class TelegramChannelListener:
    """Telegram频道监听器
    
    监听指定频道获取24H涨幅30%的交易币种
    """
    
    # 匹配规则: XXXUSDT ... 24H Price Change: ±XX%
    PATTERN = re.compile(r'([A-Z]{3,10}USDT).*?24H Price Change:\s*([+-]?\d+\.?\d*)%?', re.DOTALL)
    MIN_CHANGE_PERCENT = 30.0  # 最小涨幅
    
    def __init__(self):
        self._client = None
        self._running = False
        self._callbacks: List[Callable] = []
        self._listen_task: Optional[asyncio.Task] = None
    
    def add_callback(self, callback: Callable):
        """添加回调函数，当发现符合条件的币种时调用"""
        if callback not in self._callbacks:
            self._callbacks.append(callback)
    
    async def _notify_callbacks(self, symbol: str, change_percent: float):
        """通知回调"""
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(symbol, change_percent)
                else:
                    callback(symbol, change_percent)
            except Exception as e:
                logger.error(f"频道监听器回调异常: {e}")
    
    async def initialize(self) -> bool:
        """初始化Telethon客户端"""
        if not settings.TG_API_ID or not settings.TG_API_HASH:
            logger.warning("Telegram API 凭据未配置，无法启用频道监听")
            return False
        
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            
            # 使用用户提供的session文件
            self._client = TelegramClient(
                'tgsession',  # 对应 tgsession.session 文件
                settings.TG_API_ID,
                settings.TG_API_HASH
            )
            
            await self._client.start()
            logger.info("Telethon 客户端已启动")
            return True
            
        except Exception as e:
            logger.error(f"Telethon 初始化失败: {e}")
            return False
    
    def parse_message(self, text: str) -> List[tuple]:
        """解析消息，提取符合条件的交易对
        
        Returns:
            List of (symbol, change_percent) tuples
        """
        results = []
        matches = self.PATTERN.findall(text)
        
        for match in matches:
            symbol = match[0]
            try:
                change_percent = float(match[1])
                # 关注24H价格变化绝对值超过30%的（涨跌都算）
                if abs(change_percent) >= self.MIN_CHANGE_PERCENT:
                    results.append((symbol, change_percent))
                    direction = "涨幅" if change_percent > 0 else "跌幅"
                    logger.info(f"[{symbol}] 发现符合条件的交易对，{direction} {abs(change_percent)}%")
            except ValueError:
                continue
        
        return results
    
    async def _listen_loop(self):
        """监听循环"""
        from telethon import events
        
        # 获取频道实体
        channel = settings.TG_CHANNEL
        if channel.startswith('https://t.me/'):
            channel = channel.replace('https://t.me/', '@')
        
        try:
            entity = await self._client.get_entity(channel)
            logger.info(f"正在监听频道: {channel}")
        except Exception as e:
            logger.error(f"获取频道实体失败: {e}")
            return
        
        @self._client.on(events.NewMessage(chats=entity))
        async def handler(event):
            try:
                text = event.message.text or ""
                results = self.parse_message(text)
                
                for symbol, change_percent in results:
                    await self._notify_callbacks(symbol, change_percent)
                    
            except Exception as e:
                logger.error(f"消息处理异常: {e}")
        
        # 保持运行
        while self._running:
            await asyncio.sleep(1)
    
    async def start(self):
        """启动监听"""
        if self._running:
            return
        
        if not await self.initialize():
            return
        
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("频道监听器已启动")
    
    async def stop(self):
        """停止监听"""
        self._running = False
        
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        
        if self._client:
            await self._client.disconnect()
        
        logger.info("频道监听器已停止")


# 全局实例
telegram_service = TelegramService()
channel_listener = TelegramChannelListener()


async def on_new_symbol_detected(symbol: str, change_percent: float):
    """当检测到新的符合条件的交易对时的处理函数"""
    from app.database import DatabaseManager
    from app.models import TradingPair
    from sqlalchemy import select
    
    session = await DatabaseManager.get_session()
    try:
        # 检查是否已存在
        result = await session.execute(
            select(TradingPair).where(TradingPair.symbol == symbol)
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            logger.info(f"[{symbol}] 交易对已存在，跳过添加")
            return
        
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
        
        logger.info(f"[{symbol}] 已添加新交易对")
        
        # 通知配置变更
        await config_manager.notify_observers("trading_pair_added", {
            "symbol": symbol,
            "interval": settings.DEFAULT_STRATEGY_INTERVAL
        })
        
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
        logger.error(f"[{symbol}] 添加新交易对失败: {e}")
        await session.rollback()
    finally:
        await session.close()


# 注册回调
channel_listener.add_callback(on_new_symbol_detected)
