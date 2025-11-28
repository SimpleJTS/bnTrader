"""
Hyperliquid WebSocket管理模块
负责K线数据订阅、健康检查和自动重连
实现ExchangeWebSocket抽象接口
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Dict, Callable, Optional, List, Any
import websockets
from websockets.exceptions import ConnectionClosed

from app.config import settings, config_manager
from app.services.exchange_interface import ExchangeWebSocket, ExchangeType, KlineData

logger = logging.getLogger(__name__)

# Hyperliquid WebSocket URLs
HYPERLIQUID_WS_MAINNET = "wss://api.hyperliquid.xyz/ws"
HYPERLIQUID_WS_TESTNET = "wss://api.hyperliquid-testnet.xyz/ws"


class HyperliquidWebSocket(ExchangeWebSocket):
    """Hyperliquid WebSocket管理器"""
    
    def __init__(self):
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._subscriptions: Dict[str, str] = {}  # {symbol: interval}
        self._callbacks: List[Callable] = []
        self._last_message_time: Dict[str, datetime] = {}
        self._running = False
        self._reconnect_count = 0
        self._start_time: Optional[datetime] = None
        self._health_check_task: Optional[asyncio.Task] = None
        self._message_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
    
    @property
    def exchange_type(self) -> ExchangeType:
        return ExchangeType.HYPERLIQUID
    
    @property
    def ws_url(self) -> str:
        """获取WebSocket URL"""
        return HYPERLIQUID_WS_TESTNET if settings.HYPERLIQUID_TESTNET else HYPERLIQUID_WS_MAINNET
    
    def _get_coin_name(self, symbol: str) -> str:
        """将交易对转换为Hyperliquid的币种名称"""
        if symbol.endswith("USDT"):
            return symbol[:-4]
        return symbol
    
    def add_callback(self, callback: Callable[[KlineData], Any]):
        """添加K线数据回调"""
        if callback not in self._callbacks:
            self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable[[KlineData], Any]):
        """移除回调"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    async def _notify_callbacks(self, kline: KlineData):
        """通知所有回调"""
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(kline)
                else:
                    callback(kline)
            except Exception as e:
                logger.error(f"回调处理异常: {e}")
    
    def _parse_candle_data(self, data: dict, symbol: str, interval: str) -> KlineData:
        """解析K线数据"""
        candle = data.get("data", {})
        
        return KlineData(
            symbol=symbol,  # 保持原始格式（BTCUSDT）
            interval=interval,
            open_time=candle.get("t", 0),
            close_time=candle.get("t", 0) + self._interval_to_ms(interval) - 1,
            open_price=float(candle.get("o", 0)),
            high_price=float(candle.get("h", 0)),
            low_price=float(candle.get("l", 0)),
            close_price=float(candle.get("c", 0)),
            volume=float(candle.get("v", 0)),
            is_closed=candle.get("s", "") == "close"  # 根据状态判断是否收盘
        )
    
    def _interval_to_ms(self, interval: str) -> int:
        """将时间周期转换为毫秒"""
        unit = interval[-1]
        value = int(interval[:-1])
        
        if unit == 'm':
            return value * 60 * 1000
        elif unit == 'h':
            return value * 60 * 60 * 1000
        elif unit == 'd':
            return value * 24 * 60 * 60 * 1000
        elif unit == 'w':
            return value * 7 * 24 * 60 * 60 * 1000
        return 60 * 1000
    
    async def subscribe(self, symbol: str, interval: str):
        """订阅交易对的K线"""
        async with self._lock:
            coin = self._get_coin_name(symbol)
            
            if symbol in self._subscriptions:
                old_interval = self._subscriptions[symbol]
                if old_interval == interval:
                    logger.info(f"[{symbol}] 已订阅相同周期 {interval}，跳过重复订阅")
                    return
                
                # interval变化，先取消旧订阅
                logger.info(f"[{symbol}] 周期从 {old_interval} 变更为 {interval}，重新订阅")
                if self._ws and self._ws.open:
                    unsubscribe_msg = {
                        "method": "unsubscribe",
                        "subscription": {
                            "type": "candle",
                            "coin": coin,
                            "interval": old_interval
                        }
                    }
                    await self._ws.send(json.dumps(unsubscribe_msg))
                    await asyncio.sleep(0.2)
            
            self._subscriptions[symbol] = interval
            
            if self._ws and self._ws.open:
                subscribe_msg = {
                    "method": "subscribe",
                    "subscription": {
                        "type": "candle",
                        "coin": coin,
                        "interval": interval
                    }
                }
                await self._ws.send(json.dumps(subscribe_msg))
                logger.info(f"[{symbol}] 已订阅 {interval} K线")
    
    async def unsubscribe(self, symbol: str):
        """取消订阅"""
        async with self._lock:
            if symbol not in self._subscriptions:
                return
            
            interval = self._subscriptions.pop(symbol)
            coin = self._get_coin_name(symbol)
            
            if self._ws and self._ws.open:
                unsubscribe_msg = {
                    "method": "unsubscribe",
                    "subscription": {
                        "type": "candle",
                        "coin": coin,
                        "interval": interval
                    }
                }
                await self._ws.send(json.dumps(unsubscribe_msg))
                logger.info(f"[{symbol}] 已取消订阅")
    
    async def _connect(self) -> bool:
        """建立WebSocket连接"""
        try:
            self._ws = await websockets.connect(
                self.ws_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5
            )
            self._start_time = datetime.utcnow()
            logger.info(f"Hyperliquid WebSocket 已连接: {self.ws_url}")
            
            # 连接成功后，逐个订阅已有的交易对
            if self._subscriptions:
                await self._subscribe_all()
            
            return True
        except Exception as e:
            logger.error(f"Hyperliquid WebSocket 连接失败: {e}")
            return False
    
    async def _subscribe_all(self):
        """订阅所有已保存的交易对"""
        if not self._ws or not self._ws.open:
            return
        
        for symbol, interval in list(self._subscriptions.items()):
            coin = self._get_coin_name(symbol)
            subscribe_msg = {
                "method": "subscribe",
                "subscription": {
                    "type": "candle",
                    "coin": coin,
                    "interval": interval
                }
            }
            try:
                await self._ws.send(json.dumps(subscribe_msg))
                logger.info(f"[{symbol}] 已重新订阅 {interval} K线")
                await asyncio.sleep(0.2)  # 避免请求过快
            except Exception as e:
                logger.error(f"[{symbol}] 订阅失败: {e}")
    
    async def _reconnect(self):
        """重连"""
        self._reconnect_count += 1
        logger.warning(f"正在重连 Hyperliquid WebSocket... (第{self._reconnect_count}次尝试)")
        
        if self._ws:
            try:
                await self._ws.close()
            except:
                pass
            self._ws = None
        
        wait_time = min(10 * self._reconnect_count, 60)
        logger.info(f"等待 {wait_time} 秒后重连...")
        await asyncio.sleep(wait_time)
        
        if await self._connect():
            self._reconnect_count = 0
            return True
        return False
    
    async def _message_handler(self):
        """消息处理循环"""
        while self._running:
            try:
                if not self._ws or not self._ws.open:
                    logger.warning("Hyperliquid WebSocket 未连接或已关闭，触发重连")
                    await self._reconnect()
                    continue
                
                message = await asyncio.wait_for(self._ws.recv(), timeout=30)
                data = json.loads(message)
                
                # 处理订阅确认
                if data.get("channel") == "subscriptionResponse":
                    if data.get("data", {}).get("method") == "subscribe":
                        logger.debug(f"订阅确认: {data}")
                    continue
                
                # 处理K线数据
                if data.get("channel") == "candle":
                    candle_data = data.get("data", {})
                    coin = candle_data.get("s", "")  # 币种名称
                    
                    # 查找对应的symbol和interval
                    for symbol, interval in self._subscriptions.items():
                        if self._get_coin_name(symbol) == coin:
                            kline = self._parse_candle_data(data, symbol, interval)
                            self._last_message_time[symbol] = datetime.utcnow()
                            await self._notify_callbacks(kline)
                            break
                
                # 处理错误
                if "error" in data:
                    logger.error(f"Hyperliquid WebSocket 错误: {data['error']}")
                
            except asyncio.TimeoutError:
                continue
            except ConnectionClosed as e:
                logger.warning(f"Hyperliquid WebSocket 连接已关闭: code={e.code}, reason={e.reason}")
                await self._reconnect()
            except json.JSONDecodeError as e:
                logger.error(f"JSON 解析错误: {e}")
            except Exception as e:
                logger.error(f"消息处理异常: {type(e).__name__}: {e}")
                await asyncio.sleep(1)
    
    async def _health_check(self):
        """健康检查任务"""
        from app.services.telegram import telegram_service
        
        while self._running:
            try:
                await asyncio.sleep(settings.WS_HEALTH_CHECK_INTERVAL)
                
                now = datetime.utcnow()
                
                # 检查每个订阅的最后消息时间
                for symbol in list(self._subscriptions.keys()):
                    last_time = self._last_message_time.get(symbol)
                    if last_time:
                        time_diff = (now - last_time).total_seconds()
                        if time_diff > settings.WS_NO_DATA_TIMEOUT:
                            msg = f"⚠️ Hyperliquid WebSocket {symbol} 超过{settings.WS_NO_DATA_TIMEOUT}秒无数据，正在重连..."
                            logger.warning(msg)
                            await telegram_service.send_message(msg)
                            await self._reconnect()
                            break
                
                # 检查是否需要全量重启
                if self._start_time:
                    running_hours = (now - self._start_time).total_seconds() / 3600
                    if running_hours >= settings.WS_FULL_RESTART_HOURS:
                        msg = f"🔄 Hyperliquid WebSocket 运行超过{settings.WS_FULL_RESTART_HOURS}小时，执行全量重启..."
                        logger.info(msg)
                        await telegram_service.send_message(msg)
                        await self._full_restart()
                
            except Exception as e:
                logger.error(f"健康检查异常: {e}")
    
    async def _full_restart(self):
        """全量重启WebSocket"""
        logger.info("开始全量重启 Hyperliquid WebSocket...")
        
        if self._ws:
            try:
                await self._ws.close()
            except:
                pass
            self._ws = None
        
        await asyncio.sleep(2)
        
        if await self._connect():
            logger.info("全量重启完成")
        else:
            logger.error("全量重启失败")
    
    async def start(self):
        """启动WebSocket服务"""
        if self._running:
            return
        
        self._running = True
        await self._connect()
        self._message_task = asyncio.create_task(self._message_handler())
        self._health_check_task = asyncio.create_task(self._health_check())
        logger.info("Hyperliquid WebSocket 服务已启动")
    
    async def stop(self):
        """停止WebSocket服务"""
        self._running = False
        
        if self._message_task:
            self._message_task.cancel()
            try:
                await self._message_task
            except asyncio.CancelledError:
                pass
        
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
        
        if self._ws:
            await self._ws.close()
        
        logger.info("Hyperliquid WebSocket 服务已停止")
    
    def get_status(self) -> dict:
        """获取WebSocket状态"""
        return {
            "connected": self._ws is not None and self._ws.open,
            "subscriptions": list(self._subscriptions.keys()),
            "reconnect_count": self._reconnect_count,
            "start_time": self._start_time.isoformat() if self._start_time else None,
            "last_message_times": {
                k: v.isoformat() for k, v in self._last_message_time.items()
            }
        }


