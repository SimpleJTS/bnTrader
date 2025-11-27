# Binance Futures Trading Bot

一个基于EMA交叉策略的币安期货自动交易机器人，支持Web管理界面、Telegram通知、自动止损等功能。

## 🌟 功能特性

### 交易策略
- **EMA交叉策略**: EMA6与EMA51金叉做多，死叉做空
- **交叉过滤**: 前20根K线交叉次数<2次才开仓，过滤震荡行情
- **振幅过滤**: 近200根K线振幅<7%自动停止交易该币种

### 仓位管理
- **动态下单量**: 账户余额10% × 杠杆 / 当前价格
- **精度适配**: 自动获取交易所精度要求

### 移动止损
- **盈利2.5%~5%**: 止损提到成本价
- **盈利5%~10%**: 锁定约3%利润
- **盈利≥10%**: 锁定5%并启动追踪（回撤3%触发）

### WebSocket自愈
- 每分钟健康检查
- 5分钟无数据自动重连
- 每20小时全量重启避免24h限制

### Telegram集成
- 开仓/平仓通知
- 止损调整通知
- 振幅禁用通知
- 频道监听：自动添加24H涨幅30%的币种

### Web管理界面
- 交易对管理（增删改查）
- 实时仓位监控
- 交易日志查看
- API配置管理

## 🚀 快速开始

### 环境要求
- Docker 20.10+
- 或 Python 3.11+

### Docker部署（推荐）

```bash
# 1. 构建镜像
docker build -t binance-futures-bot .

# 2. 运行容器
docker run -d \
  --name binance-bot \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -e BINANCE_API_KEY=your_api_key \
  -e BINANCE_API_SECRET=your_api_secret \
  -e TG_BOT_TOKEN=your_bot_token \
  -e TG_CHAT_ID=your_chat_id \
  --restart unless-stopped \
  binance-futures-bot

# 3. 查看日志
docker logs -f binance-bot
```

### 本地开发

```bash
# 1. 克隆项目
cd binance-futures-bot

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 文件填入你的API密钥

# 5. 启动应用
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 📖 配置说明

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `BINANCE_API_KEY` | 币安API Key | - |
| `BINANCE_API_SECRET` | 币安API Secret | - |
| `BINANCE_TESTNET` | 是否使用测试网 | false |
| `TG_BOT_TOKEN` | Telegram Bot Token | - |
| `TG_CHAT_ID` | Telegram Chat ID | - |
| `TG_API_ID` | Telegram API ID（用于频道监听） | - |
| `TG_API_HASH` | Telegram API Hash | - |
| `DEFAULT_LEVERAGE` | 默认杠杆 | 10 |
| `DEFAULT_STRATEGY_INTERVAL` | 默认K线周期 | 15m |
| `DEFAULT_STOP_LOSS_PERCENT` | 默认止损百分比 | 2.0 |
| `POSITION_SIZE_PERCENT` | 仓位占账户余额比例 | 10.0 |

### 获取Telegram凭证

1. **Bot Token**: 与 [@BotFather](https://t.me/BotFather) 对话创建Bot
2. **Chat ID**: 与 [@userinfobot](https://t.me/userinfobot) 对话获取
3. **API ID/Hash** (可选): 在 [my.telegram.org](https://my.telegram.org) 获取

## 🎯 使用指南

### 1. 访问Web界面

启动后访问 `http://localhost:8000`

### 2. 配置API

在"配置"页面填入币安API和Telegram凭证

### 3. 添加交易对

在"交易对"页面点击"添加交易对"，例如：
- 交易对: `BTCUSDT`
- 杠杆: `10`
- 周期: `15m`
- 止损: `2%`

### 4. 监控交易

- 在"持仓"页面查看当前仓位
- 在"日志"页面查看交易记录
- 通过Telegram接收实时通知

## 📊 API文档

启动后访问 `http://localhost:8000/docs` 查看Swagger文档

### 主要接口

```
GET  /api/trading-pairs       # 获取交易对列表
POST /api/trading-pairs       # 添加交易对
PUT  /api/trading-pairs/{symbol}  # 更新交易对
DELETE /api/trading-pairs/{symbol} # 删除交易对

GET  /api/positions           # 获取仓位列表
POST /api/positions/{symbol}/close # 手动平仓

GET  /api/trade-logs          # 获取交易日志
GET  /api/account/balance     # 获取账户余额
GET  /api/websocket/status    # 获取WebSocket状态
```

## ⚠️ 风险提示

1. **风险警告**: 期货交易具有高风险，可能导致本金全部损失
2. **测试建议**: 建议先使用测试网进行充分测试
3. **API权限**: 仅开启现货和期货交易权限，不要开启提现权限
4. **资金管理**: 建议使用小额资金运行

## 🏗️ 项目结构

```
binance-futures-bot/
├── app/
│   ├── __init__.py
│   ├── main.py              # 主入口
│   ├── config.py            # 配置管理
│   ├── database.py          # 数据库
│   ├── models.py            # 数据模型
│   ├── api/
│   │   ├── routes.py        # API路由
│   │   └── schemas.py       # 请求响应模型
│   ├── services/
│   │   ├── binance_api.py   # 币安API
│   │   ├── binance_ws.py    # WebSocket管理
│   │   ├── strategy.py      # 交易策略
│   │   ├── position_manager.py # 仓位管理
│   │   ├── trailing_stop.py # 移动止损
│   │   └── telegram.py      # Telegram服务
│   ├── templates/
│   │   └── index.html       # Web界面
│   └── utils/
│       └── helpers.py       # 工具函数
├── data/                    # 数据目录
├── Dockerfile
├── requirements.txt
└── README.md
```

## 🔧 常见问题

### Q: WebSocket经常断连？
A: 这是正常现象，Bot会自动重连。如果频繁断连，检查网络连接。

### Q: 交易对显示"振幅禁用"？
A: 该币种近200根K线振幅<7%，已自动停止交易以避免低波动行情。

### Q: 如何使用测试网？
A: 设置环境变量 `BINANCE_TESTNET=true`，使用测试网API密钥。

## 📝 更新日志

### v1.0.0
- 初始版本发布
- EMA交叉策略
- 移动止损系统
- Web管理界面
- Telegram通知

## 📄 许可证

MIT License

---

**免责声明**: 本项目仅供学习交流，不构成投资建议。使用本软件进行交易的风险由用户自行承担。
