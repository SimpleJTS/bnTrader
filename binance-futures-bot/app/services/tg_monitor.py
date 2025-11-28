# tg_oi_monitor.py
import asyncio
import re
import yaml
import os
import threading
from datetime import datetime
from telethon import TelegramClient, events

# ===================================================

class OIMonitor:
    def __init__(self):
        self.client = TelegramClient('tgsession', API_ID, API_HASH)
        self.running = False
        self.thread = None
        self.pattern = re.compile(r'([A-Z]{3,10}USDT).*?24H Price Change:\s*([+-]?\d+\.?\d*)%?', re.DOTALL)

        # 绑定事件处理器
        @self.client.on(events.NewMessage(chats=CHANNEL))
        async def handler(event):
            await self._process_message(event.message.message or "")

    async def _process_message(self, text: str):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now}] OI频道新消息：")
        print(text.strip())

        match = self.pattern.search(text)
        if not match:
            return

        symbol = match.group(1)
        try:
            change = float(match.group(2))
        except:
            return

        if change >= MIN_24H_CHANGE:
            print(f"24H 涨幅 {change}% ≥ {MIN_24H_CHANGE}% → 自动添加: {symbol}")
            self._add_or_enable_symbol(symbol)
        else:
            print(f"24H 涨幅 {change}% < {MIN_24H_CHANGE}% → 忽略")

    def _add_or_enable_symbol(self, symbol: str):
        config = self._load_config()
        symbols_list = config["trading"]["symbols"]
        existing = {s["symbol"] for s in symbols_list}

        now = datetime.now().strftime("%H:%M:%S")
        if symbol in existing:
            print(f"[{now}] 已存在，但被设置为亏损，需人工介入: {symbol}")
            #for item in symbols_list:
            #    if item["symbol"] == symbol:
            #        if not item.get("enabled", False):
            #            item["enabled"] = True
            #            self._save_config(config)
            #            print(f"[{now}] {symbol} 已存在 → 重新启用")
            #        break
        else:
            new_item = {"enabled": True, "leverage": DEFAULT_LEVERAGE, "symbol": symbol}
            symbols_list.insert(0, new_item)  # 新币插最前面
            self._save_config(config)
            print(f"[{now}] 涨幅超阈值 → 已添加并启用: {symbol}")

    def _load_config(self):
        if not os.path.exists(CONFIG_PATH):
            default = {"trading": {"default_leverage": 10, "symbols": []}}
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.safe_dump(default, f, allow_unicode=True, sort_keys=False)
            return default
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {"trading": {"default_leverage": 10, "symbols": []}}

    def _save_config(self, config):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False, indent=2)

    async def _run_client(self):
        await self.client.start(phone=PHONE)
        print(f"【OI监控】已连接，正在监听 {CHANNEL}")
        print(f"规则：24H Price Change ≥ {MIN_24H_CHANGE}% 自动加入 config.yaml")
        await self.client.run_until_disconnected()

    def _thread_target(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._run_client())

    # ================= 对外接口 =================
    def start(self):
        """后台启动监控（完全不阻塞主程序）"""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._thread_target, daemon=True)
        self.thread.start()

    def stop(self):
        """停止监控"""
        if self.running:
            self.running = False
            self.client.disconnect()
            print("【OI监控】已停止")

    def is_running(self):
        return self.running