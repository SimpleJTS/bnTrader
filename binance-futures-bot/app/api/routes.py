"""
Web API路由
"""
import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, DatabaseManager
from app.models import TradingPair, Position, TradeLog, SystemConfig, StopLossLog
from app.api.schemas import (
    TradingPairCreate, TradingPairUpdate, TradingPairResponse,
    BinanceConfigUpdate, TelegramConfigUpdate, SystemConfigResponse,
    PositionResponse, WebSocketStatus, TradeLogResponse, StopLossLogResponse,
    MessageResponse, ErrorResponse,
    TrailingStopConfig, TrailingStopConfigUpdate, TrailingStopLevel
)
from app.config import settings, config_manager
from app.services.binance_api import binance_api
from app.services.binance_ws import binance_ws
from app.services.position_manager import position_manager
from app.services.telegram import telegram_service
from app.utils.encryption import encrypt, encryption_manager

logger = logging.getLogger(__name__)

router = APIRouter()


# ========== Trading Pairs ==========

@router.get("/trading-pairs", response_model=List[TradingPairResponse])
async def get_trading_pairs():
    """获取所有交易对配置"""
    session = await DatabaseManager.get_session()
    try:
        result = await session.execute(select(TradingPair).order_by(TradingPair.created_at.desc()))
        pairs = result.scalars().all()
        return pairs
    finally:
        await session.close()


@router.post("/trading-pairs", response_model=TradingPairResponse)
async def create_trading_pair(data: TradingPairCreate):
    """创建交易对配置"""
    session = await DatabaseManager.get_session()
    try:
        # 检查是否已存在
        result = await session.execute(
            select(TradingPair).where(TradingPair.symbol == data.symbol.upper())
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"交易对 {data.symbol} 已存在")
        
        # 创建
        pair = TradingPair(
            symbol=data.symbol.upper(),
            leverage=data.leverage,
            strategy_interval=data.strategy_interval,
            stop_loss_percent=data.stop_loss_percent,
            is_active=data.is_active
        )
        session.add(pair)
        await session.commit()
        await session.refresh(pair)
        
        # 通知配置变更
        if pair.is_active:
            await config_manager.notify_observers("trading_pair_added", {
                "symbol": pair.symbol,
                "interval": pair.strategy_interval
            })
        
        return pair
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await session.close()


@router.put("/trading-pairs/{symbol}", response_model=TradingPairResponse)
async def update_trading_pair(symbol: str, data: TradingPairUpdate):
    """更新交易对配置"""
    session = await DatabaseManager.get_session()
    try:
        result = await session.execute(
            select(TradingPair).where(TradingPair.symbol == symbol.upper())
        )
        pair = result.scalar_one_or_none()
        if not pair:
            raise HTTPException(status_code=404, detail=f"交易对 {symbol} 不存在")
        
        # 记录旧状态
        old_active = pair.is_active
        old_interval = pair.strategy_interval
        
        # 更新字段
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(pair, key, value)
        
        await session.commit()
        await session.refresh(pair)
        
        # 通知配置变更
        if old_active != pair.is_active or old_interval != pair.strategy_interval:
            await config_manager.notify_observers("trading_pair_updated", {
                "symbol": pair.symbol,
                "interval": pair.strategy_interval,
                "is_active": pair.is_active
            })
        
        return pair
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await session.close()


@router.delete("/trading-pairs/{symbol}", response_model=MessageResponse)
async def delete_trading_pair(symbol: str):
    """删除交易对配置"""
    session = await DatabaseManager.get_session()
    try:
        result = await session.execute(
            select(TradingPair).where(TradingPair.symbol == symbol.upper())
        )
        pair = result.scalar_one_or_none()
        if not pair:
            raise HTTPException(status_code=404, detail=f"交易对 {symbol} 不存在")
        
        await session.delete(pair)
        await session.commit()
        
        # 通知配置变更
        await config_manager.notify_observers("trading_pair_removed", {
            "symbol": symbol.upper()
        })
        
        return MessageResponse(success=True, message=f"已删除交易对 {symbol}")
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await session.close()


# ========== System Config ==========

@router.get("/config/status", response_model=SystemConfigResponse)
async def get_config_status():
    """获取系统配置状态"""
    return SystemConfigResponse(
        binance_configured=bool(settings.BINANCE_API_KEY and settings.BINANCE_API_SECRET),
        binance_testnet=settings.BINANCE_TESTNET,
        telegram_configured=bool(settings.TG_BOT_TOKEN and settings.TG_CHAT_ID),
        channel_listener_configured=bool(settings.TG_API_ID and settings.TG_API_HASH),
        encryption_enabled=encryption_manager.is_available
    )


