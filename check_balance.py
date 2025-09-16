import asyncio
import json
import os
import sys
from typing import Optional, Tuple

import aiohttp
from telegram_notifier import TelegramNotifier

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'bot_config.json')
AKUN_PATH = os.path.join(os.path.dirname(__file__), 'akun.txt')
STATE_PATH = os.path.join(os.path.dirname(__file__), 'state.json')

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

def get_token_from_file(path: str) -> Optional[str]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for raw in f:
                line = (raw or '').strip()
                if not line or line.startswith('#'):
                    continue
                return line
    except FileNotFoundError:
        print(f"[BALANCE] akun.txt tidak ditemukan: {path}")
    except Exception as e:
        print(f"[BALANCE] Error baca akun.txt: {e}")
    return None

def safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

async def api_get_user_state(token: str) -> Optional[dict]:
    """Ambil data user via API langsung untuk wallet/WNFW. Return dict JSON atau None."""
    url = 'https://api.flip.gg/api/user'
    headers = {'x-auth-token': token}
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    print(f"[BALANCE] /api/user status: {resp.status}")
                    return None
    except Exception as e:
        print(f"[BALANCE] API error: {e}")
        return None

async def capture_wallet_and_wnfw(token: str) -> Tuple[Optional[float], Optional[float]]:
    data = await api_get_user_state(token)
    if not data or not isinstance(data, dict) or 'user' not in data:
        return None, None
    user = data.get('user') or {}
    wallet = safe_float(user.get('wallet'))
    wnfw = safe_float(user.get('wagerNeededForWithdraw'))
    return wallet, wnfw

def load_snapshot() -> Tuple[Optional[float], Optional[float]]:
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, 'r', encoding='utf-8') as f:
                s = json.load(f)
                return safe_float(s.get('wallet')), safe_float(s.get('wnfw'))
    except Exception:
        pass
    return None, None

def save_snapshot(wallet: Optional[float], wnfw: Optional[float]) -> None:
    try:
        with open(STATE_PATH, 'w', encoding='utf-8') as f:
            json.dump({'wallet': wallet, 'wnfw': wnfw}, f, indent=2)
    except Exception as e:
        print(f"[BALANCE] Gagal simpan snapshot: {e}")

async def send_telegram_if_available(cfg: dict, text: str) -> None:
    tok = (cfg.get('telegram_token') or '').strip()
    chat = (cfg.get('chat_id') or '').strip()
    if not tok or not chat:
        print(f"[BALANCE] Telegram tidak dikonfigurasi. Pesan: {text}")
        return
    try:
        notifier = TelegramNotifier(tok, chat)
        await notifier.send_message(text)
    except Exception as e:
        print(f"[BALANCE] Gagal kirim Telegram: {e}")

async def check_balance_main():
    """Fungsi utama untuk check saldo manual"""
    cfg = load_config()
    
    # Ambil token
    token = get_token_from_file(AKUN_PATH)
    if not token:
        print("[BALANCE] Token tidak ditemukan di akun.txt")
        await send_telegram_if_available(cfg, '❌ Token tidak ditemukan untuk check saldo.')
        return
    
    print("[BALANCE] Mengambil data saldo dan WNFW...")
    
    # Ambil saldo saat ini
    current_wallet, current_wnfw = await capture_wallet_and_wnfw(token)
    if current_wallet is None or current_wnfw is None:
        print("[BALANCE] Gagal mengambil data saldo dari API")
        await send_telegram_if_available(cfg, '❌ Gagal mengambil data saldo dari API flip.gg')
        return
    
    # Ambil snapshot terakhir untuk perbandingan
    old_wallet, old_wnfw = load_snapshot()
    
    if old_wallet is not None and old_wnfw is not None:
        # Hitung perubahan
        d_wallet = current_wallet - old_wallet
        d_wnfw = current_wnfw - old_wnfw
        sign_wallet = '+' if d_wallet >= 0 else ''
        sign_wnfw = '+' if d_wnfw >= 0 else ''
        
        message = (
            f"💰 <b>CHECK SALDO MANUAL</b>\n\n"
            f"📊 Saldo: {current_wallet:.8f} ({sign_wallet}{d_wallet:.8f})\n"
            f"🎯 WNFW: {current_wnfw:.8f} ({sign_wnfw}{d_wnfw:.8f})\n"
            f"⏰ Waktu: {asyncio.get_event_loop().time()}"
        )
        
        print(f"[BALANCE] Saldo: {current_wallet:.8f} ({sign_wallet}{d_wallet:.8f})")
        print(f"[BALANCE] WNFW: {current_wnfw:.8f} ({sign_wnfw}{d_wnfw:.8f})")
    else:
        message = (
            f"💰 <b>CHECK SALDO MANUAL</b>\n\n"
            f"📊 Saldo: {current_wallet:.8f}\n"
            f"🎯 WNFW: {current_wnfw:.8f}\n"
            f"⏰ Waktu: {asyncio.get_event_loop().time()}"
        )
        
        print(f"[BALANCE] Saldo: {current_wallet:.8f}")
        print(f"[BALANCE] WNFW: {current_wnfw:.8f}")
    
    # Kirim ke Telegram
    await send_telegram_if_available(cfg, message)
    
    # Update snapshot
    save_snapshot(current_wallet, current_wnfw)
    print("[BALANCE] Snapshot saldo berhasil diupdate")

if __name__ == '__main__':
    try:
        asyncio.run(check_balance_main())
    except KeyboardInterrupt:
        print('\n[BALANCE] Dihentikan oleh user')
    except Exception as e:
        print(f"[BALANCE] Fatal error: {e}")