import asyncio
import json
import os
import ssl
import sys
from typing import Optional, Tuple

import aiohttp
from playwright.async_api import async_playwright

from telegram_notifier import TelegramNotifier
from asf_core import validate_token_requests_fast
from asf_token_refresher import refresh_invalid_tokens

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'bot_config.json')
AKUN_PATH = os.path.join(os.path.dirname(__file__), 'akun.txt')
STATE_PATH = os.path.join(os.path.dirname(__file__), 'state.json')
TARGET_URL_DEFAULT = 'https://flip.gg/profile'

# Selectors (sinkron dengan dokumentasi rain.txt)
PRIZEBOX_ACTIVE = 'button:has(.tss-1msi2sy-prizeBox.active)'
BTN_ACTIVE = 'button.tss-pqm623-content.active'
JOIN_TEXT_ACTIVE = 'span.tss-7bx55w-rainStartedText.active'

CHECK_INTERVAL_SEC = 5  # loop cek active per 5 detik


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[WATCHER] Gagal simpan config: {e}")


def get_token_from_file(path: str) -> Optional[str]:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for raw in f:
                line = (raw or '').strip()
                if not line or line.startswith('#'):
                    continue
                # File Anda saat ini berisi token murni tanpa '='
                # Ambil baris apa adanya sebagai token
                return line
    except FileNotFoundError:
        print(f"[WATCHER] akun.txt tidak ditemukan: {path}")
    except Exception as e:
        print(f"[WATCHER] Error baca akun.txt: {e}")
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
    
    # SSL context toleran untuk RDP Windows
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    
    try:
        connector = aiohttp.TCPConnector(ssl=ssl_context, limit=10, limit_per_host=5)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    print(f"[WATCHER] API berhasil diakses (SSL toleran)")
                    return await resp.json()
                else:
                    print(f"[WATCHER] /api/user status: {resp.status}")
                    return None
    except Exception as e:
        print(f"[WATCHER] SSL toleran gagal: {e}")
        
        # Fallback: coba tanpa SSL verification
        try:
            print(f"[WATCHER] Mencoba fallback tanpa SSL verification...")
            connector = aiohttp.TCPConnector(ssl=False, limit=10, limit_per_host=5)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                # Gunakan HTTP sebagai fallback terakhir
                fallback_url = url.replace("https://", "http://")
                async with session.get(fallback_url, headers=headers) as resp:
                    if resp.status == 200:
                        print(f"[WATCHER] API berhasil diakses (HTTP fallback)")
                        return await resp.json()
                    else:
                        print(f"[WATCHER] /api/user status (fallback): {resp.status}")
                        return None
        except Exception as e2:
            print(f"[WATCHER] Semua metode gagal: {e2}")
            return None


async def validate_or_refresh_token(token: Optional[str], max_retries: int = 2) -> Optional[str]:
    """Validasi token cepat, jika invalid coba refresh via refresher dan baca ulang akun.txt. Batasi percobaan."""
    for attempt in range(max_retries + 1):
        if not token:
            print("[WATCHER] Token kosong.")
        else:
            ok, msg = validate_token_requests_fast(token)
            print(f"[WATCHER] Validasi token: {msg}")
            if ok:
                return token
        # Invalid → coba refresh (kecuali sudah di percobaan terakhir)
        if attempt < max_retries:
            print("[WATCHER] Token invalid, mulai refresh via asf_token_refresher...")
            try:
                await refresh_invalid_tokens(headless=True)
            except Exception as e:
                print(f"[WATCHER] refresh_invalid_tokens error: {e}")
            token = get_token_from_file(AKUN_PATH)
        else:
            break
    return None


async def send_telegram_if_available(cfg: dict, text: str) -> None:
    tok = (cfg.get('telegram_token') or '').strip()
    chat = (cfg.get('chat_id') or '').strip()
    if not tok or not chat:
        print(f"[WATCHER] Telegram tidak dikonfigurasi. Pesan: {text}")
        return
    try:
        notifier = TelegramNotifier(tok, chat)
        await notifier.send_message(text)
    except Exception as e:
        print(f"[WATCHER] Gagal kirim Telegram: {e}")


