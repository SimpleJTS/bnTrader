"""
Telegram OI频道监控模块
监听指定频道获取24H价格变化超阈值的交易币种
使用独立线程运行，不阻塞主程序
"""
import asyncio
import re
import os
import threading
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class OIMonitor:
    """OI频道监控器
    
    监听Telegram频道，解析24H价格变化信息，
    当价格变化绝对值超过阈值时自动添加交易对
    """
    
    # 匹配规则: XXXUSDT ... 24H Price Change: ±XX%
    PATTERN = re.compile(r'([A-Z]{3,10}USDT).*?24H Price Change:\s*([+-]?\d+\.?\d*)%?', re.DOTALL)
    
    def __init__(self):
        self._client = None
        self._running = False
        self._thread = None
        self._loop = None
    
    def _get_settings(self):
        """延迟导入配置，避免循环导入"""
        from app.config import settings
        return settings
    
    async def _process_message(self, text: str):
        """处理频道消息"""
        from app.services.telegram import on_new_symbol_detected
        
        settings = self._get_settings()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        logger.info(f"[{now}] OI频道新消息，长度: {len(text)}")
        logger.debug(f"消息预览: {text[:200]}...")
        
        # 使用正则匹配所有交易对
        matches = self.PATTERN.findall(text)
        
        if not matches:
            if "USDT" in text:
                logger.debug("消息包含USDT但正则未匹配，可能格式不同")
            return
        
        for match in matches:
            symbol = match[0]
            try:
                change_percent = float(match[1])
            except ValueError:
                continue
            
            logger.info(f"[{symbol}] 解析到24H价格变化: {change_percent}%")
            
            # 使用绝对值判断是否超过阈值
            if abs(change_percent) >= settings.MIN_PRICE_CHANGE_PERCENT:
                direction = "涨幅" if change_percent > 0 else "跌幅"
                logger.info(f"[{symbol}] {direction} {abs(change_percent)}% >= 阈值 {settings.MIN_PRICE_CHANGE_PERCENT}% → 自动添加")
                
                # 调用回调函数添加交易对
                try:
                    await on_new_symbol_detected(symbol, change_percent)
                except Exception as e:
                    logger.error(f"[{symbol}] 添加交易对失败: {e}")
            else:
                logger.debug(f"[{symbol}] 变化 {change_percent}% < 阈值 {settings.MIN_PRICE_CHANGE_PERCENT}% → 忽略")
    
    async def _run_client(self):
        """运行Telethon客户端"""
        from telethon import TelegramClient, events
        
        settings = self._get_settings()
        
        # 检查配置
        if not settings.TG_API_ID or not settings.TG_API_HASH:
            logger.warning("TG_API_ID 或 TG_API_HASH 未配置，无法启动OI监控")
            return
        
        # Session路径
        session_path = '/app/data/tgsession'
        session_file = session_path + '.session'
        
        if not os.path.exists(session_file):
            logger.error(f"Telethon session文件不存在: {session_file}")
            logger.info("请先在本地完成Telegram登录授权，生成session文件后复制到容器")
            return
        
        try:
            self._client = TelegramClient(session_path, settings.TG_API_ID, settings.TG_API_HASH)
            await self._client.connect()
            
            if not await self._client.is_user_authorized():
                logger.error("Telethon session 未授权或已过期")
                await self._client.disconnect()
                return
            
            # 解析频道
            channel = settings.TG_CHANNEL
            if channel.startswith('https://t.me/'):
                channel = channel.replace('https://t.me/', '@')
            
            try:
                entity = await self._client.get_entity(channel)
                logger.info(f"正在监听频道: {channel} (ID: {entity.id})")
            except Exception as e:
                logger.error(f"获取频道实体失败: {e}")
                await self._client.disconnect()
                return
            
            # 注册消息处理器
            monitor = self
            
            @self._client.on(events.NewMessage(chats=entity))
            async def handler(event):
                text = event.message.text or ""
                if text:
                    await monitor._process_message(text)
            
            logger.info(f"【OI监控】已启动，正在监听 {channel}")
            logger.info(f"规则：24H Price Change 绝对值 ≥ {settings.MIN_PRICE_CHANGE_PERCENT}% 自动添加交易对")
            
            # 保持运行
            await self._client.run_until_disconnected()
            
        except Exception as e:
            logger.error(f"OI监控运行异常: {e}", exc_info=True)
        finally:
            if self._client:
                try:
                    await self._client.disconnect()
                except:
                    pass
    
    def _thread_target(self):
        """线程入口函数"""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_client())
        except Exception as e:
            logger.error(f"OI监控线程异常: {e}")
        finally:
            self._loop.close()
    
    # ================= 对外接口 =================
    
    def start(self):
        """启动监控（后台线程，不阻塞主程序）"""
        if self._running:
            logger.warning("OI监控已在运行中")
            return
        
        settings = self._get_settings()
        
        # 检查配置
        if not settings.TG_API_ID or not settings.TG_API_HASH:
            logger.warning("TG_API_ID 或 TG_API_HASH 未配置，跳过OI监控启动")
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._thread_target, daemon=True, name="OIMonitor")
        self._thread.start()
        logger.info("OI监控线程已启动")
    
    def stop(self):
        """停止监控"""
        if not self._running:
            return
        
        self._running = False
        
        # 断开客户端连接
        if self._client and self._loop:
            try:
                # 在事件循环中执行断开操作
                asyncio.run_coroutine_threadsafe(self._client.disconnect(), self._loop)
            except Exception as e:
                logger.debug(f"断开OI监控连接: {e}")
        
        logger.info("【OI监控】已停止")
    
    def is_running(self) -> bool:
        """检查监控是否运行中"""
        return self._running and (self._thread is not None and self._thread.is_alive())


# 全局实例
oi_monitor = OIMonitor()
