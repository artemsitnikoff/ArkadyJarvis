#!/usr/bin/env python3
"""Сгенерировать TELETHON_SESSION через QR-код (без СМС/звонка).

Запусти в НАСТОЯЩЕМ Терминале:
    /tmp/tgvenv/bin/python gen_session_qr.py

На экране появится QR. В Telegram на телефоне, где залогинен нужный аккаунт:
    Настройки → Устройства → Подключить устройство (десктоп) → отсканируй QR.
QR обновляется каждые ~30с (если протух — нарисуется новый). При 2FA скрипт
попросит пароль. В конце напечатает строку TELETHON_SESSION=... — её в .env на сервере.
"""
import asyncio

import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

API_ID = 33988209
API_HASH = "e88485f4f18cd2bee1f8552c655a9893"


def _show_qr(url: str) -> None:
    print("\033[2J\033[H", end="")  # очистить экран, чтобы был виден только свежий QR
    qr = qrcode.QRCode(border=2)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)
    print("\nTelegram на телефоне → Настройки → Устройства → "
          "Подключить устройство → сканируй QR ☝️")
    print("(QR живёт ~30с, при необходимости обновится сам)\n")


async def main() -> None:
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        qr_login = await client.qr_login()
        while True:
            _show_qr(qr_login.url)
            try:
                await qr_login.wait(timeout=30)
                break
            except asyncio.TimeoutError:
                await qr_login.recreate()
                continue
            except SessionPasswordNeededError:
                pw = input("Пароль 2FA (cloud password): ")
                await client.sign_in(password=pw)
                break
    me = await client.get_me()
    print(f"\n✅ Авторизован как @{me.username} (id={me.id})")
    print("\n=== Вставь эту строку в .env на сервере ===")
    print(f"TELETHON_SESSION={client.session.save()}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
