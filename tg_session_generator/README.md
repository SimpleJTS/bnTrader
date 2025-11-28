# Telegram Session 文件生成器

在 Docker / Linux 环境中生成 Telegram session 文件，解决 `EOF when reading a line` 问题。

## 快速开始 (Docker)

### 第一步：设置环境变量并发送验证码

```bash
# 设置 API 凭据 (从 https://my.telegram.org/apps 获取)
export TG_API_ID=你的API_ID
export TG_API_HASH=你的API_HASH
export TG_PHONE=+8613800138000

# 运行脚本
./run_docker.sh
```

### 第二步：输入验证码完成登录

```bash
# 设置收到的验证码
export TG_CODE=12345

# 如果有两步验证，还需要设置密码
export TG_PASSWORD=你的两步验证密码

# 再次运行
./run_docker.sh
```

### 第三步：获取 session 文件

成功后，session 文件会保存在 `./sessions/telegram_session.session`

---

## 手动 Docker 命令

如果不使用脚本，可以手动执行：

```bash
# 构建镜像
docker build -t tg-session .

# 第一步：发送验证码
docker run --rm -v $(pwd):/app \
    -e TG_API_ID=123456 \
    -e TG_API_HASH=abcdef \
    -e TG_PHONE=+8613800138000 \
    tg-session

# 第二步：输入验证码
docker run --rm -v $(pwd):/app \
    -e TG_API_ID=123456 \
    -e TG_API_HASH=abcdef \
    -e TG_PHONE=+8613800138000 \
    -e TG_CODE=12345 \
    tg-session
```

---

## 直接运行 Python (非 Docker)

```bash
# 安装依赖
pip install telethon

# 方式1: 环境变量
export TG_API_ID=123456
export TG_API_HASH=abcdef
export TG_PHONE=+8613800138000
python generate_session.py

# 方式2: 命令行参数
python generate_session.py \
    --api-id 123456 \
    --api-hash abcdef \
    --phone +8613800138000 \
    --code 12345
```

---

## 所有环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `TG_API_ID` | ✅ | Telegram API ID (数字) |
| `TG_API_HASH` | ✅ | Telegram API Hash |
| `TG_PHONE` | ✅ | 手机号，带国际区号 |
| `TG_CODE` | ⚠️ | 验证码 (发送后需要) |
| `TG_PASSWORD` | ❌ | 两步验证密码 (如果启用) |
| `TG_SESSION` | ❌ | Session 文件名，默认 `telegram_session` |

---

## 使用生成的 Session 文件

```python
from telethon import TelegramClient

# 使用已生成的 session 文件
client = TelegramClient('telegram_session', api_id, api_hash)

async def main():
    await client.connect()
    # 已经是登录状态，无需再次验证
    me = await client.get_me()
    print(f"已登录: {me.first_name}")

import asyncio
asyncio.run(main())
```

---

## 注意事项

- ⚠️ 请妥善保管 `.session` 文件
- ⚠️ 不要将 `.session` 文件提交到 Git
- 建议在 `.gitignore` 中添加 `*.session`
