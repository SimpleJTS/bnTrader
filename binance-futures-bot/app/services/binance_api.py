"""
币安API交易模块
处理所有与币安交易所的API交互
实现ExchangeAPI抽象接口
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
from app.services.exchange_interface import (
    ExchangeAPI, ExchangeType, SymbolPrecision, AccountBalance,
    PositionInfo, OrderResult
)

logger = logging.getLogger(__name__)


class BinanceAPI(ExchangeAPI):
    """币安期货API客户端"""
    
    BASE_URL = "https://fapi.binance.com"
    TESTNET_URL = "https://testnet.binancefuture.com"
    
    def __init__(self):
        self._exchange_info: Dict = {}
        self._symbol_info: Dict[str, Dict] = {}
        self._client: Optional[httpx.AsyncClient] = None
    
    @property
    def exchange_type(self) -> ExchangeType:
        return ExchangeType.BINANCE
    
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
    
    async def initialize(self) -> bool:
        """初始化API连接"""
        try:
            self._client = httpx.AsyncClient(timeout=30.0)
            await self.get_exchange_info()
            logger.info("Binance API 初始化成功")
            return True
        except Exception as e:
            logger.error(f"Binance API 初始化失败: {e}")
            return False
    
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
            logger.error(f"HTTP请求错误: 状态码={e.response.status_code}, 响应={e.response.text}")
            raise
        except Exception as e:
            logger.error(f"请求异常: {e}")
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
    
    async def get_symbol_precision(self, symbol: str) -> SymbolPrecision:
        """获取交易对精度信息"""
        info = await self.get_symbol_info(symbol)
        if not info:
            logger.warning(f"[{symbol}] 未找到交易对信息，使用默认精度")
            return SymbolPrecision(
                price_precision=8,
                quantity_precision=8,
                tick_size='0.00000001',
                step_size='0.00000001',
                min_qty='0.001',
                min_notional='5',
            )
        
        result = SymbolPrecision(
            price_precision=info.get("pricePrecision", 8),
            quantity_precision=info.get("quantityPrecision", 8),
            tick_size='0.00000001',
            step_size='0.00000001',
            min_qty='0.001',
            min_notional='5',
        )
        
        # 从过滤器获取精确的精度信息
        for f in info.get("filters", []):
            filter_type = f.get("filterType")
            if filter_type == "PRICE_FILTER":
                result.tick_size = f.get("tickSize", result.tick_size)
            elif filter_type == "LOT_SIZE":
                result.step_size = f.get("stepSize", result.step_size)
                result.min_qty = f.get("minQty", result.min_qty)
            elif filter_type == "MIN_NOTIONAL":
                result.min_notional = f.get("notional", result.min_notional)
        
        logger.debug(f"[{symbol}] 精度信息: {result}")
        return result
    
    def round_step(self, value: float, step: str) -> Decimal:
        """按步长对齐数值（向下取整）"""
        value_dec = Decimal(str(value))
        step_dec = Decimal(step)
        rounded = (value_dec / step_dec).to_integral_value(rounding=ROUND_DOWN) * step_dec
        return rounded
    
    def format_quantity(self, quantity: float, precision_info: SymbolPrecision) -> str:
        """格式化下单数量"""
        step_size = precision_info.step_size
        rounded = self.round_step(quantity, step_size)
        
        if '.' in step_size:
            decimal_places = len(step_size.rstrip('0').split('.')[1]) if '.' in step_size.rstrip('0') else 0
        else:
            decimal_places = 0
        
        if decimal_places > 0:
            return f"{rounded:.{decimal_places}f}"
        else:
            return str(int(rounded))
    
    def format_price(self, price: float, precision_info: SymbolPrecision) -> str:
        """格式化下单价格"""
        tick_size = precision_info.tick_size
        rounded = self.round_step(price, tick_size)
        precision = precision_info.price_precision
        return f"{rounded:.{precision}f}".rstrip('0').rstrip('.')
    
    def format_symbol(self, symbol: str) -> str:
        """格式化交易对名称（Binance使用BTCUSDT格式）"""
        return symbol.upper()
    
    async def get_account_balance(self) -> Dict[str, AccountBalance]:
        """获取账户余额"""
        result = await self._request("GET", "/fapi/v2/balance", signed=True)
        balances = {}
        for item in result:
            asset = item["asset"]
            balances[asset] = AccountBalance(
                asset=asset,
                balance=float(item["balance"]),
                available=float(item["availableBalance"]),
                unrealized_pnl=float(item.get("crossUnPnl", 0))
            )
        return balances
    
    async def get_usdt_balance(self) -> float:
        """获取USDT可用余额"""
        balances = await self.get_account_balance()
        usdt_info = balances.get("USDT")
        return usdt_info.available if usdt_info else 0.0
    
    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        """设置杠杆"""
        params = {
            "symbol": symbol,
            "leverage": leverage
        }
        return await self._request("POST", "/fapi/v1/leverage", params, signed=True)
    
    async def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> dict:
        """设置保证金模式 (CROSSED/ISOLATED)"""
        params = {
            "symbol": symbol,
            "marginType": margin_type
        }
        try:
            return await self._request("POST", "/fapi/v1/marginType", params, signed=True)
        except httpx.HTTPStatusError as e:
            if "No need to change margin type" in str(e.response.text):
                return {"msg": "Already in this margin type"}
            raise
    
    async def get_position(self, symbol: str = None) -> List[PositionInfo]:
        """获取持仓信息"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        result = await self._request("GET", "/fapi/v2/positionRisk", params, signed=True)
        
        positions = []
        for p in result:
            if float(p.get("positionAmt", 0)) != 0:
                amt = float(p.get("positionAmt", 0))
                positions.append(PositionInfo(
                    symbol=p["symbol"],
                    side="LONG" if amt > 0 else "SHORT",
                    entry_price=float(p.get("entryPrice", 0)),
                    quantity=abs(amt),
                    leverage=int(p.get("leverage", 1)),
                    unrealized_pnl=float(p.get("unRealizedProfit", 0)),
                    liquidation_price=float(p.get("liquidationPrice", 0))
                ))
        return positions
    
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
                                  reduce_only: bool = False) -> OrderResult:
        """下市价单"""
        precision_info = await self.get_symbol_precision(symbol)
        formatted_qty = self.format_quantity(quantity, precision_info)
        
        if Decimal(formatted_qty) <= 0:
            raise ValueError(f"无效的下单数量: {quantity} -> {formatted_qty}")
        
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": formatted_qty
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        
        side_desc = "买入" if side == "BUY" else "卖出"
        reduce_desc = "(仅减仓)" if reduce_only else ""
        logger.info(f"[{symbol}] 提交市价单: {side_desc}{reduce_desc}, 数量={formatted_qty}")
        
        result = await self._request("POST", "/fapi/v1/order", params, signed=True)
        
        # 计算实际成交价格
        avg_price = float(result.get("avgPrice", "0") or "0")
        if avg_price <= 0:
            cum_quote = float(result.get("cumQuote", 0) or result.get("cummulativeQuoteQty", 0))
            executed_qty = float(result.get("executedQty", 0))
            if cum_quote > 0 and executed_qty > 0:
                avg_price = cum_quote / executed_qty
        
        return OrderResult(
            order_id=str(result.get("orderId", "")),
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=float(formatted_qty),
            price=avg_price,
            status=result.get("status", ""),
            executed_qty=float(result.get("executedQty", 0)),
            avg_price=avg_price,
            raw_data=result
        )
    
    async def place_stop_loss_order(self, symbol: str, side: str, quantity: float,
                                     stop_price: float, close_position: bool = False) -> OrderResult:
        """下止损单"""
        precision_info = await self.get_symbol_precision(symbol)
        formatted_price = self.format_price(stop_price, precision_info)
        
        if Decimal(formatted_price) <= 0:
            raise ValueError(f"无效的止损价格: {stop_price} -> {formatted_price}")
        
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
            formatted_qty = self.format_quantity(quantity, precision_info)
            min_qty = Decimal(precision_info.min_qty)
            
            if Decimal(formatted_qty) <= 0:
                raise ValueError(f"无效的下单数量: {quantity} -> {formatted_qty}")
            if Decimal(formatted_qty) < min_qty:
                raise ValueError(f"下单数量 {formatted_qty} 小于最小值 {min_qty}")
            
            params["quantity"] = formatted_qty
            params["reduceOnly"] = "true"
        
        side_desc = "买入止损" if side == "BUY" else "卖出止损"
        logger.info(f"[{symbol}] 提交止损单: {side_desc}, 触发价={formatted_price}, 数量={params.get('quantity', '全仓')}")
        
        result = await self._request("POST", "/fapi/v1/order", params, signed=True)
        
        return OrderResult(
            order_id=str(result.get("orderId", "")),
            symbol=symbol,
            side=side,
            order_type="STOP_MARKET",
            quantity=float(params.get("quantity", 0)),
            price=float(formatted_price),
            status=result.get("status", ""),
            raw_data=result
        )
    
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
    
    async def get_24hr_ticker(self, symbol: str = None) -> List[dict]:
        """获取24小时价格变化统计"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        result = await self._request("GET", "/fapi/v1/ticker/24hr", params)
        if isinstance(result, dict):
            return [result]
        return result
    
    async def get_high_change_symbols(self, min_change_percent: float = 30.0) -> List[dict]:
        """获取24小时涨跌幅绝对值大于指定百分比的币种"""
        all_tickers = await self.get_24hr_ticker()
        
        high_change = []
        for ticker in all_tickers:
            symbol = ticker.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            
            try:
                change_percent = float(ticker.get("priceChangePercent", 0))
                if abs(change_percent) >= min_change_percent:
                    high_change.append({
                        "symbol": symbol,
                        "priceChangePercent": change_percent,
                        "lastPrice": float(ticker.get("lastPrice", 0)),
                        "highPrice": float(ticker.get("highPrice", 0)),
                        "lowPrice": float(ticker.get("lowPrice", 0)),
                        "volume": float(ticker.get("volume", 0)),
                        "quoteVolume": float(ticker.get("quoteVolume", 0)),
                    })
            except (ValueError, TypeError):
                continue
        
        high_change.sort(key=lambda x: abs(x["priceChangePercent"]), reverse=True)
        logger.info(f"找到 {len(high_change)} 个涨跌幅绝对值 >= {min_change_percent}% 的币种")
        return high_change
    
    async def calculate_order_quantity(self, symbol: str, leverage: int, 
                                        position_percent: float = None) -> float:
        """计算下单数量"""
        if position_percent is None:
            position_percent = settings.POSITION_SIZE_PERCENT
        
        precision_info = await self.get_symbol_precision(symbol)
        min_qty = Decimal(precision_info.min_qty)
        min_notional = Decimal(precision_info.min_notional)
        
        usdt_balance = await self.get_usdt_balance()
        available_funds = usdt_balance * (position_percent / 100)
        current_price = await self.get_current_price(symbol)
        
        if current_price <= 0:
            logger.error(f"[{symbol}] 无效的当前价格: {current_price}")
            return 0
        
        quantity = (available_funds * leverage) / current_price
        formatted_qty = self.format_quantity(quantity, precision_info)
        quantity_dec = Decimal(formatted_qty)
        
        if quantity_dec < min_qty:
            logger.warning(f"[{symbol}] 计算数量 {formatted_qty} 小于最小下单量 {min_qty}")
            return 0
        
        notional = quantity_dec * Decimal(str(current_price))
        if notional < min_notional:
            logger.warning(f"[{symbol}] 订单名义价值 {notional} 小于最小值 {min_notional}")
            return 0
        
        return float(quantity_dec)


# 全局实例
binance_api = BinanceAPI()
