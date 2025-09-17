import asyncio
import aiohttp
import json
import ssl
from typing import Optional
from datetime import datetime

class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
    
    async def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """Mengirim pesan ke Telegram"""
        url = f"{self.base_url}/sendMessage"
        
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode
        }
        
        # Buat SSL context yang lebih toleran untuk RDP Windows
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        # Timeout untuk koneksi
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        
        try:
            # Coba dengan SSL context yang toleran
            connector = aiohttp.TCPConnector(ssl=ssl_context, limit=10, limit_per_host=5)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.post(url, json=payload) as response:
                    result = await response.json()
                    
                    if result.get("ok"):
                        print(f"[TELEGRAM] Pesan berhasil dikirim (SSL toleran)")
                        return True
                    else:
                        print(f"[TELEGRAM] Error: {result.get('description')}")
                        return False
                        
        except Exception as e:
            print(f"[TELEGRAM] SSL toleran gagal: {e}")
            
            # Fallback: coba tanpa SSL verification sama sekali
            try:
                print(f"[TELEGRAM] Mencoba fallback tanpa SSL verification...")
                connector = aiohttp.TCPConnector(ssl=False, limit=10, limit_per_host=5)
                async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                    # Gunakan HTTP sebagai fallback terakhir (tidak aman tapi berfungsi di RDP)
                    fallback_url = url.replace("https://", "http://")
                    async with session.post(fallback_url, json=payload) as response:
                        result = await response.json()
                        
                        if result.get("ok"):
                            print(f"[TELEGRAM] Pesan berhasil dikirim (HTTP fallback)")
                            return True
                        else:
                            print(f"[TELEGRAM] Error fallback: {result.get('description')}")
                            return False
                            
            except Exception as e2:
                print(f"[TELEGRAM] Semua metode gagal: {e2}")
                return False
    
    async def send_success_notification(self, details: dict) -> bool:
        """Mengirim notifikasi sukses join"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🎉 <b>BOT SUKSES JOIN!</b>

⏰ <b>Waktu:</b> {timestamp}
🌐 <b>Website:</b> {details.get('website', 'flip.gg')}
🎯 <b>Status:</b> ENTERED
✅ <b>Method:</b> {details.get('method', 'Auto')}

{details.get('extra_info', '')}
        """.strip()
        
        return await self.send_message(message)
    
    async def send_captcha_solved_notification(self, details: dict) -> bool:
        """Mengirim notifikasi captcha berhasil diselesaikan"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
🔓 <b>CAPTCHA SOLVED!</b>

⏰ <b>Waktu:</b> {timestamp}
🌐 <b>Website:</b> {details.get('website', 'flip.gg')}
🤖 <b>Solver:</b> Capsolver
⚡ <b>Type:</b> Cloudflare Turnstile
💰 <b>Cost:</b> ~$0.001
✅ <b>Status:</b> {details.get('status', 'Success')}

{details.get('extra_info', '')}
        """.strip()
        
        return await self.send_message(message)
    
    async def send_error_notification(self, error_msg: str, details: dict = None) -> bool:
        """Mengirim notifikasi error"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
❌ <b>BOT ERROR!</b>

⏰ <b>Waktu:</b> {timestamp}
🚨 <b>Error:</b> {error_msg}
        """.strip()
        
        if details:
            message += f"\n\n📋 <b>Details:</b>\n{json.dumps(details, indent=2)}"
        
        return await self.send_message(message)
    
    async def send_balance_notification(self, balance: float) -> bool:
        """Mengirim notifikasi saldo Capsolver"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        message = f"""
💰 <b>CAPSOLVER BALANCE</b>

⏰ <b>Waktu:</b> {timestamp}
💵 <b>Saldo:</b> ${balance:.4f}
        """.strip()
        
        if balance < 1.0:
            message += "\n\n⚠️ <b>WARNING:</b> Saldo rendah, silakan top up!"
        
        return await self.send_message(message)