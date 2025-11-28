"""
币安WebSocket管理模块
负责K线数据订阅、健康检查和自动重连
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Set, Callable, Optional, List
import websockets
from websockets.exceptions import ConnectionClosed

from app.config import settings, config_manager

logger = logging.getLogger(__name__)


class KlineData:
    """K线数据结构"""
    def __init__(self, data: dict):
        k = data.get("k", {})
        self.symbol = k.get("s", "")
        self.interval = k.get("i", "")
        self.open_time = k.get("t", 0)
        self.close_time = k.get("T", 0)
        self.open_price = float(k.get("o", 0))
        self.high_price = float(k.get("h", 0))
        self.low_price = float(k.get("l", 0))
        self.close_price = float(k.get("c", 0))
        self.volume = float(k.get("v", 0))
        self.is_closed = k.get("x", False)  # K线是否已收盘
    
    def to_dict(self):
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "open_time": self.open_time,
            "close_time": self.close_time,
            "open": self.open_price,
            "high": self.high_price,
            "low": self.low_price,
            "close": self.close_price,
            "volume": self.volume,
            "is_closed": self.is_closed
        }


class BinanceWebSocket:
    """币安WebSocket管理器"""
    
    WS_BASE_URL = "wss://fstream.binance.com"
    TESTNET_WS_URL = "wss://stream.binancefuture.com"
    
    def __init__(self):
        self.base_url = self.TESTNET_WS_URL if settings.BINANCE_TESTNET else self.WS_BASE_URL
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
    
    def add_callback(self, callback: Callable):
        """添加K线数据回调"""
        if callback not in self._callbacks:
            self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable):
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
    
    def _build_stream_name(self, symbol: str, interval: str) -> str:
        """构建stream名称"""
        return f"{symbol.lower()}@kline_{interval}"
    
    async def subscribe(self, symbol: str, interval: str):
        """订阅交易对的K线"""
        async with self._lock:
            stream_name = self._build_stream_name(symbol, interval)
            if symbol in self._subscriptions:
                logger.info(f"[{symbol}] 已订阅，跳过重复订阅")
                return
            
            self._subscriptions[symbol] = interval
            
            if self._ws and self._ws.open:
                # 发送订阅消息
                subscribe_msg = {
                    "method": "SUBSCRIBE",
                    "params": [stream_name],
                    "id": int(time.time() * 1000)
                }
                await self._ws.send(json.dumps(subscribe_msg))
                logger.info(f"[{symbol}] 已订阅 {interval} K线")
    
    async def unsubscribe(self, symbol: str):
        """取消订阅"""
        async with self._lock:
            if symbol not in self._subscriptions:
                return
            
            interval = self._subscriptions.pop(symbol)
            stream_name = self._build_stream_name(symbol, interval)
            
            if self._ws and self._ws.open:
                unsubscribe_msg = {
                    "method": "UNSUBSCRIBE",
                    "params": [stream_name],
                    "id": int(time.time() * 1000)
                }
                await self._ws.send(json.dumps(unsubscribe_msg))
                logger.info(f"[{symbol}] 已取消订阅")
    
    async def _connect(self, include_streams: bool = False):
        """建立WebSocket连接
        
        Args:
            include_streams: 是否在URL中包含streams（首次连接时不包含，避免无效交易对导致连接失败）
        """
        # 始终先建立空连接，然后通过SUBSCRIBE消息订阅
        # 这样即使某些交易对无效，也不会影响整个连接
        url = f"{self.base_url}/ws"
        
        try:
            self._ws = await websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5
            )
            self._start_time = datetime.utcnow()
            logger.info(f"WebSocket 已连接: {url}")
            
            # 连接成功后，逐个订阅已有的交易对
            if self._subscriptions:
                await self._subscribe_all()
            
            return True
        except Exception as e:
            logger.error(f"WebSocket 连接失败: {e}")
            return False
    
    async def _subscribe_all(self):
        """订阅所有已保存的交易对"""
        if not self._ws or not self._ws.open:
            return
        
        for symbol, interval in list(self._subscriptions.items()):
            stream_name = self._build_stream_name(symbol, interval)
            subscribe_msg = {
                "method": "SUBSCRIBE",
                "params": [stream_name],
                "id": int(time.time() * 1000)
            }
            try:
                await self._ws.send(json.dumps(subscribe_msg))
                logger.info(f"[{symbol}] 已发送订阅请求: {interval}")
                # 添加小延迟，避免发送过快被限流
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"[{symbol}] 订阅失败: {e}")
    
    async def _reconnect(self):
        """重连"""
        self._reconnect_count += 1
        logger.warning(f"正在重连 WebSocket... (第{self._reconnect_count}次尝试)")
        
        # 关闭旧连接
        if self._ws:
            try:
                await self._ws.close()
            except:
                pass
            self._ws = None
        
        # 等待后重连（指数退避，最多60秒）
        wait_time = min(5 * self._reconnect_count, 60)
        logger.info(f"等待 {wait_time} 秒后重连...")
        await asyncio.sleep(wait_time)
        
        # _connect 会自动调用 _subscribe_all 重新订阅
        if await self._connect():
            self._reconnect_count = 0  # 重连成功后重置计数
            return True
        return False
    
    async def _message_handler(self):
        """消息处理循环"""
        while self._running:
            try:
                if not self._ws or not self._ws.open:
                    logger.warning("WebSocket 未连接或已关闭，触发重连")
                    await self._reconnect()
                    continue
                
                message = await asyncio.wait_for(self._ws.recv(), timeout=30)
                data = json.loads(message)
                
                # 处理订阅响应消息
                if "result" in data and "id" in data:
                    # 订阅成功响应：{"result": null, "id": xxx}
                    if data["result"] is None:
                        logger.debug(f"订阅响应成功: id={data['id']}")
                    else:
                        logger.warning(f"订阅响应: {data}")
                    continue
                
                # 处理错误消息
                if "error" in data:
                    error_msg = data.get("error", {})
                    logger.error(f"WebSocket 错误: code={error_msg.get('code')}, msg={error_msg.get('msg')}")
                    continue
                
                # 处理stream消息
                if "stream" in data and "data" in data:
                    stream_data = data["data"]
                    if stream_data.get("e") == "kline":
                        kline = KlineData(stream_data)
                        self._last_message_time[kline.symbol] = datetime.utcnow()
                        await self._notify_callbacks(kline)
                
                # 处理单独的kline消息
                elif data.get("e") == "kline":
                    kline = KlineData(data)
                    self._last_message_time[kline.symbol] = datetime.utcnow()
                    await self._notify_callbacks(kline)
                
            except asyncio.TimeoutError:
                # 30秒没收到消息是正常的，继续等待
                continue
            except ConnectionClosed as e:
                logger.warning(f"WebSocket 连接已关闭: code={e.code}, reason={e.reason}")
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
                            msg = f"⚠️ WebSocket {symbol} 超过{settings.WS_NO_DATA_TIMEOUT}秒无数据，正在重连..."
                            logger.warning(msg)
                            await telegram_service.send_message(msg)
                            await self._reconnect()
                            break
                
                # 检查是否需要全量重启 (每20小时)
                if self._start_time:
                    running_hours = (now - self._start_time).total_seconds() / 3600
                    if running_hours >= settings.WS_FULL_RESTART_HOURS:
                        msg = f"🔄 WebSocket 运行超过{settings.WS_FULL_RESTART_HOURS}小时，执行全量重启..."
                        logger.info(msg)
                        await telegram_service.send_message(msg)
                        await self._full_restart()
                
            except Exception as e:
                logger.error(f"健康检查异常: {e}")
    
    async def _full_restart(self):
        """全量重启WebSocket"""
        logger.info("开始全量重启 WebSocket...")
        
        # 关闭连接
        if self._ws:
            try:
                await self._ws.close()
            except:
                pass
            self._ws = None
        
        # 等待一小段时间
        await asyncio.sleep(2)
        
        # 重新连接（_connect 会自动调用 _subscribe_all 重新订阅）
        if await self._connect():
            logger.info("全量重启完成")
        else:
            logger.error("全量重启失败")
    
    async def start(self):
        """启动WebSocket服务"""
        if self._running:
            return
        
        self._running = True
        
        # 连接
        await self._connect()
        
        # 启动消息处理任务
        self._message_task = asyncio.create_task(self._message_handler())
        
        # 启动健康检查任务
        self._health_check_task = asyncio.create_task(self._health_check())
        
        logger.info("WebSocket 服务已启动")
    
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
        
        logger.info("WebSocket 服务已停止")
    
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
binance_ws = BinanceWebSocket()


# 配置变更监听器
async def on_config_change(change_type: str, data: dict):
    """处理配置变更"""
    if change_type == "trading_pair_added":
        symbol = data.get("symbol")
        interval = data.get("interval", settings.DEFAULT_STRATEGY_INTERVAL)
        if symbol:
            await binance_ws.subscribe(symbol, interval)
    
    elif change_type == "trading_pair_removed":
        symbol = data.get("symbol")
        if symbol:
            await binance_ws.unsubscribe(symbol)
    
    elif change_type == "trading_pair_updated":
        symbol = data.get("symbol")
        is_active = data.get("is_active")
        interval = data.get("interval")
        
        if symbol:
            if is_active:
                await binance_ws.subscribe(symbol, interval)
            else:
                await binance_ws.unsubscribe(symbol)

# 注册配置变更监听
config_manager.add_observer(on_config_change)