@router.post("/config/binance", response_model=MessageResponse)
async def update_binance_config(data: BinanceConfigUpdate):
    """更新币安API配置（加密存储）"""
    session = await DatabaseManager.get_session()
    try:
        # 加密敏感数据后保存到数据库
        configs = [
            ("BINANCE_API_KEY", encrypt(data.api_key), "币安API Key (加密)"),
            ("BINANCE_API_SECRET", encrypt(data.api_secret), "币安API Secret (加密)"),
            ("BINANCE_TESTNET", str(data.testnet), "是否使用测试网")
        ]
        
        for key, value, desc in configs:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key == key)
            )
            config = result.scalar_one_or_none()
            if config:
                config.value = value
                config.description = desc
            else:
                config = SystemConfig(key=key, value=value, description=desc)
                session.add(config)
        
        await session.commit()
        
        # 更新运行时配置（使用明文）
        config_manager.update_binance_config(data.api_key, data.api_secret, data.testnet)
        
        encrypted_status = "已加密" if encryption_manager.is_available else "未加密（加密器不可用）"
        return MessageResponse(success=True, message=f"币安API配置已更新（{encrypted_status}）")
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await session.close()


@router.post("/config/telegram", response_model=MessageResponse)
async def update_telegram_config(data: TelegramConfigUpdate):
    """更新Telegram配置（加密存储）"""
    session = await DatabaseManager.get_session()
    try:
        # 加密敏感数据后保存
        configs = [
            ("TG_BOT_TOKEN", encrypt(data.bot_token), "Telegram Bot Token (加密)"),
            ("TG_CHAT_ID", data.chat_id, "Telegram Chat ID"),  # Chat ID 不需要加密
        ]
        
        if data.api_id:
            configs.append(("TG_API_ID", str(data.api_id), "Telegram API ID"))
        if data.api_hash:
            configs.append(("TG_API_HASH", encrypt(data.api_hash), "Telegram API Hash (加密)"))
        
        for key, value, desc in configs:
            result = await session.execute(
                select(SystemConfig).where(SystemConfig.key == key)
            )
            config = result.scalar_one_or_none()
            if config:
                config.value = value
                config.description = desc
            else:
                config = SystemConfig(key=key, value=value, description=desc)
                session.add(config)
        
        await session.commit()
        
        # 更新运行时配置（使用明文）
        config_manager.update_telegram_config(
            data.bot_token, data.chat_id,
            data.api_id or 0, data.api_hash or ""
        )
        
        # 重新初始化Telegram服务
        await telegram_service.initialize()
        
        encrypted_status = "已加密" if encryption_manager.is_available else "未加密（加密器不可用）"
        return MessageResponse(success=True, message=f"Telegram配置已更新（{encrypted_status}）")
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await session.close()


# ========== Positions ==========

@router.get("/positions", response_model=List[PositionResponse])
async def get_positions(status: Optional[str] = Query(None, description="OPEN/CLOSED")):
    """获取仓位列表"""
    session = await DatabaseManager.get_session()
    try:
        query = select(Position).order_by(Position.opened_at.desc())
        if status:
            query = query.where(Position.status == status.upper())
        
        result = await session.execute(query)
        positions = result.scalars().all()
        return positions
    finally:
        await session.close()


@router.post("/positions/{symbol}/close", response_model=MessageResponse)
async def close_position(symbol: str):
    """手动平仓"""
    try:
        success = await position_manager.close_position(symbol.upper(), reason="MANUAL")
        if success:
            return MessageResponse(success=True, message=f"已平仓 {symbol}")
        else:
            raise HTTPException(status_code=404, detail=f"未找到 {symbol} 的持仓")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== WebSocket Status ==========

@router.get("/websocket/status", response_model=WebSocketStatus)
async def get_websocket_status():
    """获取WebSocket状态"""
    status = binance_ws.get_status()
    return WebSocketStatus(**status)


@router.post("/websocket/restart", response_model=MessageResponse)
async def restart_websocket():
    """重启WebSocket连接"""
    try:
        await binance_ws.stop()
        await binance_ws.start()
        return MessageResponse(success=True, message="WebSocket已重启")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== Trade Logs ==========

@router.get("/trade-logs", response_model=List[TradeLogResponse])
async def get_trade_logs(
    symbol: Optional[str] = None,
    limit: int = Query(default=100, le=1000)
):
    """获取交易日志"""
    session = await DatabaseManager.get_session()
    try:
        query = select(TradeLog).order_by(TradeLog.created_at.desc()).limit(limit)
        if symbol:
            query = query.where(TradeLog.symbol == symbol.upper())
        
        result = await session.execute(query)
        logs = result.scalars().all()
        return logs
    finally:
        await session.close()


# ========== Stop Loss Logs ==========

@router.get("/stop-loss-logs", response_model=List[StopLossLogResponse])
async def get_stop_loss_logs(
    symbol: Optional[str] = None,
    limit: int = Query(default=100, le=500)
):
    """获取止损调整记录"""
    session = await DatabaseManager.get_session()
    try:
        query = select(StopLossLog).order_by(StopLossLog.created_at.desc()).limit(limit)
        if symbol:
            query = query.where(StopLossLog.symbol == symbol.upper())
        
        result = await session.execute(query)
        logs = result.scalars().all()
        return logs
    finally:
        await session.close()


