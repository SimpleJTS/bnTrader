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
    
    async def get_symbol_precision(self, symbol: str) -> dict:
        """获取交易对精度信息
        
        Returns:
            dict: {
                'price_precision': int,      # 价格小数位数
                'quantity_precision': int,   # 数量小数位数  
                'tick_size': str,            # 价格最小变动单位
                'step_size': str,            # 数量最小变动单位
                'min_qty': str,              # 最小下单数量
                'min_notional': str,         # 最小名义价值
            }
        """
        info = await self.get_symbol_info(symbol)
        if not info:
            logger.warning(f"Symbol {symbol} not found, using default precision")
            return {
                'price_precision': 8,
                'quantity_precision': 8,
                'tick_size': '0.00000001',
                'step_size': '0.00000001',
                'min_qty': '0.001',
                'min_notional': '5',
            }
        
        result = {
            'price_precision': info.get("pricePrecision", 8),
            'quantity_precision': info.get("quantityPrecision", 8),
            'tick_size': '0.00000001',
            'step_size': '0.00000001',
            'min_qty': '0.001',
            'min_notional': '5',
        }
        
        # 从过滤器获取精确的精度信息
        for f in info.get("filters", []):
            filter_type = f.get("filterType")
            if filter_type == "PRICE_FILTER":
                result['tick_size'] = f.get("tickSize", result['tick_size'])
            elif filter_type == "LOT_SIZE":
                result['step_size'] = f.get("stepSize", result['step_size'])
                result['min_qty'] = f.get("minQty", result['min_qty'])
            elif filter_type == "MIN_NOTIONAL":
                result['min_notional'] = f.get("notional", result['min_notional'])
        
        logger.debug(f"Symbol {symbol} precision: {result}")
        return result
    
    def round_step(self, value: float, step: str) -> Decimal:
        """按步长对齐数值（向下取整）
        
        Args:
            value: 原始数值
            step: 步长（如 "0.001", "0.00001"）
        
        Returns:
            对齐后的 Decimal 值
        """
        value_dec = Decimal(str(value))
        step_dec = Decimal(step)
        
        # 向下取整到步长的整数倍
        rounded = (value_dec / step_dec).to_integral_value(rounding=ROUND_DOWN) * step_dec
        return rounded
    
    def format_quantity(self, quantity: float, precision_info: dict) -> str:
        """格式化下单数量
        
        Args:
            quantity: 原始数量
            precision_info: get_symbol_precision 返回的精度信息
        
        Returns:
            格式化后的数量字符串
        """
        step_size = precision_info['step_size']
        rounded = self.round_step(quantity, step_size)
        
        # 计算小数位数
        if '.' in step_size:
            decimal_places = len(step_size.rstrip('0').split('.')[1]) if '.' in step_size.rstrip('0') else 0
        else:
            decimal_places = 0
        
        # 格式化为字符串，去除多余的0
        if decimal_places > 0:
            return f"{rounded:.{decimal_places}f}"
        else:
            return str(int(rounded))
    
    def format_price(self, price: float, precision_info: dict) -> str:
        """格式化下单价格
        
        Args:
            price: 原始价格
            precision_info: get_symbol_precision 返回的精度信息
        
        Returns:
            格式化后的价格字符串
        """
        tick_size = precision_info['tick_size']
        rounded = self.round_step(price, tick_size)
        
        # 使用 price_precision 来格式化
        precision = precision_info['price_precision']
        return f"{rounded:.{precision}f}".rstrip('0').rstrip('.')
    
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
        # 获取精度信息并格式化数量
        precision_info = await self.get_symbol_precision(symbol)
        formatted_qty = self.format_quantity(quantity, precision_info)
        
        if Decimal(formatted_qty) <= 0:
            raise ValueError(f"Invalid quantity: {quantity} -> {formatted_qty}")
        
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": formatted_qty
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        
        logger.info(f"Placing market order: {params}")
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
        # 获取精度信息
        precision_info = await self.get_symbol_precision(symbol)
        
        # 格式化止损价格
        formatted_price = self.format_price(stop_price, precision_info)
        if Decimal(formatted_price) <= 0:
            raise ValueError(f"Invalid stop price: {stop_price} -> {formatted_price} (tick_size={precision_info['tick_size']})")
        
        params = {
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "stopPrice": formatted_price,
            "timeInForce": "GTE_GTC"
        }
        
        if close_position:
            params["closePosition"] = "true"
        else:
            # 格式化数量
            formatted_qty = self.format_quantity(quantity, precision_info)
            min_qty = Decimal(precision_info['min_qty'])
            
            if Decimal(formatted_qty) <= 0:
                raise ValueError(f"Invalid quantity: {quantity} -> {formatted_qty} (step_size={precision_info['step_size']})")
            if Decimal(formatted_qty) < min_qty:
                raise ValueError(f"Quantity {formatted_qty} is less than minimum {min_qty}")
            
            params["quantity"] = formatted_qty
            params["reduceOnly"] = "true"
        
        logger.info(f"Placing stop loss order: {params}")
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
        
        # 获取精度信息
        precision_info = await self.get_symbol_precision(symbol)
        min_qty = Decimal(precision_info['min_qty'])
        min_notional = Decimal(precision_info['min_notional'])
        
        # 获取USDT余额
        usdt_balance = await self.get_usdt_balance()
        
        # 计算可用资金 (余额的position_percent%)
        available_funds = usdt_balance * (position_percent / 100)
        
        # 获取当前价格
        current_price = await self.get_current_price(symbol)
        
        if current_price <= 0:
            logger.error(f"Invalid current price for {symbol}: {current_price}")
            return 0
        
        # 计算数量 (资金 * 杠杆 / 价格)
        quantity = (available_funds * leverage) / current_price
        
        # 按精度格式化
        formatted_qty = self.format_quantity(quantity, precision_info)
        quantity_dec = Decimal(formatted_qty)
        
        # 确保不小于最小数量
        if quantity_dec < min_qty:
            logger.warning(f"Calculated quantity {formatted_qty} is less than min_qty {min_qty}")
            return 0
        
        # 检查最小名义价值 (quantity * price >= min_notional)
        notional = quantity_dec * Decimal(str(current_price))
        if notional < min_notional:
            logger.warning(f"Order notional {notional} is less than min_notional {min_notional}")
            return 0
        
        return float(quantity_dec)


# 全局实例
binance_api = BinanceAPI()