# 全局实例
hyperliquid_ws = HyperliquidWebSocket()


# 配置变更监听器
async def on_hl_config_change(change_type: str, data: dict):
    """处理配置变更（Hyperliquid专用）"""
    # 只在当前交易所是Hyperliquid时处理
    if settings.EXCHANGE != "hyperliquid":
        return
    
    if change_type == "trading_pair_added":
        symbol = data.get("symbol")
        interval = data.get("interval", settings.DEFAULT_STRATEGY_INTERVAL)
        if symbol:
            await hyperliquid_ws.subscribe(symbol, interval)
    
    elif change_type == "trading_pair_removed":
        symbol = data.get("symbol")
        if symbol:
            await hyperliquid_ws.unsubscribe(symbol)
    
    elif change_type == "trading_pair_updated":
        symbol = data.get("symbol")
        is_active = data.get("is_active")
        interval = data.get("interval")
        
        if symbol:
            if is_active:
                await hyperliquid_ws.subscribe(symbol, interval)
                logger.info(f"[{symbol}] 配置已更新，周期: {interval}")
            else:
                await hyperliquid_ws.unsubscribe(symbol)
                logger.info(f"[{symbol}] 已停用，取消订阅")

# 注册配置变更监听
config_manager.add_observer(on_hl_config_change)
