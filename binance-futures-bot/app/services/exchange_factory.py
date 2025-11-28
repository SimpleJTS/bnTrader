"""
交易所工厂模式
根据配置动态选择交易所实现
"""
import logging
from typing import Optional

from app.config import settings
from app.services.exchange_interface import ExchangeAPI, ExchangeWebSocket, ExchangeType

logger = logging.getLogger(__name__)

# 交易所实例缓存
_exchange_api: Optional[ExchangeAPI] = None
_exchange_ws: Optional[ExchangeWebSocket] = None


def get_exchange_type() -> ExchangeType:
    """获取当前配置的交易所类型"""
    exchange_name = getattr(settings, 'EXCHANGE', 'binance').lower()
    
    if exchange_name == 'hyperliquid':
        return ExchangeType.HYPERLIQUID
    else:
        return ExchangeType.BINANCE


def get_exchange_api(force_new: bool = False) -> ExchangeAPI:
    """获取当前配置的交易所API实例
    
    Args:
        force_new: 是否强制创建新实例（用于切换交易所时）
    
    Returns:
        ExchangeAPI实例
    """
    global _exchange_api
    
    exchange_type = get_exchange_type()
    
    # 检查是否需要创建新实例
    if force_new or _exchange_api is None or _exchange_api.exchange_type != exchange_type:
        if exchange_type == ExchangeType.HYPERLIQUID:
            from app.services.hyperliquid_api import HyperliquidAPI
            _exchange_api = HyperliquidAPI()
            logger.info("已创建 Hyperliquid API 实例")
        else:
            from app.services.binance_api import BinanceAPI
            _exchange_api = BinanceAPI()
            logger.info("已创建 Binance API 实例")
    
    return _exchange_api


def get_exchange_ws(force_new: bool = False) -> ExchangeWebSocket:
    """获取当前配置的交易所WebSocket实例
    
    Args:
        force_new: 是否强制创建新实例（用于切换交易所时）
    
    Returns:
        ExchangeWebSocket实例
    """
    global _exchange_ws
    
    exchange_type = get_exchange_type()
    
    # 检查是否需要创建新实例
    if force_new or _exchange_ws is None or _exchange_ws.exchange_type != exchange_type:
        if exchange_type == ExchangeType.HYPERLIQUID:
            from app.services.hyperliquid_ws import HyperliquidWebSocket
            _exchange_ws = HyperliquidWebSocket()
            logger.info("已创建 Hyperliquid WebSocket 实例")
        else:
            from app.services.binance_ws import BinanceWebSocket
            _exchange_ws = BinanceWebSocket()
            logger.info("已创建 Binance WebSocket 实例")
    
    return _exchange_ws


async def switch_exchange(exchange_type: ExchangeType) -> bool:
    """切换交易所
    
    Args:
        exchange_type: 目标交易所类型
    
    Returns:
        是否切换成功
    """
    global _exchange_api, _exchange_ws
    
    try:
        # 停止当前WebSocket
        if _exchange_ws:
            await _exchange_ws.stop()
        
        # 关闭当前API
        if _exchange_api:
            await _exchange_api.close()
        
        # 更新配置
        settings.EXCHANGE = exchange_type.value
        
        # 创建新实例
        _exchange_api = None
        _exchange_ws = None
        
        new_api = get_exchange_api(force_new=True)
        new_ws = get_exchange_ws(force_new=True)
        
        # 初始化新API
        if not await new_api.initialize():
            logger.error(f"初始化 {exchange_type.value} API 失败")
            return False
        
        # 启动新WebSocket
        await new_ws.start()
        
        logger.info(f"已切换到交易所: {exchange_type.value}")
        return True
        
    except Exception as e:
        logger.error(f"切换交易所失败: {e}")
        return False


def get_exchange_display_name(exchange_type: ExchangeType = None) -> str:
    """获取交易所显示名称"""
    if exchange_type is None:
        exchange_type = get_exchange_type()
    
    names = {
        ExchangeType.BINANCE: "Binance (币安)",
        ExchangeType.HYPERLIQUID: "Hyperliquid"
    }
    return names.get(exchange_type, exchange_type.value)


def get_supported_exchanges() -> list:
    """获取支持的交易所列表"""
    return [
        {
            "type": ExchangeType.BINANCE.value,
            "name": "Binance (币安)",
            "description": "全球最大的中心化交易所"
        },
        {
            "type": ExchangeType.HYPERLIQUID.value,
            "name": "Hyperliquid",
            "description": "L1区块链上的去中心化永续合约交易所"
        }
    ]
