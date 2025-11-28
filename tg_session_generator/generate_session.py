#!/usr/bin/env python3
import os, asyncio
os.system('pip install telethon -q')
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

async def main():
    API_ID = os.environ['TG_API_ID']
    API_HASH = os.environ['TG_API_HASH']
    PHONE = os.environ['TG_PHONE']
    CODE = os.environ.get('TG_CODE', '')
    PASSWORD = os.environ.get('TG_PASSWORD', '')
    
    client = TelegramClient('/data/tg_session', int(API_ID), API_HASH)
    await client.connect()
    
    if not await client.is_user_authorized():
        if not CODE:
            await client.send_code_request(PHONE)
            print("\n✅ 验证码已发送!")
            print("请设置 TG_CODE 后重新运行\n")
            await client.disconnect()
            return
        try:
            await client.sign_in(PHONE, CODE)
        except SessionPasswordNeededError:
            if not PASSWORD:
                print("需要两步验证密码，请设置 TG_PASSWORD")
                return
            await client.sign_in(password=PASSWORD)
    
    me = await client.get_me()
    print(f"\n✅ 登录成功! 用户: {me.first_name} (ID: {me.id})")
    print("📁 Session文件: ./tg_session.session\n")
    await client.disconnect()

asyncio.run(main())