def set_fast_execute(cfg: dict, value: bool = True) -> None:
    try:
        current = bool(cfg.get('fast_execute', False))
        if current != value:
            cfg['fast_execute'] = value
            save_config(cfg)
            print(f"[WATCHER] fast_execute diset ke {value}")
    except Exception as e:
        print(f"[WATCHER] Gagal set fast_execute: {e}")


async def inject_token_init_script(context, token: str) -> None:
    # Injeksi token HANYA saat origin flip.gg + blokir semua interaksi (mode watcher, anti-klik)
    script = f"""
        (() => {{
            try {{
                if (location && location.hostname && location.hostname.endsWith('flip.gg')) {{
                    // Set token ke storage
                    localStorage.removeItem('token');
                    localStorage.setItem('token', '{token}');
                    try {{ sessionStorage.setItem('token', '{token}'); }} catch (e) {{}}

                    // MODE WATCHER: Nonaktifkan SEMUA interaksi agar tidak ada aksi klik apapun
                    try {{
                        const block = (e) => {{ try {{ e.stopImmediatePropagation(); e.stopPropagation(); e.preventDefault(); }} catch(_) {{}} }};
                        const events = ['click','mousedown','mouseup','pointerdown','pointerup','touchstart','touchend','keydown','keyup','submit'];
                        events.forEach(ev => {{
                            window.addEventListener(ev, block, true);
                            document.addEventListener(ev, block, true);
                        }});
                        // No-op untuk programmatic click
                        if (window.HTMLElement && window.HTMLElement.prototype) {{
                            try {{ window.HTMLElement.prototype.click = function() {{ /* suppressed by watcher */ }}; }} catch (_) {{}}
                        }}
                        // Cegah window.open membuat tab/menavigasi
                        try {{
                            const _open = window.open;
                            window.open = function(url, target, features) {{ try {{ /* jangan navigasi */ }} catch(_) {{}} return window; }};
                        }} catch(_) {{}}
                        // Cegah submit form eksplisit
                        try {{
                            document.addEventListener('submit', (e) => {{ try {{ e.preventDefault(); }} catch(_) {{}} }}, true);
                        }} catch(_) {{}}
                        // Opsi: hanya ubah cursor agar tidak mengganggu deteksi
                        try {{
                            const style = document.createElement('style');
                            style.id = 'watcher-no-pointer-events';
                            style.textContent = '* {{ cursor: default !important; }}';
                            document.documentElement.appendChild(style);
                        }} catch(_) {{}}
                        console.log('[WATCHER] Interaksi pengguna dinonaktifkan (mode pemantauan)');
                    }} catch(_) {{}}
                }}
            }} catch (e) {{}}
        }})()
    """
    await context.add_init_script(script)


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
        print(f"[WATCHER] Gagal simpan snapshot: {e}")


def read_fast_result() -> Optional[str]:
    path = os.path.join(os.path.dirname(__file__), 'fast_exec_result.json')
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # bersihkan agar tidak terbaca ulang di iterasi berikutnya
            try:
                os.remove(path)
            except Exception:
                pass
            return str(data.get('status') or '').strip().lower() or None
    except Exception:
        pass
    return None


async def run_executor(cfg: dict) -> int:
    """Jalankan start_gologin_and_bot.py dan tunggu selesai. Return exit code."""
    try:
        # Set fast_execute = True agar bot_cdp menjalankan mode cepat
        set_fast_execute(cfg, True)
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            os.path.join(os.path.dirname(__file__), 'start_gologin_and_bot.py'),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if stdout:
            print(stdout.decode(errors='ignore'))
        if stderr:
            print(stderr.decode(errors='ignore'))
        return proc.returncode or 0
    except Exception as e:
        print(f"[WATCHER] Gagal jalankan executor: {e}")
        return 1

