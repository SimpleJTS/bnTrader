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
                logger.info(f"[{symbol}] 正在执行回调函数...")
                if asyncio.iscoroutinefunction(callback):
                    await callback(symbol, change_percent)
                else:
                    callback(symbol, change_percent)
                logger.info(f"[{symbol}] 回调函数执行完成")
            except Exception as e:
                logger.error(f"频道监听器回调异常: {e}", exc_info=True)
    
    async def initialize(self) -> bool:
        """初始化Telethon客户端"""
        if not settings.TG_API_ID or not settings.TG_API_HASH:
            logger.warning("Telegram API 凭据未配置，无法启用频道监听")
            return False
        
        try:
            from telethon import TelegramClient
            import os
            
            # session文件路径: /app/data/tgsession.session
            session_path = '/app/data/tgsession'
            session_file = session_path + '.session'
            
            if not os.path.exists(session_file):
                logger.error(f"Telethon session文件不存在: {session_file}")
                return False
            
            logger.info(f"使用 session 文件: {session_file}")
            self._client = TelegramClient(
                session_path,
                settings.TG_API_ID,
                settings.TG_API_HASH
            )
            
            # 只连接，不尝试交互式登录
            await self._client.connect()
            
            if not await self._client.is_user_authorized():
                logger.error("Telethon session 未授权或已过期")
                await self._client.disconnect()
                return False
            
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
        
        logger.debug(f"[TG频道] 正则匹配结果: {matches}")
        
        if not matches:
            # 尝试找出消息中是否有类似的内容但格式不同
            if "USDT" in text:
                logger.debug(f"[TG频道] 消息包含USDT但正则未匹配，可能格式不同")
            if "Price Change" in text or "price change" in text.lower():
                logger.debug(f"[TG频道] 消息包含Price Change相关内容但正则未匹配")
        
        for match in matches:
            symbol = match[0]
            try:
                change_percent = float(match[1])
                logger.info(f"[TG频道] 解析到: {symbol} 变化 {change_percent}%")
                
                # 关注24H价格变化绝对值超过30%的（涨跌都算）
                if abs(change_percent) >= self.MIN_CHANGE_PERCENT:
                    results.append((symbol, change_percent))
                    direction = "涨幅" if change_percent > 0 else "跌幅"
                    logger.info(f"[{symbol}] 发现符合条件的交易对，{direction} {abs(change_percent)}%")
                else:
                    logger.debug(f"[{symbol}] 变化 {change_percent}% 未达到阈值 {self.MIN_CHANGE_PERCENT}%")
            except ValueError as e:
                logger.warning(f"解析变化百分比失败: {match[1]}, 错误: {e}")
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
            logger.info(f"正在监听频道: {channel} (ID: {entity.id})")
        except Exception as e:
            logger.error(f"获取频道实体失败: {e}")
            return
        
        # 保存 self 引用供事件处理器使用
        listener = self
        
        async def handler(event):
            try:
                text = event.message.text or ""
                logger.info(f"[TG频道] 收到新消息，长度: {len(text)}")
                # 打印消息前200字符以便调试
                logger.info(f"[TG频道] 消息预览: {text[:200]}...")
                
                results = listener.parse_message(text)
                
                if results:
                    logger.info(f"[TG频道] 解析到 {len(results)} 个符合条件的交易对: {results}")
                    for symbol, change_percent in results:
                        await listener._notify_callbacks(symbol, change_percent)
                else:
                    logger.info(f"[TG频道] 消息中未发现符合条件的交易对")
                    
            except Exception as e:
                logger.error(f"消息处理异常: {e}", exc_info=True)
        
        # 使用 add_event_handler 而不是装饰器，确保正确注册
        self._client.add_event_handler(handler, events.NewMessage(chats=entity))
        logger.info("事件处理器已注册，开始监听消息...")
        
        # 启动时获取最近几条历史消息并处理
        try:
            logger.info("正在获取频道最近的历史消息...")
            async for message in self._client.iter_messages(entity, limit=5):
                if message.text:
                    logger.info(f"[TG历史] 消息时间: {message.date}, 长度: {len(message.text)}")
                    logger.info(f"[TG历史] 消息预览: {message.text[:300]}...")
                    
                    # 解析并处理历史消息
                    results = self.parse_message(message.text)
                    if results:
                        logger.info(f"[TG历史] 解析到 {len(results)} 个符合条件的交易对: {results}")
                        # 历史消息也触发回调，添加到交易对列表
                        for symbol, change_percent in results:
                            logger.info(f"[TG历史] 准备添加交易对: {symbol}")
                            await self._notify_callbacks(symbol, change_percent)
                    else:
                        logger.info(f"[TG历史] 消息中未发现符合条件的交易对")
        except Exception as e:
            logger.error(f"获取历史消息失败: {e}", exc_info=True)
        
        # 关键：使用 run_until_disconnected() 让 Telethon 正确接收更新
        # 这个方法会阻塞直到客户端断开连接
        try:
            logger.info("开始运行 Telethon 事件循环，等待新消息...")
            await self._client.run_until_disconnected()
        except asyncio.CancelledError:
            logger.info("监听任务被取消")
        except Exception as e:
            logger.error(f"Telethon 事件循环异常: {e}", exc_info=True)
    
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
        
        # 先断开客户端连接，这会让 run_until_disconnected() 返回
        if self._client:
            try:
                await self._client.disconnect()
                logger.info("Telethon 客户端已断开连接")
            except Exception as e:
                logger.error(f"断开 Telethon 连接时出错: {e}")
        
        # 然后取消监听任务
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        
        logger.info("频道监听器已停止")


