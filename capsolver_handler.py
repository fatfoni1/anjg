import asyncio
import aiohttp
import json
import time
from typing import Optional, Dict, Any
from telegram_notifier import TelegramNotifier

class CapsolverHandler:
    def __init__(self, api_key: str, notifier: Optional[TelegramNotifier] = None):
        self.api_key = api_key
        self.base_url = "https://api.capsolver.com"
        self.notifier = notifier
        # Auto-attach notifier dari bot_config.json jika tidak diberikan
        if self.notifier is None:
            try:
                with open('bot_config.json', 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                tok = (cfg.get('telegram_token') or '').strip()
                chat = (cfg.get('chat_id') or '').strip()
                if tok and chat:
                    self.notifier = TelegramNotifier(tok, chat)
            except Exception:
                pass
        
    async def create_task(self, task_data: Dict[str, Any]) -> Optional[str]:
        """Membuat task baru di Capsolver dan mengembalikan task_id"""
        url = f"{self.base_url}/createTask"
        
        payload = {
            "clientKey": self.api_key,
            "task": task_data
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    result = await response.json()
                    
                    if result.get("errorId") == 0:
                        task_id = result.get("taskId")
                        if self.notifier:
                            await self.notifier.send_message(f"🧩 <b>CapSolver</b> → Task dibuat\nID: <code>{task_id}</code>")
                        else:
                            print(f"[CAPSOLVER] Task berhasil dibuat: {task_id}")
                        return task_id
                    else:
                        msg = result.get('errorDescription')
                        if self.notifier:
                            await self.notifier.send_message(f"❌ <b>CapSolver</b> → Gagal membuat task\nError: <code>{msg}</code>")
                        else:
                            print(f"[CAPSOLVER] Error membuat task: {msg}")
                        return None
                        
        except Exception as e:
            if self.notifier:
                await self.notifier.send_message(f"❌ <b>CapSolver</b> → Exception saat membuat task\n<code>{e}</code>")
            else:
                print(f"[CAPSOLVER] Exception saat membuat task: {e}")
            return None
    
    async def get_task_result(self, task_id: str, max_wait_time: int = 120) -> Optional[Dict[str, Any]]:
        """Mengambil hasil task dari Capsolver dengan polling"""
        url = f"{self.base_url}/getTaskResult"
        
        payload = {
            "clientKey": self.api_key,
            "taskId": task_id
        }
        
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload) as response:
                        result = await response.json()
                        
                        if result.get("errorId") == 0:
                            status = result.get("status")
                            
                            if status == "ready":
                                if self.notifier:
                                    await self.notifier.send_message("✅ <b>CapSolver</b> → Task selesai, token siap")
                                else:
                                    print("[CAPSOLVER] Task selesai!")
                                return result.get("solution")
                            elif status == "processing":
                                # Hindari spam notif, hanya tunggu
                                await asyncio.sleep(3)
                                continue
                            else:
                                if self.notifier:
                                    await self.notifier.send_message(f"❓ <b>CapSolver</b> → Status task tidak dikenal: <code>{status}</code>")
                                else:
                                    print(f"[CAPSOLVER] Status tidak dikenal: {status}")
                                return None
                        else:
                            msg = result.get('errorDescription')
                            if self.notifier:
                                await self.notifier.send_message(f"❌ <b>CapSolver</b> → Error mengambil hasil: <code>{msg}</code>")
                            else:
                                print(f"[CAPSOLVER] Error mengambil hasil: {msg}")
                            return None
                            
            except Exception as e:
                if self.notifier:
                    await self.notifier.send_message(f"❌ <b>CapSolver</b> → Exception polling hasil: <code>{e}</code>")
                else:
                    print(f"[CAPSOLVER] Exception saat mengambil hasil: {e}")
                await asyncio.sleep(3)
                continue
        
        if self.notifier:
            await self.notifier.send_message("⏱️ <b>CapSolver</b> → Timeout menunggu hasil task")
        else:
            print("[CAPSOLVER] Timeout menunggu hasil task")
        return None
    
    async def solve_turnstile(self, website_url: str, website_key: str, proxy: Optional[str] = None, action: str = "", cdata: str = "") -> Optional[str]:
        """Menyelesaikan Cloudflare Turnstile menggunakan Capsolver dengan implementasi yang benar"""
        if self.notifier:
            await self.notifier.send_message(
                f"🧩 <b>CapSolver</b> → Mulai solve Turnstile\nURL: <code>{website_url}</code>\nKey: <code>{website_key[:20]}...</code>"
            )
        else:
            print(f"[CAPSOLVER] Memulai solve Turnstile untuk {website_url}")
            print(f"[CAPSOLVER] Website Key: {website_key}")
        
        # Gunakan task type yang benar sesuai dokumentasi CapSolver (AntiTurnstileTaskProxyLess)
        task_data = {
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": website_url,
            "websiteKey": website_key,
            "metadata": {
                "action": action,  # Optional: data-action attribute
                "cdata": cdata     # Optional: data-cdata attribute
            }
        }
        
        # Jika ada proxy, gunakan TurnstileTask dengan proxy
        if proxy:
            task_data["type"] = "AntiTurnstileTask"
            task_data["proxy"] = proxy
            if self.notifier:
                await self.notifier.send_message(f"🌐 <b>CapSolver</b> → Menggunakan proxy untuk solve")
            else:
                print(f"[CAPSOLVER] Menggunakan proxy: {proxy}")
        
        # Buat task
        task_id = await self.create_task(task_data)
        if not task_id:
            if self.notifier:
                await self.notifier.send_message("❌ <b>CapSolver</b> → Gagal membuat task")
            else:
                print("[CAPSOLVER] Gagal membuat task")
            return None
        
        # Ambil hasil dengan timeout yang lebih lama untuk Turnstile
        solution = await self.get_task_result(task_id, max_wait_time=180)  # 3 menit timeout
        if solution and "token" in solution:
            token = solution["token"]
            if self.notifier:
                await self.notifier.send_message(
                    f"🔓 <b>CAPTCHA SOLVED</b> (CapSolver)\nToken: <code>{token[:42]}...</code>"
                )
                # Info tambahan (UA) jika ada
                if "userAgent" in solution:
                    await self.notifier.send_message(
                        f"🖥️ <b>User-Agent</b>\n<code>{solution['userAgent']}</code>"
                    )
            else:
                print(f"[CAPSOLVER] Token berhasil didapat: {token[:50]}...")
                if "userAgent" in solution:
                    print(f"[CAPSOLVER] User Agent: {solution['userAgent']}")
            
            return token
        
        if self.notifier:
            await self.notifier.send_message("❌ <b>CapSolver</b> → Gagal mendapatkan token dari CapSolver")
        else:
            print("[CAPSOLVER] Gagal mendapatkan token dari CapSolver")
        return None
    
    async def get_balance(self) -> Optional[float]:
        """Mengecek saldo Capsolver"""
        url = f"{self.base_url}/getBalance"
        
        payload = {
            "clientKey": self.api_key
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    result = await response.json()
                    
                    if result.get("errorId") == 0:
                        balance = result.get("balance", 0)
                        print(f"[CAPSOLVER] Saldo: ${balance}")
                        return float(balance)
                    else:
                        print(f"[CAPSOLVER] Error cek saldo: {result.get('errorDescription')}")
                        return None
                        
        except Exception as e:
            print(f"[CAPSOLVER] Exception saat cek saldo: {e}")
            return None