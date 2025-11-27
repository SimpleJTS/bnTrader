"""
币安API交易模块
处理所有与币安交易所的API交互
"""
import asyncio
import logging
from typing import Optional, Dict, List, Any
from decimal import Decimal, ROUND_DOWN
import httpx
import hmac
import hashlib
import time
from urllib.parse import urlencode

from app.config import settings

logger = logging.getLogger(__name__)


class BinanceAPI:
    """币安期货API客户端"""
    
    BASE_URL = "https://fapi.binance.com"
    TESTNET_URL = "https://testnet.binancefuture.com"
    
    def __init__(self):
        self._exchange_info: Dict = {}
        self._symbol_info: Dict[str, Dict] = {}
        self._client: Optional[httpx.AsyncClient] = None
    
    @property
    def api_key(self) -> str:
        """动态获取API Key，支持运行时配置更新"""
        return settings.BINANCE_API_KEY
    
    @property
    def api_secret(self) -> str:
        """动态获取API Secret，支持运行时配置更新"""
        return settings.BINANCE_API_SECRET
    
    @property
    def base_url(self) -> str:
        """动态获取API基础URL，支持运行时切换测试网/主网"""
        return self.TESTNET_URL if settings.BINANCE_TESTNET else self.BASE_URL
    
    async def get_client(self) -> httpx.AsyncClient:
        """获取HTTP客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client
    
    async def close(self):
        """关闭客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
    
    def _generate_signature(self, params: dict) -> str:
        """生成请求签名"""
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _get_headers(self) -> dict:
        """获取请求头"""
        return {
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/json"
        }
    
    async def _request(self, method: str, endpoint: str, params: dict = None, 
                       signed: bool = False) -> dict:
        """发送API请求"""
        client = await self.get_client()
        url = f"{self.base_url}{endpoint}"
        
        if params is None:
            params = {}
        
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["signature"] = self._generate_signature(params)
        
        try:
            if method.upper() == "GET":
                response = await client.get(url, params=params, headers=self._get_headers())
            elif method.upper() == "POST":
                response = await client.post(url, params=params, headers=self._get_headers())
            elif method.upper() == "DELETE":
                response = await client.delete(url, params=params, headers=self._get_headers())
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Request error: {e}")
            raise
    
    async def get_exchange_info(self) -> dict:
        """获取交易所信息"""
        if not self._exchange_info:
            self._exchange_info = await self._request("GET", "/fapi/v1/exchangeInfo")
            # 缓存交易对信息
            for symbol_info in self._exchange_info.get("symbols", []):
                self._symbol_info[symbol_info["symbol"]] = symbol_info
        return self._exchange_info
    
    async def get_symbol_info(self, symbol: str) -> Optional[dict]:
        """获取交易对信息"""
        if not self._symbol_info:
            await self.get_exchange_info()
        return self._symbol_info.get(symbol)
    
    async def get_symbol_precision(self, symbol: str) -> tuple:
        """获取交易对精度 (价格精度, 数量精度)"""
        info = await self.get_symbol_info(symbol)
        if not info:
            return (8, 8)  # 默认精度
        
        price_precision = info.get("pricePrecision", 8)
        quantity_precision = info.get("quantityPrecision", 8)
        
        # 获取过滤器中的最小数量
        min_qty = 0.001
        step_size = 0.001
        for f in info.get("filters", []):
            if f["filterType"] == "LOT_SIZE":
                min_qty = float(f["minQty"])
                step_size = float(f["stepSize"])
                break
        
        return (price_precision, quantity_precision, min_qty, step_size)
    
    def round_quantity(self, quantity: float, precision: int, step_size: float) -> float:
        """按精度舍入数量"""
        # 使用step_size进行对齐
        decimal_places = len(str(step_size).split('.')[-1].rstrip('0'))
        quantity = Decimal(str(quantity))
        step = Decimal(str(step_size))
        rounded = (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
        return float(rounded)
    
    def round_price(self, price: float, precision: int) -> float:
        """按精度舍入价格"""
        return round(price, precision)
    
    async def get_account_balance(self) -> Dict[str, float]:
        """获取账户余额"""
        result = await self._request("GET", "/fapi/v2/balance", signed=True)
        balances = {}
        for item in result:
            asset = item["asset"]
            balances[asset] = {
                "balance": float(item["balance"]),
                "available": float(item["availableBalance"]),
                "unrealized_pnl": float(item.get("crossUnPnl", 0))
            }
        return balances
    
    async def get_usdt_balance(self) -> float:
        """获取USDT可用余额"""
        balances = await self.get_account_balance()
        usdt_info = balances.get("USDT", {})
        return usdt_info.get("available", 0.0)
    
    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        """设置杠杆"""
        params = {
            "symbol": symbol,
            "leverage": leverage
        }
        return await self._request("POST", "/fapi/v1/leverage", params, signed=True)
    
    async def set_margin_type(self, symbol: str, margin_type: str = "CROSSED") -> dict:
        """设置保证金模式 (CROSSED/ISOLATED)"""
        params = {
            "symbol": symbol,
            "marginType": margin_type
        }
        try:
            return await self._request("POST", "/fapi/v1/marginType", params, signed=True)
        except httpx.HTTPStatusError as e:
            # 如果已经是该模式，忽略错误
            if "No need to change margin type" in str(e.response.text):
                return {"msg": "Already in this margin type"}
            raise
    
    async def get_position(self, symbol: str = None) -> List[dict]:
        """获取持仓信息"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        result = await self._request("GET", "/fapi/v2/positionRisk", params, signed=True)
        # 只返回有持仓的
        return [p for p in result if float(p.get("positionAmt", 0)) != 0]
    
    async def get_current_price(self, symbol: str) -> float:
        """获取当前价格"""
        result = await self._request("GET", "/fapi/v1/ticker/price", {"symbol": symbol})
        return float(result["price"])
    
    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> List[list]:
        """获取K线数据"""
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
        return await self._request("GET", "/fapi/v1/klines", params)
    
    async def place_market_order(self, symbol: str, side: str, quantity: float, 
                                  reduce_only: bool = False) -> dict:
        """下市价单
        
        Args:
            symbol: 交易对
            side: BUY/SELL
            quantity: 数量
            reduce_only: 是否仅减仓
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": quantity
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        
        return await self._request("POST", "/fapi/v1/order", params, signed=True)
    
    async def place_stop_loss_order(self, symbol: str, side: str, quantity: float,
                                     stop_price: float, close_position: bool = False) -> dict:
        """下止损单
        
        Args:
            symbol: 交易对
            side: BUY(空头止损)/SELL(多头止损)
            quantity: 数量
            stop_price: 触发价格
            close_position: 是否平全部仓位
        """
        price_precision, qty_precision, _, step_size = await self.get_symbol_precision(symbol)
        
        params = {
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "stopPrice": self.round_price(stop_price, price_precision),
            "timeInForce": "GTE_GTC"
        }
        
        if close_position:
            params["closePosition"] = "true"
        else:
            params["quantity"] = self.round_quantity(quantity, qty_precision, step_size)
            params["reduceOnly"] = "true"
        
        return await self._request("POST", "/fapi/v1/order", params, signed=True)
    
    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        """取消订单"""
        params = {
            "symbol": symbol,
            "orderId": order_id
        }
        return await self._request("DELETE", "/fapi/v1/order", params, signed=True)
    
    async def cancel_all_orders(self, symbol: str) -> dict:
        """取消某个交易对的所有订单"""
        params = {"symbol": symbol}
        return await self._request("DELETE", "/fapi/v1/allOpenOrders", params, signed=True)
    
    async def get_open_orders(self, symbol: str = None) -> List[dict]:
        """获取当前挂单"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        return await self._request("GET", "/fapi/v1/openOrders", params, signed=True)
    
    async def calculate_order_quantity(self, symbol: str, leverage: int, 
                                        position_percent: float = None) -> float:
        """计算下单数量
        
        根据账户余额、杠杆和仓位比例计算下单数量
        """
        if position_percent is None:
            position_percent = settings.POSITION_SIZE_PERCENT
        
        # 获取USDT余额
        usdt_balance = await self.get_usdt_balance()
        
        # 计算可用资金 (余额的position_percent%)
        available_funds = usdt_balance * (position_percent / 100)
        
        # 获取当前价格
        current_price = await self.get_current_price(symbol)
        
        # 计算数量 (资金 * 杠杆 / 价格)
        quantity = (available_funds * leverage) / current_price
        
        # 获取精度并舍入
        price_precision, qty_precision, min_qty, step_size = await self.get_symbol_precision(symbol)
        quantity = self.round_quantity(quantity, qty_precision, step_size)
        
        # 确保不小于最小数量
        if quantity < min_qty:
            logger.warning(f"Calculated quantity {quantity} is less than min_qty {min_qty}")
            return 0
        
        return quantity


# 全局实例
binance_api = BinanceAPI()