async def ensure_gologin_prepared(cfg: dict) -> None:
    """Pastikan GoLogin aktif dan CDP siap tanpa menutupnya (prepare-only)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            os.path.join(os.path.dirname(__file__), 'start_gologin_and_bot.py'),
            '--prepare-only',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if stdout:
            print(stdout.decode(errors='ignore'))
        if stderr:
            print(stderr.decode(errors='ignore'))
        print(f"[WATCHER] GoLogin prepare-only selesai dengan kode: {proc.returncode}")
    except Exception as e:
        print(f"[WATCHER] Gagal prepare GoLogin: {e}")


async def watcher_main():
    cfg = load_config()
    target_url = cfg.get('target_url') or TARGET_URL_DEFAULT
    headless_mode = bool(cfg.get('headless', False))

    # 0) Pastikan GoLogin aktif 24/7 (prepare-only)
    await ensure_gologin_prepared(cfg)

    while True:  # nonstop loop
        # 1) Ambil token & validasi/recovery
        token = get_token_from_file(AKUN_PATH)
        token = await validate_or_refresh_token(token, max_retries=2)
        if not token:
            await send_telegram_if_available(cfg, '❌ Token tidak valid dan gagal refresh. Watcher berhenti sementara.')
            await asyncio.sleep(15)
            continue

        # 2) Launch Playwright biasa (injeksi JWT + cek active) - seperti sebelumnya
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless_mode)
            context = await browser.new_context()
            await inject_token_init_script(context, token)
            page = await context.new_page()
            try:
                print(f"[WATCHER] Navigasi ke {target_url}")
                await page.goto(target_url, wait_until='domcontentloaded', timeout=30000)
                # Tunggu halaman selesai loading sepenuhnya
                await asyncio.sleep(5)
                await send_telegram_if_available(cfg, f"🔎 Watcher aktif. Memantau tombol active setiap {CHECK_INTERVAL_SEC}s")
            except Exception as e:
                print(f"[WATCHER] Gagal buka target: {e}")

            # 3) Loop cek active per 5 detik
            active_found = False

            while True:
                try:
                    # urutan deteksi selector
                    if await page.locator(PRIZEBOX_ACTIVE).count() > 0:
                        active_found = True
                    elif await page.locator(BTN_ACTIVE).count() > 0:
                        active_found = True
                    else:
                        loc = page.locator(f'button:has({JOIN_TEXT_ACTIVE})')
                        if await loc.count() > 0:
                            active_found = True
                except Exception:
                    active_found = False

                if active_found:
                    print('[WATCHER] Active terdeteksi. Menjalankan executor GoLogin...')
                    await send_telegram_if_available(cfg, '🚦 Active terdeteksi. Menjalankan eksekusi GoLogin...')
                    break

                await asyncio.sleep(CHECK_INTERVAL_SEC)

            # Tutup watcher browser sebelum jalankan executor
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

        # 4) Jalankan Executor (GoLogin fast-exec) dan tunggu selesai
        rc = await run_executor(cfg)
        print(f"[WATCHER] Executor selesai dengan kode: {rc}")

        # 5) Baca hasil eksekusi cepat; jika success → jeda 3 menit sebelum cek active lagi
        result = read_fast_result()
        if result == 'success':
            await send_telegram_if_available(cfg, '⏳ Cooldown 3 menit dimulai. Tidak akan scan active selama 3 menit.')
            print('[WATCHER] Cooldown 3 menit dimulai setelah sukses join rain...')
            await asyncio.sleep(180)  # 3 menit = 180 detik
            await send_telegram_if_available(cfg, '��� Cooldown 3 menit selesai. Kembali ke mode scan active.')
            print('[WATCHER] Cooldown 3 menit selesai. Kembali ke mode scan active.')
        else:
            # Jika tidak ada hasil success dari fast_exec_result.json, 
            # tetap berikan jeda singkat untuk menghindari spam eksekusi
            print('[WATCHER] Tidak ada hasil success, memberikan jeda singkat 10 detik...')
            await asyncio.sleep(10)

        # 6) Kembali ke awal loop (nonstop)
        print('[WATCHER] Kembali ke mode pemantauan (loop nonstop).')
        await asyncio.sleep(2)


if __name__ == '__main__':
    try:
        asyncio.run(watcher_main())
    except KeyboardInterrupt:
        print('\n[WATCHER] Dihentikan oleh user')
    except Exception as e:
        print(f"[WATCHER] Fatal error: {e}")
