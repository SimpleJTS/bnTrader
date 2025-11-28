#!/bin/bash
# Telegram Session 生成器 Docker 运行脚本
#
# 使用方法:
#   第一步 - 发送验证码:
#     ./run_docker.sh
#
#   第二步 - 输入验证码完成登录:
#     export TG_CODE=12345
#     ./run_docker.sh
#
# 需要设置的环境变量:
#   TG_API_ID     - Telegram API ID (必需)
#   TG_API_HASH   - Telegram API Hash (必需)
#   TG_PHONE      - 手机号,带国际区号 (必需)
#   TG_CODE       - 验证码 (第二步需要)
#   TG_PASSWORD   - 两步验证密码 (如果启用了两步验证)

set -e

# 检查必需的环境变量
if [ -z "$TG_API_ID" ]; then
    echo "错误: 请设置 TG_API_ID 环境变量"
    echo "  export TG_API_ID=你的API_ID"
    exit 1
fi

if [ -z "$TG_API_HASH" ]; then
    echo "错误: 请设置 TG_API_HASH 环境变量"
    echo "  export TG_API_HASH=你的API_HASH"
    exit 1
fi

if [ -z "$TG_PHONE" ]; then
    echo "错误: 请设置 TG_PHONE 环境变量"
    echo "  export TG_PHONE=+8613800138000"
    exit 1
fi

# 创建session目录
mkdir -p ./sessions

# 构建镜像
echo "正在构建 Docker 镜像..."
docker build -t tg-session-generator .

# 运行容器
echo "正在运行..."
docker run --rm \
    -v "$(pwd)/sessions:/app" \
    -e TG_API_ID="$TG_API_ID" \
    -e TG_API_HASH="$TG_API_HASH" \
    -e TG_PHONE="$TG_PHONE" \
    -e TG_CODE="${TG_CODE:-}" \
    -e TG_PASSWORD="${TG_PASSWORD:-}" \
    tg-session-generator

echo ""
echo "Session 文件保存在 ./sessions/ 目录下"
ls -la ./sessions/*.session 2>/dev/null || echo "(暂无session文件)"
