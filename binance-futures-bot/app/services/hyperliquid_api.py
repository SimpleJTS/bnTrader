"""
Hyperliquid API交易模块
处理所有与Hyperliquid交易所的API交互
实现ExchangeAPI抽象接口

Hyperliquid是一个L1区块链上的去中心化永续合约交易所
使用钱包私钥签名进行认证（EIP-712）
"""
import asyncio
import logging
import time
import json
from typing import Optional, Dict, List, Any
from decimal import Decimal, ROUND_DOWN
import httpx

from app.config import settings
from app.services.exchange_interface import (
    ExchangeAPI, ExchangeType, SymbolPrecision, AccountBalance,
    PositionInfo, OrderResult
)

logger = logging.getLogger(__name__)

# Hyperliquid特定常量
HYPERLIQUID_MAINNET_URL = "https://api.hyperliquid.xyz"
HYPERLIQUID_TESTNET_URL = "https://api.hyperliquid-testnet.xyz"


class HyperliquidAPI(ExchangeAPI):
    """Hyperliquid API客户端
    
    Hyperliquid与Binance的主要差异:
    1. 使用钱包私钥签名而非API Key
    2. 交易对格式: BTC而非BTCUSDT
    3. 订单签名使用EIP-712
    """
    
    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._meta: Dict = {}  # 交易所元数据
        self._asset_info: Dict[str, Dict] = {}  # 资产信息缓存
        self._wallet_address: str = ""
        
    @property
    def exchange_type(self) -> ExchangeType:
        return ExchangeType.HYPERLIQUID
    
    @property
    def private_key(self) -> str:
        """获取钱包私钥"""
        return settings.HYPERLIQUID_PRIVATE_KEY
    
    @property
    def base_url(self) -> str:
        """获取API基础URL"""
        return HYPERLIQUID_TESTNET_URL if settings.HYPERLIQUID_TESTNET else HYPERLIQUID_MAINNET_URL
    
    async def initialize(self) -> bool:
        """初始化API连接"""
        try:
            self._client = httpx.AsyncClient(timeout=30.0)
            
            # 从私钥获取钱包地址
            if self.private_key:
                try:
                    from eth_account import Account
                    account = Account.from_key(self.private_key)
                    self._wallet_address = account.address
                    logger.info(f"Hyperliquid 钱包地址: {self._wallet_address}")
                except Exception as e:
                    logger.error(f"无法从私钥获取钱包地址: {e}")
                    return False
            
            # 获取交易所元数据
            await self._load_meta()
            logger.info("Hyperliquid API 初始化成功")
            return True
        except Exception as e:
            logger.error(f"Hyperliquid API 初始化失败: {e}")
            return False
    
    async def _load_meta(self):
        """加载交易所元数据"""
        try:
            result = await self._post_info({"type": "meta"})
            if result and "universe" in result:
                self._meta = result
                # 缓存资产信息
                for asset in result.get("universe", []):
                    name = asset.get("name", "")
                    self._asset_info[name] = asset
                    # 同时支持带USDT后缀的查询
                    self._asset_info[f"{name}USDT"] = asset
                logger.info(f"已加载 {len(result.get('universe', []))} 个交易对信息")
        except Exception as e:
            logger.error(f"加载Hyperliquid元数据失败: {e}")
    
    async def get_client(self) -> httpx.AsyncClient:
        """获取HTTP客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client
    
    async def close(self):
        """关闭客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
    
    def _get_coin_name(self, symbol: str) -> str:
        """将交易对转换为Hyperliquid的币种名称
        
        Binance格式: BTCUSDT -> BTC
        """
        if symbol.endswith("USDT"):
            return symbol[:-4]
        return symbol
    
    def _get_asset_index(self, symbol: str) -> int:
        """获取资产索引（Hyperliquid下单需要）"""
        coin = self._get_coin_name(symbol)
        asset = self._asset_info.get(coin) or self._asset_info.get(symbol)
        if asset:
            # 在universe列表中的索引
            for i, a in enumerate(self._meta.get("universe", [])):
                if a.get("name") == coin:
                    return i
        return -1
    
    async def _post_info(self, data: dict) -> dict:
        """发送info请求（无需签名）"""
        client = await self.get_client()
        url = f"{self.base_url}/info"
        
        try:
            response = await client.post(url, json=data)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Hyperliquid info请求失败: {e}")
            raise
    
    async def _post_exchange(self, action: dict) -> dict:
        """发送交易请求（需要签名）"""
        if not self.private_key:
            raise ValueError("未配置Hyperliquid私钥")
        
        try:
            from eth_account import Account
            from eth_account.messages import encode_typed_data
            
            client = await self.get_client()
            url = f"{self.base_url}/exchange"
            
            # 构建EIP-712签名
            nonce = int(time.time() * 1000)
            
            # Hyperliquid的签名结构
            typed_data = {
                "types": {
                    "EIP712Domain": [
                        {"name": "name", "type": "string"},
                        {"name": "version", "type": "string"},
                        {"name": "chainId", "type": "uint256"},
                        {"name": "verifyingContract", "type": "address"},
                    ],
                    "HyperliquidTransaction:Action": [
                        {"name": "hyperliquidChain", "type": "string"},
                        {"name": "action", "type": "string"},
                        {"name": "nonce", "type": "uint64"},
                    ],
                },
                "primaryType": "HyperliquidTransaction:Action",
                "domain": {
                    "name": "HyperliquidSignTransaction",
                    "version": "1",
                    "chainId": 1337 if settings.HYPERLIQUID_TESTNET else 42161,
                    "verifyingContract": "0x0000000000000000000000000000000000000000",
                },
                "message": {
                    "hyperliquidChain": "Testnet" if settings.HYPERLIQUID_TESTNET else "Mainnet",
                    "action": json.dumps(action, separators=(',', ':')),
                    "nonce": nonce,
                },
            }
            
            # 签名
            account = Account.from_key(self.private_key)
            signable = encode_typed_data(full_message=typed_data)
            signed = account.sign_message(signable)
            
            # 构建请求
            request_data = {
                "action": action,
                "nonce": nonce,
                "signature": {
                    "r": hex(signed.r),
                    "s": hex(signed.s),
                    "v": signed.v,
                },
                "vaultAddress": None,
            }
            
            response = await client.post(url, json=request_data)
            response.raise_for_status()
            return response.json()
            
        except ImportError:
            logger.error("需要安装eth-account库: pip install eth-account")
            raise
        except Exception as e:
            logger.error(f"Hyperliquid exchange请求失败: {e}")
            raise
    
    def format_symbol(self, symbol: str) -> str:
        """格式化交易对名称
        
        Hyperliquid使用纯币种名称（BTC而非BTCUSDT）
        但为了与系统兼容，内部存储仍使用BTCUSDT格式
        """
        # 保持USDT后缀以兼容系统其他部分
        if not symbol.endswith("USDT"):
            return f"{symbol}USDT"
        return symbol.upper()
    
    async def get_exchange_info(self) -> dict:
        """获取交易所信息"""
        if not self._meta:
            await self._load_meta()
        return self._meta
    
    async def get_symbol_precision(self, symbol: str) -> SymbolPrecision:
        """获取交易对精度信息"""
        coin = self._get_coin_name(symbol)
        asset = self._asset_info.get(coin) or self._asset_info.get(symbol)
        
        if not asset:
            logger.warning(f"[{symbol}] 未找到交易对信息，使用默认精度")
            return SymbolPrecision(
                price_precision=6,
                quantity_precision=4,
                tick_size='0.000001',
                step_size='0.0001',
                min_qty='0.0001',
                min_notional='10',
            )
        
        # Hyperliquid的精度信息
        sz_decimals = asset.get("szDecimals", 4)
        
        # 计算step_size
        step_size = str(Decimal(10) ** -sz_decimals)
        
        return SymbolPrecision(
            price_precision=6,  # Hyperliquid价格精度通常为6
            quantity_precision=sz_decimals,
            tick_size='0.000001',
            step_size=step_size,
            min_qty=step_size,
            min_notional='10',
        )
    
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
        """格式化价格"""
        tick_size = precision_info.tick_size
        rounded = self.round_step(price, tick_size)
        precision = precision_info.price_precision
        return f"{rounded:.{precision}f}".rstrip('0').rstrip('.')
    
    async def get_current_price(self, symbol: str) -> float:
        """获取当前价格"""
        coin = self._get_coin_name(symbol)
        
        result = await self._post_info({
            "type": "allMids"
        })
        
        if result and coin in result:
            return float(result[coin])
        
        logger.warning(f"[{symbol}] 未找到价格信息")
        return 0.0
    
    async def get_klines(self, symbol: str, interval: str, limit: int = 200) -> List[list]:
        """获取K线数据
        
        Hyperliquid的K线格式转换为Binance兼容格式
        """
        coin = self._get_coin_name(symbol)
        
        # 转换时间周期
        interval_map = {
            "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m",
            "30m": "30m", "1h": "1h", "2h": "2h", "4h": "4h",
            "6h": "6h", "12h": "12h", "1d": "1d", "1w": "1w"
        }
        hl_interval = interval_map.get(interval, "1m")
        
        # 计算时间范围
        end_time = int(time.time() * 1000)
        interval_ms = self._interval_to_ms(hl_interval)
        start_time = end_time - (limit * interval_ms)
        
        result = await self._post_info({
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": hl_interval,
                "startTime": start_time,
                "endTime": end_time
            }
        })
        
        # 转换为Binance K线格式
        # [open_time, open, high, low, close, volume, close_time, ...]
        klines = []
        for candle in result:
            open_time = candle.get("t", 0)
            close_time = open_time + interval_ms - 1
            klines.append([
                open_time,
                candle.get("o", "0"),
                candle.get("h", "0"),
                candle.get("l", "0"),
                candle.get("c", "0"),
                candle.get("v", "0"),
                close_time,
                "0",  # quote volume
                0,    # trades
                "0",  # taker buy base
                "0",  # taker buy quote
                "0"   # ignore
            ])
        
        return klines
    
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
    
    async def get_account_balance(self) -> Dict[str, AccountBalance]:
        """获取账户余额"""
        if not self._wallet_address:
            return {}
        
        result = await self._post_info({
            "type": "clearinghouseState",
            "user": self._wallet_address
        })
        
        balances = {}
        if result:
            # 提取USDC余额（Hyperliquid使用USDC作为保证金）
            margin_summary = result.get("marginSummary", {})
            account_value = float(margin_summary.get("accountValue", 0))
            total_margin = float(margin_summary.get("totalMarginUsed", 0))
            available = account_value - total_margin
            
            # 计算未实现盈亏
            unrealized_pnl = 0.0
            for pos in result.get("assetPositions", []):
                position = pos.get("position", {})
                unrealized_pnl += float(position.get("unrealizedPnl", 0))
            
            # 使用USDT作为资产名称以兼容系统
            balances["USDT"] = AccountBalance(
                asset="USDT",
                balance=account_value,
                available=available,
                unrealized_pnl=unrealized_pnl
            )
        
        return balances
    
    async def get_usdt_balance(self) -> float:
        """获取USDT可用余额（实际为USDC）"""
        balances = await self.get_account_balance()
        usdt_info = balances.get("USDT")
        return usdt_info.available if usdt_info else 0.0
    
    async def get_position(self, symbol: str = None) -> List[PositionInfo]:
        """获取持仓信息"""
        if not self._wallet_address:
            return []
        
        result = await self._post_info({
            "type": "clearinghouseState",
            "user": self._wallet_address
        })
        
        positions = []
        if result:
            for pos in result.get("assetPositions", []):
                position = pos.get("position", {})
                coin = position.get("coin", "")
                szi = float(position.get("szi", 0))
                
                if szi == 0:
                    continue
                
                # 如果指定了symbol，只返回该symbol
                if symbol:
                    symbol_coin = self._get_coin_name(symbol)
                    if coin != symbol_coin:
                        continue
                
                positions.append(PositionInfo(
                    symbol=f"{coin}USDT",  # 转换为Binance格式
                    side="LONG" if szi > 0 else "SHORT",
                    entry_price=float(position.get("entryPx", 0)),
                    quantity=abs(szi),
                    leverage=int(position.get("leverage", {}).get("value", 1)),
                    unrealized_pnl=float(position.get("unrealizedPnl", 0)),
                    liquidation_price=float(position.get("liquidationPx", 0) or 0)
                ))
        
        return positions
    
    async def set_leverage(self, symbol: str, leverage: int) -> dict:
        """设置杠杆"""
        coin = self._get_coin_name(symbol)
        asset_index = self._get_asset_index(symbol)
        
        if asset_index < 0:
            raise ValueError(f"未找到资产: {symbol}")
        
        action = {
            "type": "updateLeverage",
            "asset": asset_index,
            "isCross": False,  # 使用逐仓模式
            "leverage": leverage
        }
        
        return await self._post_exchange(action)
    
    async def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> dict:
        """设置保证金模式
        
        Hyperliquid在设置杠杆时指定isCross参数
        """
        # 实际上在set_leverage中处理
        return {"msg": "Margin type set via leverage update"}
    
    async def place_market_order(self, symbol: str, side: str, quantity: float,
                                  reduce_only: bool = False) -> OrderResult:
        """下市价单"""
        coin = self._get_coin_name(symbol)
        asset_index = self._get_asset_index(symbol)
        
        if asset_index < 0:
            raise ValueError(f"未找到资产: {symbol}")
        
        precision_info = await self.get_symbol_precision(symbol)
        formatted_qty = self.format_quantity(quantity, precision_info)
        
        if Decimal(formatted_qty) <= 0:
            raise ValueError(f"无效的下单数量: {quantity} -> {formatted_qty}")
        
        # 获取当前价格用于滑点保护
        current_price = await self.get_current_price(symbol)
        slippage = 0.01  # 1% 滑点
        
        if side == "BUY":
            limit_price = current_price * (1 + slippage)
            is_buy = True
        else:
            limit_price = current_price * (1 - slippage)
            is_buy = False
        
        action = {
            "type": "order",
            "orders": [{
                "a": asset_index,
                "b": is_buy,
                "p": self.format_price(limit_price, precision_info),
                "s": formatted_qty,
                "r": reduce_only,
                "t": {
                    "limit": {
                        "tif": "Ioc"  # Immediate or Cancel (模拟市价单)
                    }
                }
            }],
            "grouping": "na"
        }
        
        side_desc = "买入" if is_buy else "卖出"
        reduce_desc = "(仅减仓)" if reduce_only else ""
        logger.info(f"[{symbol}] 提交市价单: {side_desc}{reduce_desc}, 数量={formatted_qty}")
        
        result = await self._post_exchange(action)
        
        # 解析订单结果
        order_id = ""
        status = "UNKNOWN"
        avg_price = current_price
        
        if result.get("status") == "ok":
            statuses = result.get("response", {}).get("data", {}).get("statuses", [])
            if statuses:
                order_status = statuses[0]
                if "resting" in order_status:
                    order_id = str(order_status["resting"]["oid"])
                    status = "NEW"
                elif "filled" in order_status:
                    order_id = str(order_status["filled"]["oid"])
                    status = "FILLED"
                    avg_price = float(order_status["filled"].get("avgPx", current_price))
                elif "error" in order_status:
                    raise ValueError(f"订单错误: {order_status['error']}")
        
        return OrderResult(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=float(formatted_qty),
            price=avg_price,
            status=status,
            executed_qty=float(formatted_qty) if status == "FILLED" else 0,
            avg_price=avg_price,
            raw_data=result
        )
    
    async def place_stop_loss_order(self, symbol: str, side: str, quantity: float,
                                     stop_price: float, close_position: bool = False) -> OrderResult:
        """下止损单（触发订单）"""
        coin = self._get_coin_name(symbol)
        asset_index = self._get_asset_index(symbol)
        
        if asset_index < 0:
            raise ValueError(f"未找到资产: {symbol}")
        
        precision_info = await self.get_symbol_precision(symbol)
        formatted_price = self.format_price(stop_price, precision_info)
        
        if Decimal(formatted_price) <= 0:
            raise ValueError(f"无效的止损价格: {stop_price}")
        
        is_buy = side == "BUY"
        
        # 构建触发订单
        order_spec = {
            "a": asset_index,
            "b": is_buy,
            "p": formatted_price,
            "r": True,  # 止损单总是reduce_only
            "t": {
                "trigger": {
                    "isMarket": True,  # 触发后以市价成交
                    "triggerPx": formatted_price,
                    "tpsl": "sl"  # stop loss
                }
            }
        }
        
        if close_position:
            # 平全仓 - 使用一个很大的数量
            order_spec["s"] = "1000000"
        else:
            formatted_qty = self.format_quantity(quantity, precision_info)
            if Decimal(formatted_qty) <= 0:
                raise ValueError(f"无效的下单数量: {quantity}")
            order_spec["s"] = formatted_qty
        
        action = {
            "type": "order",
            "orders": [order_spec],
            "grouping": "na"
        }
        
        side_desc = "买入止损" if is_buy else "卖出止损"
        logger.info(f"[{symbol}] 提交止损单: {side_desc}, 触发价={formatted_price}")
        
        result = await self._post_exchange(action)
        
        # 解析订单结果
        order_id = ""
        status = "UNKNOWN"
        
        if result.get("status") == "ok":
            statuses = result.get("response", {}).get("data", {}).get("statuses", [])
            if statuses:
                order_status = statuses[0]
                if "resting" in order_status:
                    order_id = str(order_status["resting"]["oid"])
                    status = "NEW"
                elif "error" in order_status:
                    raise ValueError(f"订单错误: {order_status['error']}")
        
        return OrderResult(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type="STOP_MARKET",
            quantity=float(order_spec["s"]) if not close_position else 0,
            price=float(formatted_price),
            status=status,
            raw_data=result
        )
    
    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        """取消订单"""
        coin = self._get_coin_name(symbol)
        asset_index = self._get_asset_index(symbol)
        
        if asset_index < 0:
            raise ValueError(f"未找到资产: {symbol}")
        
        action = {
            "type": "cancel",
            "cancels": [{
                "a": asset_index,
                "o": int(order_id)
            }]
        }
        
        return await self._post_exchange(action)
    
    async def cancel_all_orders(self, symbol: str) -> dict:
        """取消某个交易对的所有订单"""
        coin = self._get_coin_name(symbol)
        asset_index = self._get_asset_index(symbol)
        
        if asset_index < 0:
            raise ValueError(f"未找到资产: {symbol}")
        
        # 先获取所有挂单
        open_orders = await self.get_open_orders(symbol)
        
        if not open_orders:
            return {"msg": "No open orders"}
        
        # 批量取消
        cancels = []
        for order in open_orders:
            cancels.append({
                "a": asset_index,
                "o": int(order.get("oid", 0))
            })
        
        if cancels:
            action = {
                "type": "cancel",
                "cancels": cancels
            }
            return await self._post_exchange(action)
        
        return {"msg": "No orders to cancel"}
    
    async def get_open_orders(self, symbol: str = None) -> List[dict]:
        """获取当前挂单"""
        if not self._wallet_address:
            return []
        
        result = await self._post_info({
            "type": "openOrders",
            "user": self._wallet_address
        })
        
        orders = []
        for order in result:
            coin = order.get("coin", "")
            
            if symbol:
                symbol_coin = self._get_coin_name(symbol)
                if coin != symbol_coin:
                    continue
            
            orders.append({
                "symbol": f"{coin}USDT",
                "oid": order.get("oid"),
                "side": "BUY" if order.get("side") == "B" else "SELL",
                "price": order.get("limitPx"),
                "quantity": order.get("sz"),
                "orderType": order.get("orderType"),
            })
        
        return orders
    
    async def get_24hr_ticker(self, symbol: str = None) -> List[dict]:
        """获取24小时价格变化统计
        
        Hyperliquid没有直接的24hr ticker接口，需要通过其他方式获取
        """
        # 获取所有中间价
        all_mids = await self._post_info({"type": "allMids"})
        
        # 获取元数据中的币种列表
        tickers = []
        for coin, price in all_mids.items():
            if symbol:
                symbol_coin = self._get_coin_name(symbol)
                if coin != symbol_coin:
                    continue
            
            tickers.append({
                "symbol": f"{coin}USDT",
                "lastPrice": float(price),
                "priceChangePercent": 0,  # Hyperliquid API不直接提供
                "highPrice": float(price),
                "lowPrice": float(price),
                "volume": 0,
                "quoteVolume": 0,
            })
        
        return tickers
    
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
hyperliquid_api = HyperliquidAPI()
