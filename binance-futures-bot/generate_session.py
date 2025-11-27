#!/usr/bin/env python3
"""
Telegram Session 生成工具

用于生成 Telethon 的 session 字符串或 session 文件
在本地有交互式终端的环境下运行此脚本

使用方法:
1. 安装依赖: pip install telethon
2. 运行脚本: python generate_session.py
3. 按提示输入 API ID、API Hash 和手机号
4. 输入收到的验证码
5. 获取 StringSession 字符串，配置到 TG_SESSION_STRING 环境变量

或者生成 session 文件:
python generate_session.py --file
"""

import asyncio
import argparse
import sys


async def generate_string_session(api_id: int, api_hash: str):
    """生成 StringSession"""
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    
    print("\n正在创建 Telethon 客户端...")
    client = TelegramClient(StringSession(), api_id, api_hash)
    
    await client.start()
    
    session_string = client.session.save()
    print("\n" + "=" * 60)
    print("✅ StringSession 生成成功!")
    print("=" * 60)
    print("\n将以下字符串配置到 TG_SESSION_STRING 环境变量:\n")
    print(session_string)
    print("\n" + "=" * 60)
    
    await client.disconnect()
    return session_string


async def generate_session_file(api_id: int, api_hash: str, output_path: str):
    """生成 session 文件"""
    from telethon import TelegramClient
    
    print(f"\n正在创建 session 文件: {output_path}.session")
    client = TelegramClient(output_path, api_id, api_hash)
    
    await client.start()
    
    print("\n" + "=" * 60)
    print(f"✅ Session 文件生成成功: {output_path}.session")
    print("=" * 60)
    print("\n将此文件复制到 Docker 容器的 /app/data/ 目录下")
    print("或者通过 volume 挂载: -v ./tgsession.session:/app/data/tgsession.session")
    print("\n" + "=" * 60)
    
    await client.disconnect()


def main():
    parser = argparse.ArgumentParser(description='Telegram Session 生成工具')
    parser.add_argument('--file', action='store_true', 
                        help='生成 session 文件而不是 StringSession')
    parser.add_argument('--output', '-o', default='tgsession',
                        help='session 文件输出路径 (默认: tgsession)')
    parser.add_argument('--api-id', type=int, help='Telegram API ID')
    parser.add_argument('--api-hash', help='Telegram API Hash')
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Telegram Session 生成工具")
    print("=" * 60)
    
    # 获取 API 凭据
    if args.api_id:
        api_id = args.api_id
    else:
        api_id = int(input("\n请输入 Telegram API ID: "))
    
    if args.api_hash:
        api_hash = args.api_hash
    else:
        api_hash = input("请输入 Telegram API Hash: ")
    
    print("\n接下来会要求你输入手机号和验证码...")
    print("手机号格式示例: +8613800138000\n")
    
    try:
        if args.file:
            asyncio.run(generate_session_file(api_id, api_hash, args.output))
        else:
            asyncio.run(generate_string_session(api_id, api_hash))
    except KeyboardInterrupt:
        print("\n\n已取消")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