@router.get("/stop-loss-logs/stats")
async def get_stop_loss_stats():
    """获取止损调整统计"""
    session = await DatabaseManager.get_session()
    try:
        # 获取最近的调整记录数
        result = await session.execute(
            select(StopLossLog).order_by(StopLossLog.created_at.desc()).limit(100)
        )
        logs = result.scalars().all()
        
        # 统计各级别调整次数
        level_counts = {0: 0, 1: 0, 2: 0, 3: 0}
        trailing_count = 0
        symbols = set()
        
        for log in logs:
            level_counts[log.new_level] = level_counts.get(log.new_level, 0) + 1
            if log.is_trailing:
                trailing_count += 1
            symbols.add(log.symbol)
        
        return {
            "total_adjustments": len(logs),
            "level_counts": level_counts,
            "trailing_adjustments": trailing_count,
            "symbols_affected": list(symbols)
        }
    finally:
        await session.close()


# ========== Account ==========

@router.get("/account/balance")
async def get_account_balance():
    """获取账户余额"""
    try:
        balances = await binance_api.get_account_balance()
        usdt = balances.get("USDT", {})
        return {
            "usdt_balance": usdt.get("balance", 0),
            "usdt_available": usdt.get("available", 0),
            "unrealized_pnl": usdt.get("unrealized_pnl", 0)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== Test ==========

@router.post("/test/telegram", response_model=MessageResponse)
async def test_telegram():
    """测试Telegram通知"""
    try:
        success = await telegram_service.send_message("🔔 测试消息 - Binance Futures Bot 运行正常!")
        if success:
            return MessageResponse(success=True, message="测试消息已发送")
        else:
            raise HTTPException(status_code=500, detail="发送失败，请检查Telegram配置")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== Trailing Stop Config ==========

def get_default_trailing_config() -> dict:
    """获取默认移动止损配置"""
    return {
        "level_1": {"profit_min": 2.5, "profit_max": 5.0, "lock_profit": 0, "trailing_enabled": False, "trailing_percent": 3.0},
        "level_2": {"profit_min": 5.0, "profit_max": 10.0, "lock_profit": 3.0, "trailing_enabled": False, "trailing_percent": 3.0},
        "level_3": {"profit_min": 10.0, "profit_max": None, "lock_profit": 5.0, "trailing_enabled": True, "trailing_percent": 3.0}
    }


@router.get("/config/trailing-stop", response_model=TrailingStopConfig)
async def get_trailing_stop_config():
    """获取移动止损配置"""
    import json
    session = await DatabaseManager.get_session()
    try:
        result = await session.execute(
            select(SystemConfig).where(SystemConfig.key == "TRAILING_STOP_CONFIG")
        )
        config = result.scalar_one_or_none()
        
        if config and config.value:
            try:
                config_data = json.loads(config.value)
                return TrailingStopConfig(
                    level_1=TrailingStopLevel(**config_data.get("level_1", get_default_trailing_config()["level_1"])),
                    level_2=TrailingStopLevel(**config_data.get("level_2", get_default_trailing_config()["level_2"])),
                    level_3=TrailingStopLevel(**config_data.get("level_3", get_default_trailing_config()["level_3"]))
                )
            except (json.JSONDecodeError, TypeError):
                pass
        
        # 返回默认配置
        default = get_default_trailing_config()
        return TrailingStopConfig(
            level_1=TrailingStopLevel(**default["level_1"]),
            level_2=TrailingStopLevel(**default["level_2"]),
            level_3=TrailingStopLevel(**default["level_3"])
        )
    finally:
        await session.close()


@router.post("/config/trailing-stop", response_model=MessageResponse)
async def update_trailing_stop_config(data: TrailingStopConfigUpdate):
    """更新移动止损配置"""
    import json
    session = await DatabaseManager.get_session()
    try:
        # 先获取现有配置
        result = await session.execute(
            select(SystemConfig).where(SystemConfig.key == "TRAILING_STOP_CONFIG")
        )
        config = result.scalar_one_or_none()
        
        # 合并配置
        existing_config = get_default_trailing_config()
        if config and config.value:
            try:
                existing_config = json.loads(config.value)
            except json.JSONDecodeError:
                pass
        
        # 更新提供的级别
        if data.level_1:
            existing_config["level_1"] = data.level_1.model_dump()
        if data.level_2:
            existing_config["level_2"] = data.level_2.model_dump()
        if data.level_3:
            existing_config["level_3"] = data.level_3.model_dump()
        
        # 保存到数据库
        config_value = json.dumps(existing_config)
        if config:
            config.value = config_value
            config.description = "移动止损级别配置"
        else:
            config = SystemConfig(
                key="TRAILING_STOP_CONFIG",
                value=config_value,
                description="移动止损级别配置"
            )
            session.add(config)
        
        await session.commit()
        
        # 通知配置变更
        await config_manager.notify_observers("trailing_stop_config_updated", existing_config)
        
        return MessageResponse(success=True, message="移动止损配置已更新")
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        await session.close()
