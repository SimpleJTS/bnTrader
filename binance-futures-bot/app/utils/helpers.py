"""
工具函数
"""
import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from typing import List


def setup_logging(level: str = "INFO", log_dir: str = "logs"):
    """
    配置日志
    
    Args:
        level: 日志级别
        log_dir: 日志文件目录
    """
    # 确保日志目录存在
    os.makedirs(log_dir, exist_ok=True)
    
    log_level = getattr(logging, level.upper())
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    
    # 创建根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # 清除已有的处理器（避免重复添加）
    root_logger.handlers.clear()
    
    # 创建格式化器
    formatter = logging.Formatter(log_format, datefmt=date_format)
    
    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # 文件处理器 - 每4小时分割一次
    log_file = os.path.join(log_dir, "bot.log")
    file_handler = TimedRotatingFileHandler(
        filename=log_file,
        when='H',           # 按小时分割
        interval=4,         # 每4小时
        backupCount=42,     # 保留42个备份文件（约7天的日志）
        encoding='utf-8'
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    file_handler.suffix = "%Y%m%d_%H%M%S"  # 备份文件后缀格式
    root_logger.addHandler(file_handler)
    
    # 禁用HTTP库的常规请求日志，只保留WARNING及以上级别（异常时会输出）
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    
    logging.info(f"日志系统初始化完成，日志目录: {log_dir}，分割周期: 4小时")


def format_price(price: float, decimals: int = 4) -> str:
    """格式化价格（固定小数位）"""
    return f"{price:.{decimals}f}"


def format_price_full(price: float) -> str:
    """格式化价格，保留完整精度，去除末尾无意义的0
    
    用于TG推送等需要显示实际价格的场景
    """
    # 使用足够精度格式化后去除末尾的0
    formatted = f"{price:.12f}".rstrip('0').rstrip('.')
    return formatted


def format_percent(value: float, decimals: int = 2) -> str:
    """格式化百分比"""
    return f"{value:.{decimals}f}%"


def timestamp_to_datetime(timestamp: int) -> datetime:
    """时间戳转datetime"""
    return datetime.fromtimestamp(timestamp / 1000)


def datetime_to_timestamp(dt: datetime) -> int:
    """datetime转时间戳"""
    return int(dt.timestamp() * 1000)