# 全局实例
telegram_service = TelegramService()
channel_listener = TelegramChannelListener()


async def on_new_symbol_detected(symbol: str, change_percent: float):
    """当检测到新的符合条件的交易对时的处理函数"""
    from app.database import DatabaseManager
    from app.models import TradingPair
    from sqlalchemy import select
    
    logger.info(f"[{symbol}] ========== 回调函数开始执行 ==========")
    logger.info(f"[{symbol}] 变化幅度: {change_percent}%")
    
    session = None
    try:
        session = await DatabaseManager.get_session()
        logger.info(f"[{symbol}] 数据库会话已获取")
        
        # 检查是否已存在
        result = await session.execute(
            select(TradingPair).where(TradingPair.symbol == symbol)
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            logger.info(f"[{symbol}] 交易对已存在（id={existing.id}, is_active={existing.is_active}），跳过添加")
            return
        
        logger.info(f"[{symbol}] 交易对不存在，准备添加到数据库...")
        logger.info(f"[{symbol}] 配置: leverage={settings.DEFAULT_LEVERAGE}, interval={settings.DEFAULT_STRATEGY_INTERVAL}, stop_loss={settings.DEFAULT_STOP_LOSS_PERCENT}%")
        
        # 添加新交易对
        new_pair = TradingPair(
            symbol=symbol,
            leverage=settings.DEFAULT_LEVERAGE,
            strategy_interval=settings.DEFAULT_STRATEGY_INTERVAL,
            stop_loss_percent=settings.DEFAULT_STOP_LOSS_PERCENT,
            is_active=True
        )
        session.add(new_pair)
        logger.info(f"[{symbol}] 准备提交数据库事务...")
        
        await session.commit()
        await session.refresh(new_pair)
        
        logger.info(f"[{symbol}] ✓ 已成功添加新交易对到数据库 (id={new_pair.id})")
        
        # 通知配置变更
        await config_manager.notify_observers("trading_pair_added", {
            "symbol": symbol,
            "interval": settings.DEFAULT_STRATEGY_INTERVAL
        })
        logger.info(f"[{symbol}] ✓ 已通知观察者配置变更")
        
        # TG通知
        direction = "📈 涨幅" if change_percent > 0 else "📉 跌幅"
        msg = (
            f"🆕 **自动添加交易对**\n"
            f"交易对: {symbol}\n"
            f"24H变化: {direction} {abs(change_percent)}%\n"
            f"来源: TG频道监听"
        )
        await telegram_service.send_message(msg)
        logger.info(f"[{symbol}] ✓ 已发送Telegram通知")
        
    except Exception as e:
        logger.error(f"[{symbol}] ✗ 添加新交易对失败: {e}", exc_info=True)
        if session:
            await session.rollback()
            logger.info(f"[{symbol}] 数据库事务已回滚")
    finally:
        if session:
            await session.close()
            logger.info(f"[{symbol}] 数据库会话已关闭")
        logger.info(f"[{symbol}] ========== 回调函数执行结束 ==========")


# 注册回调
channel_listener.add_callback(on_new_symbol_detected)
