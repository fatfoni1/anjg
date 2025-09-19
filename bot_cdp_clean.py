import asyncio, time, random, json, re, os
from playwright.async_api import async_playwright, TimeoutError as PWTimeout
from capsolver_handler import CapsolverHandler
from telegram_notifier import TelegramNotifier

# Pastikan stdout/stderr aman di Windows RDP: pakai UTF-8 dan hindari crash pada karakter non-ASCII
import sys as _sys
try:
    _sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    _sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

# ASF modules tidak digunakan dalam implementasi saat ini
ASF_AVAILABLE = False

# ================== LOAD CONFIG ==================
def load_config():
    try:
        with open('bot_config.json', 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"[CONFIG] Error loading config: {e}")
        return {}

config = load_config()
FAST_EXECUTE = bool(config.get("fast_execute", False))
MAX_CF_RELOAD = 2

def _save_fast_result(status: str):
    try:
        path = os.path.join(os.path.dirname(__file__), 'fast_exec_result.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({"status": status, "ts": time.time()}, f)
    except Exception as e:
        print(f"[FAST-RESULT] Gagal simpan hasil: {e}")

# ================== KONFIG ==================
CDP_URL = config.get("cdp_url", "http://127.0.0.1:9222")
TARGET_URL = config.get("target_url", "https://flip.gg/profile")
CHECK_INTERVAL_SEC = config.get("check_interval_sec", 5)
RELOAD_EVERY_SEC = config.get("reload_every_sec", 300)
TURNSTILE_WAIT_MS = config.get("turnstile_wait_ms", 600000)
AUTO_SOLVE_CAPTCHA = config.get("auto_solve_captcha", True)

# Initialize handlers
capsolver = None
telegram = None

if config.get("capsolver_token") and config.get("capsolver_token") != "MASUKKAN_API_KEY_CAPSOLVER_DISINI":
    capsolver = CapsolverHandler(config.get("capsolver_token"))
    print("[INIT] Capsolver handler initialized")

if config.get("telegram_token") and config.get("chat_id"):
    telegram = TelegramNotifier(config.get("telegram_token"), config.get("chat_id"))
    print(f"[INIT] Telegram notifier initialized - Token: {config.get('telegram_token')[:10]}... Chat ID: {config.get('chat_id')}")
    
    # Test koneksi Telegram saat startup
    try:
        import asyncio
        async def test_telegram():
            try:
                result = await telegram.send_message("ðŸ¤– <b>BOT STARTUP</b>\n\nBot berhasil diinisialisasi dan siap bekerja!")
                if result:
                    print("[INIT] âœ… Test Telegram berhasil!")
                else:
                    print("[INIT] âŒ Test Telegram gagal!")
            except Exception as e:
                print(f"[INIT] âŒ Error test Telegram: {e}")
        
        # Jalankan test dalam event loop yang ada atau buat baru
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(test_telegram())
            else:
                loop.run_until_complete(test_telegram())
        except:
            # Fallback jika tidak ada event loop
            asyncio.run(test_telegram())
    except Exception as e:
        print(f"[INIT] âŒ Error saat test Telegram: {e}")
else:
    print("[INIT] âš ï¸ Telegram tidak diinisialisasi - token atau chat_id tidak tersedia")
    if not config.get("telegram_token"):
        print("[INIT] âŒ Telegram token tidak ditemukan dalam config")
    if not config.get("chat_id"):
        print("[INIT] âŒ Chat ID tidak ditemukan dalam config")

async def send_event(message: str):
    """Kirim event feed sederhana ke Telegram (jika tersedia)."""
    if telegram:
        try:
            await telegram.send_message(f"ðŸ”” {message}")
        except Exception as e:
            print(f"[TELEGRAM] Error sending event: {e}")

async def send_telegram_log(message: str, level: str = "INFO"):
    """Kirim log langsung ke Telegram dengan level yang berbeda"""
    if telegram:
        try:
            emoji_map = {
                "INFO": "â„¹ï¸",
                "SUCCESS": "âœ…", 
                "WARNING": "âš ï¸",
                "ERROR": "âŒ",
                "DEBUG": "ðŸ”"
            }
            emoji = emoji_map.get(level, "ðŸ“")
            full_message = f"{emoji} <b>[{level}]</b> {message}"
            
            # Debug logging untuk memastikan pesan dikirim
            print(f"[TELEGRAM_LOG] Mengirim pesan: {full_message}")
            
            result = await telegram.send_message(full_message)
            if result:
                print(f"[TELEGRAM_LOG] âœ… Pesan berhasil dikirim!")
            else:
                print(f"[TELEGRAM_LOG] âŒ Pesan gagal dikirim!")
                
        except Exception as e:
            print(f"[TELEGRAM] Error sending log: {e}")
            # Fallback ke print jika telegram gagal
            print(f"[{level}] {message}")
    else:
        # Fallback ke print jika telegram tidak tersedia
        print(f"[{level}] {message}")
        print("[TELEGRAM_LOG] âš ï¸ Telegram tidak tersedia - hanya print ke console")

# === Selector kunci ===
BTN_ACTIVE = 'button.tss-pqm623-content.active'
PRIZEBOX_ACTIVE = 'button:has(.tss-1msi2sy-prizeBox.active)'
JOIN_TEXT_ACTIVE = 'span.tss-7bx55w-rainStartedText.active'

# turnstile selectors
IFRAME_TURNSTILE = (
    'iframe[src*="challenges.cloudflare.com"], '
    'iframe[src*="challenge-platform"], '
    'iframe[id^="cf-chl-widget-"] , '
    'iframe[title*="Cloudflare"], '
    'iframe[title*="Turnstile"], '
    'iframe[title*="security challenge"], '
    'iframe[title*="tantangan"], '
    'iframe[title*="tantangan keamanan"]'
)
TURNSTILE_INPUT = 'input[name="cf-turnstile-response"]'

# Deteksi status khusus Turnstile untuk membedakan CRASH vs LOADING
async def is_turnstile_crashed(page) -> bool:
    """Heuristik crash khusus frame Turnstile/Cloudflare.
    Mengembalikan True jika frame Turnstile tidak bisa dievaluasi (context destroyed, frame detached, target closed).
    """
    try:
        for frame in page.frames:
            u = (frame.url or "").lower()
            if any(k in u for k in ["challenges.cloudflare.com", "turnstile", "cloudflare"]):
                try:
                    # Uji eksekusi sederhana pada frame
                    _ = await frame.evaluate("() => 42")
                except Exception as e:
                    msg = str(e).lower()
                    if any(k in msg for k in [
                        "execution context was destroyed",
                        "frame was detached",
                        "target closed",
                        "context destroyed",
                    ]):
                        print(f"[CF] Heuristik crash Turnstile: {e}")
                        return True
        return False
    except Exception:
        return False

async def is_turnstile_loading(page) -> bool:
    """Deteksi kondisi LOADING (bukan crash) pada widget Turnstile.
    True jika iframe ada dan indikator loading/disable terdeteksi, atau checkbox ada tapi disabled.
    """
    try:
        count = await page.locator(IFRAME_TURNSTILE).count()
        if count <= 0:
            return False
        fl = page.frame_locator(IFRAME_TURNSTILE)
        # Indikasi loading umum
        candidates = [
            'div[class*="spinner"]', '.spinner', '.loading', '[aria-busy="true"]',
            'div[role="progressbar"]', 'div[aria-live="polite"]'
        ]
        for sel in candidates:
            try:
                el = fl.locator(sel).first
                if await el.is_visible():
                    return True
            except Exception:
                pass
        # Checkbox ada namun disabled
        try:
            cb = fl.locator('input[type="checkbox"]').first
            if await cb.count() > 0:
                disabled = await cb.get_attribute('disabled')
                aria = await cb.get_attribute('aria-disabled')
                if disabled is not None or (aria and aria.lower() == 'true'):
                    return True
        except Exception:
            pass
        return False
    except Exception:
        return False

# Success/notification selectors - diperluas untuk deteksi yang lebih baik
SUCCESS_SELECTORS = [
    '.success-message',
    '.notification.success',
    '[class*="success"]',
    '.alert-success',
    '.toast-success',
    'div:has-text("Success")',
    'div:has-text("Joined")',
    'div:has-text("Entered")',
    'div:has-text("successfully")',
    'div:has-text("Successfully")',
    'span:has-text("successfully")',
    'span:has-text("Successfully")',
    '.tss-success',
    '[data-success="true"]',
    '.notification:has-text("success")',
    '.toast:has-text("success")',
    '[class*="notification"]:has-text("success")'
]

# Already joined selectors
ALREADY_JOINED_SELECTORS = [
    'div:has-text("already")',
    'span:has-text("already")',
    'div:has-text("Already")',
    'span:has-text("Already")',
    '.already-joined',
    '[class*="already"]',
    'div:has-text("sudah join")',
    'div:has-text("sudah bergabung")',
    '.notification:has-text("already")',
    '.toast:has-text("already")'
]

def now(): return time.time()

def set_already_joined_cooldown():
    """Set cooldown 3 menit untuk already joined - digunakan dalam handle_turnstile_challenge"""
    # Fungsi ini dipanggil dari handle_turnstile_challenge untuk kompatibilitas
    print(f"[ALREADY] Cooldown 3 menit dimulai pada {time.strftime('%H:%M:%S', time.localtime())}")

async def check_already_joined(page):
    """Cek apakah sudah join sebelumnya (already joined)"""
    # Kurangi log spam - hanya log saat benar-benar menemukan already joined
    
    try:
        # Cek di main page
        for selector in ALREADY_JOINED_SELECTORS:
            if await page.locator(selector).count() > 0:
                element = page.locator(selector).first
                if await element.is_visible():
                    text = await element.text_content()
                    await send_telegram_log(f"â„¹ï¸ Already joined terdeteksi: {text}", "INFO")
                    return True
        
        # Cek di semua frame
        for frame in page.frames:
            try:
                for selector in ALREADY_JOINED_SELECTORS:
                    if await frame.locator(selector).count() > 0:
                        element = frame.locator(selector).first
                        if await element.is_visible():
                            text = await element.text_content()
                            await send_telegram_log(f"â„¹ï¸ Already joined terdeteksi di frame: {text}", "INFO")
                            return True
            except Exception:
                continue
        
        # Cek keyword already
        already_keywords = ["already", "sudah", "duplicate", "participated", "entered before"]
        for keyword in already_keywords:
            if await page.locator(f'text=/{keyword}/i').count() > 0:
                await send_telegram_log(f"â„¹ï¸ Keyword already ditemukan: {keyword}", "INFO")
                return True
                
            # Cek juga di frame
            for frame in page.frames:
                try:
                    if await frame.locator(f'text=/{keyword}/i').count() > 0:
                        await send_telegram_log(f"â„¹ï¸ Keyword already ditemukan di frame: {keyword}", "INFO")
                        return True
                except Exception:
                    continue
                    
    except Exception as e:
        await send_telegram_log(f"âŒ Error saat cek already joined: {e}", "ERROR")
    
    # Tidak perlu log jika tidak ada already joined - kurangi spam
    return False


async def check_page_crashed(page):
    """Cek apakah halaman crashed"""
    try:
        # Cek apakah page masih responsif
        await page.evaluate("() => document.title", timeout=5000)
        return False
    except Exception as e:
        print(f"[CRASH] Page crashed terdeteksi: {e}")
        return True

async def page_reload_if_needed(page, last_reload_ts):
    t = now()
    current_url = page.url
    
    # Cek apakah page crashed
    is_crashed = await check_page_crashed(page)
    
    # Cek berbagai kondisi yang memerlukan reload
    need_reload = (
        is_crashed or
        current_url.startswith("about:blank") or 
        current_url == "chrome://newtab/" or
        current_url == "" or
        "chrome-error://" in current_url or
        (t - last_reload_ts > RELOAD_EVERY_SEC)
    )
    
    if need_reload:
        if is_crashed:
            print("[RELOAD] Page crashed terdeteksi â†’ reload Flip sekarang")
            await send_event("Reload halaman karena crash")
        else:
            print(f"[RELOAD] Halaman perlu di-reload. URL saat ini: {current_url}")
            await send_event("Reload halaman dipicu")
        
        # Retry mechanism untuk reload
        max_retries = 3
        for retry in range(max_retries):
            try:
                print(f"[RELOAD] Percobaan reload #{retry + 1}")
                await page.reload(wait_until="domcontentloaded", timeout=30000)
                print(f"[LOAD] Reload berhasil dimuat")
                
                # Tunggu sebentar untuk memastikan halaman fully loaded
                await asyncio.sleep(3)
                
                # Verifikasi halaman berhasil dimuat
                final_url = page.url
                if final_url.startswith("about:blank") or "chrome-error://" in final_url:
                    print("[RELOAD] Halaman masih blank setelah reload, coba lagi...")
                    await asyncio.sleep(5)
                    await page.reload(wait_until="networkidle", timeout=30000)
                    await asyncio.sleep(2)
                
                # Test apakah page responsif
                if not await check_page_crashed(page):
                    print("[RELOAD] Reload berhasil, page responsif")
                    return now()
                else:
                    print(f"[RELOAD] Page masih crashed setelah reload #{retry + 1}")
                    if retry < max_retries - 1:
                        await asyncio.sleep(5)
                        continue
                
            except Exception as e:
                print(f"[RELOAD] Error saat reload #{retry + 1}: {e}")
                if retry < max_retries - 1:
                    print(f"[RELOAD] Mencoba reload ulang dalam 10 detik...")
                    await asyncio.sleep(10)
                    continue
                else:
                    print("[RELOAD] Semua percobaan reload gagal")
                    return last_reload_ts
        
        return now()
    
    return last_reload_ts

async def detect_active(page):
    """Balikin selector tombol yang valid kalau 'active' terdeteksi; else None."""
    
    # Cek apakah halaman sudah dimuat dengan benar
    current_url = page.url
    if current_url.startswith("about:blank") or "chrome-error://" in current_url or current_url == "":
        return None
    
    # Cek apakah halaman flip.gg sudah dimuat
    if "flip.gg" not in current_url:
        return None
    
    try:
        if await page.locator(PRIZEBOX_ACTIVE).count() > 0:
            await page.locator(PRIZEBOX_ACTIVE).first.wait_for(state="visible", timeout=1500)
            await send_telegram_log("ðŸŽ¯ RAIN ACTIVE TERDETEKSI! (prizeBox)", "SUCCESS")
            return PRIZEBOX_ACTIVE
    except PWTimeout:
        pass
    except Exception as e:
        pass

    try:
        if await page.locator(BTN_ACTIVE).count() > 0:
            await page.locator(BTN_ACTIVE).first.wait_for(state="visible", timeout=1500)
            await send_telegram_log("ðŸŽ¯ RAIN ACTIVE TERDETEKSI! (button)", "SUCCESS")
            return BTN_ACTIVE
    except PWTimeout:
        pass
    except Exception as e:
        pass

    try:
        loc = page.locator(f'button:has({JOIN_TEXT_ACTIVE})').first
        if await loc.count() > 0:
            await loc.wait_for(state="visible", timeout=1500)
            await send_telegram_log("ðŸŽ¯ RAIN ACTIVE TERDETEKSI! (join text)", "SUCCESS")
            return f'button:has({JOIN_TEXT_ACTIVE})'
    except PWTimeout:
        pass
    except Exception as e:
        pass

    return None

async def click_join(page, btn_selector):
    """Klik tombol berdasarkan selector yang diberikan."""
    try:
        btn = page.locator(btn_selector).first
        await btn.scroll_into_view_if_needed()
        await btn.click()
        await send_telegram_log(f"âœ… Berhasil klik tombol Rain: {btn_selector}", "SUCCESS")
        return True
    except Exception as e:
        await send_telegram_log(f"âŒ Gagal klik tombol Rain: {e}", "ERROR")
        return False

async def detect_success_notification(page, timeout_sec=15):
    """Deteksi apakah ada notifikasi sukses setelah join - cek di semua frame"""
    print("[SUCCESS] Mengecek notifikasi sukses di semua frame...")
    
    for i in range(timeout_sec):
        try:
            # Cek di main page
            for selector in SUCCESS_SELECTORS:
                if await page.locator(selector).count() > 0:
                    element = page.locator(selector).first
                    if await element.is_visible():
                        text = await element.text_content()
                        print(f"[SUCCESS] Notifikasi sukses ditemukan di main page: {text}")
                        return True
            
            # Cek di semua frame/iframe
            for frame in page.frames:
                try:
                    for selector in SUCCESS_SELECTORS:
                        if await frame.locator(selector).count() > 0:
                            element = frame.locator(selector).first
                            if await element.is_visible():
                                text = await element.text_content()
                                print(f"[SUCCESS] Notifikasi sukses ditemukan di frame {frame.url}: {text}")
                                return True
                except Exception:
                    continue
            
            # Check for any text containing success keywords di main page
            success_keywords = ["successfully", "success", "joined", "entered", "complete", "done", "berhasil"]
            for keyword in success_keywords:
                if await page.locator(f'text=/{keyword}/i').count() > 0:
                    print(f"[SUCCESS] Keyword sukses ditemukan di main page: {keyword}")
                    return True
                    
                # Cek juga di semua frame
                for frame in page.frames:
                    try:
                        if await frame.locator(f'text=/{keyword}/i').count() > 0:
                            print(f"[SUCCESS] Keyword sukses ditemukan di frame {frame.url}: {keyword}")
                            return True
                    except Exception:
                        continue
                    
        except Exception as e:
            print(f"[SUCCESS] Error checking success: {e}")
        
        await asyncio.sleep(1)
    
    print("[SUCCESS] Tidak ada notifikasi sukses ditemukan")
    return False


async def extract_turnstile_info(page):
    """Extract website key dan URL untuk Turnstile dengan pencarian yang lebih komprehensif"""
    try:
        website_url = page.url
        sitekey = None
        action = ""
        cdata = ""
        
        print("[TURNSTILE] Mencari sitekey dan metadata di semua frame dan elemen...")
        
        # 1. Cek di iframe Turnstile tradisional
        iframe_count = await page.locator(IFRAME_TURNSTILE).count()
        if iframe_count > 0:
            iframe_src = await page.locator(IFRAME_TURNSTILE).first.get_attribute('src')
            if iframe_src:
                sitekey_match = re.search(r'sitekey=([^&]+)', iframe_src)
                if sitekey_match:
                    sitekey = sitekey_match.group(1)
                    print(f"[TURNSTILE] Sitekey ditemukan di iframe src: {sitekey}")
                else:
                    # Dukungan pola sitekey di PATH, contoh: .../0x4AAAAAAAGPyRCsiqTNqbBd/dark/...
                    path_key = re.search(r'(0x[0-9A-Za-z]{16,})', iframe_src)
                    if path_key:
                        sitekey = path_key.group(1)
                        print(f"[TURNSTILE] Sitekey (PATH) ditemukan di iframe src: {sitekey}")
        
        # 2. Cek di elemen dengan data-sitekey di main page
        if not sitekey:
            sitekey_selectors = [
                '[data-sitekey]',
                '[data-site-key]',
                '#cf-turnstile[data-sitekey]',
                '.cf-turnstile[data-sitekey]',
                'div[data-sitekey]',
                'iframe[data-sitekey]',
                '.cf-turnstile',
                '#cf-turnstile'
            ]
            
            for selector in sitekey_selectors:
                try:
                    if await page.locator(selector).count() > 0:
                        element = page.locator(selector).first
                        sitekey = await element.get_attribute('data-sitekey') or await element.get_attribute('data-site-key')
                        
                        # Ambil metadata tambahan jika ada
                        if not action:
                            action = await element.get_attribute('data-action') or ""
                        if not cdata:
                            cdata = await element.get_attribute('data-cdata') or ""
                            
                        if sitekey:
                            print(f"[TURNSTILE] Sitekey ditemukan di main page: {sitekey}")
                            if action:
                                print(f"[TURNSTILE] Action ditemukan: {action}")
                            if cdata:
                                print(f"[TURNSTILE] CData ditemukan: {cdata}")
                            break
                except Exception:
                    continue
        
        # 3. Cek di semua frame
        if not sitekey:
            for frame in page.frames:
                try:
                    frame_url = frame.url or ""
                    print(f"[TURNSTILE] Mengecek sitekey di frame: {frame_url}")
                    
                    # Cek di elemen dengan data-sitekey di frame
                    for selector in sitekey_selectors:
                        try:
                            if await frame.locator(selector).count() > 0:
                                element = frame.locator(selector).first
                                sitekey = await element.get_attribute('data-sitekey') or await element.get_attribute('data-site-key')
                                
                                # Ambil metadata tambahan jika ada
                                if not action:
                                    action = await element.get_attribute('data-action') or ""
                                if not cdata:
                                    cdata = await element.get_attribute('data-cdata') or ""
                                    
                                if sitekey:
                                    print(f"[TURNSTILE] Sitekey ditemukan di frame {frame_url}: {sitekey}")
                                    break
                        except Exception:
                            continue
                    
                    if sitekey:
                        break
                        
                except Exception:
                    continue
        
        # 4. Cek di JavaScript/script tags
        if not sitekey:
            print("[TURNSTILE] Mencari sitekey di script tags...")
            try:
                scripts = await page.locator('script').all()
                for script in scripts:
                    try:
                        script_content = await script.text_content()
                        if script_content and 'turnstile' in script_content.lower():
                            # Pattern untuk mencari sitekey
                            patterns = [
                                r'sitekey["\']?\s*:\s*["\']([^"\']+)["\']',
                                r'data-sitekey["\']?\s*=\s*["\']([^"\']+)["\']',
                                r'websiteKey["\']?\s*:\s*["\']([^"\']+)["\']',
                                r'site_key["\']?\s*:\s*["\']([^"\']+)["\']'
                            ]
                            
                            for pattern in patterns:
                                match = re.search(pattern, script_content, re.IGNORECASE)
                                if match:
                                    sitekey = match.group(1)
                                    print(f"[TURNSTILE] Sitekey ditemukan di script: {sitekey}")
                                    break
                            
                            # Cari action dan cdata juga
                            if not action:
                                action_match = re.search(r'action["\']?\s*:\s*["\']([^"\']+)["\']', script_content, re.IGNORECASE)
                                if action_match:
                                    action = action_match.group(1)
                                    print(f"[TURNSTILE] Action ditemukan di script: {action}")
                            
                            if not cdata:
                                cdata_match = re.search(r'cdata["\']?\s*:\s*["\']([^"\']+)["\']', script_content, re.IGNORECASE)
                                if cdata_match:
                                    cdata = cdata_match.group(1)
                                    print(f"[TURNSTILE] CData ditemukan di script: {cdata}")
                            
                            if sitekey:
                                break
                    except Exception:
                        continue
            except Exception as e:
                print(f"[TURNSTILE] Error checking scripts: {e}")
        
        # 5. Fallback: gunakan sitekey umum jika tidak ditemukan
        if not sitekey:
            print("[TURNSTILE] Sitekey tidak ditemukan, menggunakan sitekey default")
            # Sitekey umum untuk testing/demo Cloudflare Turnstile
            sitekey = "0x4AAAAAAADnPIDROlWd_wc"
        
        print(f"[TURNSTILE] Website: {website_url}")
        print(f"[TURNSTILE] Sitekey: {sitekey}")
        print(f"[TURNSTILE] Action: {action}")
        print(f"[TURNSTILE] CData: {cdata}")
        
        return website_url, sitekey, action, cdata
        
    except Exception as e:
        print(f"[TURNSTILE] Error extracting info: {e}")
        # Return fallback values
        return page.url, "0x4AAAAAAADnPIDROlWd_wc", "", ""

async def click_turnstile_checkbox(page):
    """Klik checkbox di iframe Turnstile dengan pencarian yang lebih komprehensif"""
    print("[TURNSTILE] Mencari dan mengklik checkbox Turnstile di semua frame...")
    
    # Selectors untuk checkbox Turnstile yang lebih lengkap
    checkbox_selectors = [
        'input[type="checkbox"]',
        '[role="checkbox"]',
        'div[role="checkbox"]',
        'button[role="checkbox"]',
        'label:has([type="checkbox"])',
        'div[role="button"][tabindex]',
        'div[role="button"]',
        'button[role="button"]',
        'div[aria-checked]',
        '.cf-turnstile',
        '.ctp-checkbox',
        '[data-testid*="turnstile"]',
        'div:has-text("Verify")',
        'div:has-text("verify")',
        'span:has-text("Verify")',
        '.cb-lb input[type="checkbox"]',  # Selector khusus dari beberapa varian
        'label.cb-lb',
        'label.cb-lb input',
        '#wNUym6 input[type="checkbox"]',
        '.cb-c input[type="checkbox"]',
        'input[type="checkbox"][class*="cb"]',
        'label[class*="cb"] input[type="checkbox"]'
    ]
    
    # 1. Prioritaskan cek di iframe Turnstile tradisional, karena ini skenario paling umum
    print("[TURNSTILE] Mengecek iframe Turnstile tradisional (prioritas)...")
    try:
        # Tunggu iframe muncul dengan timeout yang cukup
        await page.wait_for_selector(IFRAME_TURNSTILE, state="attached", timeout=15_000)
        fl = page.frame_locator(IFRAME_TURNSTILE)
        
        for selector in checkbox_selectors:
            try:
                el = fl.locator(selector).first
                print(f"[TURNSTILE] Menunggu checkbox '{selector}' di iframe menjadi visible...")
                await el.wait_for(state="visible", timeout=10_000) # Tunggu hingga 10 detik
                try:
                    await el.scroll_into_view_if_needed()
                except Exception:
                    pass
                await el.click(force=True, timeout=5000)
                print(f"[TURNSTILE] Checkbox berhasil diklik di iframe Turnstile: {selector}")
                return True
            except Exception:
                continue
    except PWTimeout:
        print("[TURNSTILE] Iframe Turnstile tradisional tidak ditemukan dalam 15 detik.")
    
    # 1. Cek di main page dulu (mungkin tidak di iframe)
    print("[TURNSTILE] Mengecek checkbox di main page...")
    for selector in checkbox_selectors:
        try:
            if await page.locator(selector).count() > 0:
                element = page.locator(selector).first
                await element.wait_for(state="visible", timeout=2000)
                
                # Cek apakah ini checkbox yang benar dengan melihat teks di sekitarnya
                try:
                    parent = element.locator('xpath=..')
                    parent_text = await parent.text_content()
                    if parent_text and ("verify" in parent_text.lower() or "human" in parent_text.lower()):
                        print(f"[TURNSTILE] Checkbox 'Verify you are human' ditemukan di main page: {selector}")
                        await element.scroll_into_view_if_needed()
                        await element.click(force=True)
                        print("[TURNSTILE] Checkbox berhasil diklik di main page!")
                        return True
                except Exception:
                    # Jika tidak bisa cek parent text, coba klik saja
                    print(f"[TURNSTILE] Mencoba klik checkbox di main page: {selector}")
                    await element.scroll_into_view_if_needed()
                    await element.click(force=True)
                    print("[TURNSTILE] Checkbox diklik di main page!")
                    return True
        except Exception as e:
            continue
    
    # 3. Cek di SEMUA frame/iframe yang ada
    print("[TURNSTILE] Mengecek di semua frame yang tersedia...")
    for frame in page.frames:
        try:
            frame_url = frame.url or ""
            print(f"[TURNSTILE] Mengecek frame: {frame_url}")
            
            # Cek semua selector di frame ini
            for selector in checkbox_selectors:
                try:
                    if await frame.locator(selector).count() > 0:
                        element = frame.locator(selector).first
                        await element.wait_for(state="visible", timeout=2000)
                        
                        # Cek apakah ini checkbox yang benar
                        try:
                            parent = element.locator('xpath=..')
                            parent_text = await parent.text_content()
                            if parent_text and ("verify" in parent_text.lower() or "human" in parent_text.lower()):
                                print(f"[TURNSTILE] Checkbox 'Verify you are human' ditemukan di frame {frame_url}: {selector}")
                                await element.click(force=True)
                                print(f"[TURNSTILE] Checkbox berhasil diklik di frame!")
                                return True
                        except Exception:
                            pass
                        
                        # Jika frame mengandung cloudflare/turnstile, langsung coba klik
                        if any(keyword in frame_url.lower() for keyword in ["cloudflare", "turnstile", "challenges"]):
                            print(f"[TURNSTILE] Frame Cloudflare/Turnstile terdeteksi, klik checkbox: {selector}")
                            await element.click(force=True)
                            print(f"[TURNSTILE] Checkbox diklik di frame Cloudflare!")
                            return True
                        
                        # Untuk frame lain, coba klik jika selector cocok dengan pattern Turnstile
                        if any(pattern in selector for pattern in ["cb-", "checkbox"]):
                            print(f"[TURNSTILE] Pattern Turnstile terdeteksi di frame, klik checkbox: {selector}")
                            await element.click(force=True)
                            print(f"[TURNSTILE] Checkbox diklik di frame!")
                            return True
                            
                except Exception as e:
                    continue
                    
        except Exception as e:
            continue
    
    # 4. Fallback: cari berdasarkan text content
    print("[TURNSTILE] Fallback: mencari berdasarkan text 'Verify you are human'...")
    try:
        # Cek di main page
        verify_elements = await page.locator('text=/verify.*human/i').all()
        for element in verify_elements:
            try:
                # Cari checkbox di dalam atau dekat element ini
                checkbox = element.locator('input[type="checkbox"]').first
                if await checkbox.count() > 0:
                    await checkbox.click(force=True, timeout=5000)
                    print("[TURNSTILE] Checkbox ditemukan via text search di main page!")
                    return True
                    
                # Cari di parent
                parent_checkbox = element.locator('xpath=..//*[@type="checkbox"]').first
                if await parent_checkbox.count() > 0:
                    await parent_checkbox.click(force=True, timeout=5000)
                    print("[TURNSTILE] Checkbox ditemukan via parent text search di main page!")
                    return True
            except Exception:
                continue
        
        # Cek di semua frame
        for frame in page.frames:
            try:
                verify_elements = await frame.locator('text=/verify.*human/i').all()
                for element in verify_elements:
                    try:
                        checkbox = element.locator('input[type="checkbox"]').first
                        if await checkbox.count() > 0:
                            await checkbox.click(force=True, timeout=5000)
                            print(f"[TURNSTILE] Checkbox ditemukan via text search di frame {frame.url}!")
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
                
    except Exception as e:
        print(f"[TURNSTILE] Error dalam text search: {e}")
    
    # Fallback: klik tengah iframe Turnstile bila selector gagal
    try:
        print("[TURNSTILE] Fallback: klik tengah iframe Turnstileâ€¦")
        iframe_el = page.locator(IFRAME_TURNSTILE).first
        await iframe_el.wait_for(state="attached", timeout=5000)
        box = await iframe_el.bounding_box()
        if box and box["width"] > 5 and box["height"] > 5:
            x = box["x"] + box["width"] / 2
            y = box["y"] + box["height"] / 2
            await page.mouse.click(x, y, delay=50)
            print("[TURNSTILE] Fallback click tengah iframe berhasil")
            return True
    except Exception as e:
        print(f"[TURNSTILE] Fallback click iframe gagal: {e}")

    print("[TURNSTILE] Checkbox tidak ditemukan di manapun")
    return False

async def wait_turnstile_token(page, timeout_ms):
    """Tunggu token Turnstile terisi (jika elemen ada)."""
    print("[TURNSTILE] Menunggu token Turnstileâ€¦")
    try:
        await page.wait_for_selector(TURNSTILE_INPUT, timeout=10_000)
    except PWTimeout:
        print("[TS] Input token tidak tampil. Lanjut saja.")
        return None

    for i in range(timeout_ms // 1000):
        val = await page.evaluate(
            f'''() => {{
                const el = document.querySelector('{TURNSTILE_INPUT}');
                return el && el.value ? el.value : null;
            }}'''
        )
        if val:
            print("[TURNSTILE] Token terdeteksi ðŸ¥³")
            return val
        if i % 5 == 0:
            print(f"[TURNSTILE] â€¦menunggu token ({i}s)")
        await asyncio.sleep(1)
    print("[TURNSTILE] Timeout menunggu token.")
    return None

async def inject_turnstile_token(page, token):
    """Inject token Turnstile ke dalam input field"""
    try:
        # Inject token ke input field
        await page.evaluate(f'''() => {{
            const input = document.querySelector('{TURNSTILE_INPUT}');
            if (input) {{
                input.value = '{token}';
                input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                console.log('Token injected successfully');
                return true;
            }}
            return false;
        }}''')
        
        print("[INJECT] Token berhasil diinjeksi")
        return True
        
    except Exception as e:
        print(f"[INJECT] Error injecting token: {e}")
        return False

async def detect_success_notification_quick(page):
    """Deteksi cepat notifikasi sukses tanpa timeout panjang - LEBIH KETAT"""
    try:
        # Kurangi log spam - hanya print saat benar-benar menemukan sukses
        
        # HANYA cek keyword sukses yang SANGAT SPESIFIK untuk Rain
        specific_success_keywords = [
            "successfully joined",
            "joined successfully", 
            "successfully entered",
            "entered successfully",
            "rain joined",
            "joined the rain",
            "entered the rain",
            "participation confirmed",
            "entry confirmed"
        ]
        
        # Cek di main page dengan keyword spesifik
        for keyword in specific_success_keywords:
            try:
                if await page.locator(f'text=/{re.escape(keyword)}/i').count() > 0:
                    element = page.locator(f'text=/{re.escape(keyword)}/i').first
                    if await element.is_visible():
                        text = await element.text_content()
                        print(f"[SUCCESS_QUICK] âœ… SUKSES ASLI ditemukan di main page: '{text}' (keyword: {keyword})")
                        return True
            except Exception:
                continue
                
        # Cek di frame flip.gg dengan keyword spesifik (exclude cf/turnstile)
        for frame in page.frames:
            try:
                frame_url = (frame.url or "").lower()
                if 'flip.gg' not in frame_url:
                    continue
                if any(k in frame_url for k in ['cloudflare', 'turnstile', 'challenges.cloudflare.com']):
                    continue
                    
                for keyword in specific_success_keywords:
                    try:
                        if await frame.locator(f'text=/{re.escape(keyword)}/i').count() > 0:
                            element = frame.locator(f'text=/{re.escape(keyword)}/i').first
                            if await element.is_visible():
                                text = await element.text_content()
                                print(f"[SUCCESS_QUICK] âœ… SUKSES ASLI ditemukan di frame {frame.url}: '{text}' (keyword: {keyword})")
                                return True
                    except Exception:
                        continue
            except Exception:
                continue
        
        # Cek selector sukses yang SANGAT SPESIFIK (hanya yang benar-benar untuk notifikasi)
        specific_success_selectors = [
            '.success-notification',
            '.rain-success',
            '.join-success', 
            '.entry-success',
            '.notification.success:has-text("joined")',
            '.notification.success:has-text("entered")',
            '.toast.success:has-text("joined")',
            '.toast.success:has-text("entered")',
            '.alert.success:has-text("joined")',
            '.alert.success:has-text("entered")'
        ]
        
        # Cek di main page dengan selector spesifik
        for selector in specific_success_selectors:
            try:
                if await page.locator(selector).count() > 0:
                    element = page.locator(selector).first
                    if await element.is_visible():
                        text = await element.text_content()
                        # Validasi tambahan: pastikan teks mengandung kata kunci sukses
                        if any(word in text.lower() for word in ['joined', 'entered', 'success', 'confirmed']):
                            print(f"[SUCCESS_QUICK] âœ… SUKSES ASLI ditemukan via selector di main page: '{text}' (selector: {selector})")
                            return True
            except Exception:
                continue
        
        # Cek di frame flip.gg dengan selector spesifik
        for frame in page.frames:
            try:
                frame_url = (frame.url or "").lower()
                if 'flip.gg' not in frame_url:
                    continue
                if any(k in frame_url for k in ['cloudflare', 'turnstile', 'challenges.cloudflare.com']):
                    continue
                    
                for selector in specific_success_selectors:
                    try:
                        if await frame.locator(selector).count() > 0:
                            element = frame.locator(selector).first
                            if await element.is_visible():
                                text = await element.text_content()
                                # Validasi tambahan: pastikan teks mengandung kata kunci sukses
                                if any(word in text.lower() for word in ['joined', 'entered', 'success', 'confirmed']):
                                    print(f"[SUCCESS_QUICK] âœ… SUKSES ASLI ditemukan via selector di frame {frame.url}: '{text}' (selector: {selector})")
                                    return True
                    except Exception:
                        continue
            except Exception:
                continue
                    
        # Tidak perlu log jika tidak ada sukses - kurangi spam
        return False
        
    except Exception as e:
        print(f"[SUCCESS_QUICK] Error: {e}")
        return False

async def auto_click_checkbox_if_found(page):
    """Fungsi untuk otomatis klik checkbox Turnstile kapanpun dan dimanapun ditemukan"""
    try:
        # Selectors untuk checkbox Turnstile
        checkbox_selectors = [
            'input[type="checkbox"]',
            '[role="checkbox"]',
            'div[role="checkbox"]',
            'button[role="checkbox"]',
            'label:has([type="checkbox"])',
            'div[role="button"][tabindex]',
            'div[role="button"]',
            'button[role="button"]',
            'div[aria-checked]',
            '.cf-turnstile',
            '.ctp-checkbox',
            '[data-testid*="turnstile"]',
            'div:has-text("Verify")',
            'div:has-text("verify")',
            'span:has-text("Verify")',
            '.cb-lb input[type="checkbox"]',
            'label.cb-lb',
            'label.cb-lb input',
            '#wNUym6 input[type="checkbox"]',
            '.cb-c input[type="checkbox"]',
            'input[type="checkbox"][class*="cb"]',
            'label[class*="cb"] input[type="checkbox"]'
        ]
        
        # 1. Cek di iframe Turnstile terlebih dahulu
        try:
            iframe_count = await page.locator(IFRAME_TURNSTILE).count()
            if iframe_count > 0:
                fl = page.frame_locator(IFRAME_TURNSTILE)
                
                for selector in checkbox_selectors:
                    try:
                        if await fl.locator(selector).count() > 0:
                            el = fl.locator(selector).first
                            # Cek apakah visible dan enabled
                            if await el.is_visible() and await el.is_enabled():
                                await el.click(force=True, timeout=2000)
                                print(f"[AUTO_CHECKBOX] âœ… Checkbox diklik otomatis di iframe: {selector}")
                                return True
                    except Exception:
                        continue
        except Exception:
            pass
        
        # 2. Cek di main page
        for selector in checkbox_selectors:
            try:
                if await page.locator(selector).count() > 0:
                    element = page.locator(selector).first
                    # Cek apakah visible dan enabled
                    if await element.is_visible() and await element.is_enabled():
                        # Cek apakah ini checkbox Turnstile dengan melihat konteks
                        try:
                            parent = element.locator('xpath=..')
                            parent_text = await parent.text_content()
                            if parent_text and ("verify" in parent_text.lower() or "human" in parent_text.lower() or "turnstile" in parent_text.lower()):
                                await element.click(force=True, timeout=2000)
                                print(f"[AUTO_CHECKBOX] âœ… Checkbox Turnstile diklik otomatis di main page: {selector}")
                                return True
                        except Exception:
                            # Jika tidak bisa cek parent, coba klik jika selector mengandung pattern Turnstile
                            if any(pattern in selector for pattern in ["cb-", "checkbox", "turnstile", "verify"]):
                                await element.click(force=True, timeout=2000)
                                print(f"[AUTO_CHECKBOX] âœ… Checkbox diklik otomatis di main page (pattern): {selector}")
                                return True
            except Exception:
                continue
        
        # 3. Cek di semua frame lainnya
        for frame in page.frames:
            try:
                frame_url = frame.url or ""
                
                # Prioritaskan frame Cloudflare/Turnstile
                is_cf_frame = any(keyword in frame_url.lower() for keyword in ["cloudflare", "turnstile", "challenges"])
                
                for selector in checkbox_selectors:
                    try:
                        if await frame.locator(selector).count() > 0:
                            element = frame.locator(selector).first
                            # Cek apakah visible dan enabled
                            if await element.is_visible() and await element.is_enabled():
                                if is_cf_frame:
                                    # Jika frame Cloudflare/Turnstile, langsung klik
                                    await element.click(force=True, timeout=2000)
                                    print(f"[AUTO_CHECKBOX] âœ… Checkbox diklik otomatis di frame CF: {selector}")
                                    return True
                                else:
                                    # Untuk frame lain, cek konteks
                                    try:
                                        parent = element.locator('xpath=..')
                                        parent_text = await parent.text_content()
                                        if parent_text and ("verify" in parent_text.lower() or "human" in parent_text.lower()):
                                            await element.click(force=True, timeout=2000)
                                            print(f"[AUTO_CHECKBOX] âœ… Checkbox diklik otomatis di frame: {selector}")
                                            return True
                                    except Exception:
                                        pass
                    except Exception:
                        continue
            except Exception:
                continue
        
        # Fallback terakhir: klik tengah iframe Turnstile bila ada
        try:
            iframe_count = await page.locator(IFRAME_TURNSTILE).count()
            if iframe_count > 0:
                print("[AUTO_CHECKBOX] Fallback: klik tengah iframe Turnstileâ€¦")
                iframe_el = page.locator(IFRAME_TURNSTILE).first
                await iframe_el.wait_for(state="attached", timeout=3000)
                box = await iframe_el.bounding_box()
                if box and box["width"] > 5 and box["height"] > 5:
                    x = box["x"] + box["width"] / 2
                    y = box["y"] + box["height"] / 2
                    await page.mouse.click(x, y, delay=50)
                    print("[AUTO_CHECKBOX] âœ… Fallback click tengah iframe berhasil")
                    return True
        except Exception:
            pass

        return False
        
    except Exception as e:
        print(f"[AUTO_CHECKBOX] Error: {e}")
        return False

async def continuous_success_scanner(page):
    """Background task untuk scan notifikasi sukses secara kontinyu di frame flip.gg dengan interval 0.2 detik"""
    print("[SUCCESS_SCANNER] Memulai background scanner untuk notifikasi sukses (scan setiap 0.2 detik)...")
    
    # Flag untuk mencegah klik berulang setelah sukses/already terdeteksi
    success_detected = False
    already_detected = False
    
    try:
        while True:
            try:
                # Skip handling halaman informasi Rain (no refresh)
                # Dihilangkan sesuai permintaan: tidak melakukan refresh saat info Rain muncul.
                
                # Cek notifikasi sukses - PRIORITAS TINGGI
                success_found = await detect_success_notification_quick(page)
                if success_found and not success_detected:
                    success_detected = True
                    print("[SUCCESS_SCANNER] âœ… SUKSES ditemukan! BERHENTI KLIK!")
                    
                    # Kirim notifikasi Telegram langsung dengan detail lengkap
                    if telegram:
                        try:
                            await telegram.send_message(
                                f"ðŸŽ‰ <b>SUKSES JOIN RAIN!</b>\n\n"
                                f"âœ… Notifikasi sukses berhasil terdeteksi\n"
                                f"ðŸŽ¯ Status: ENTERED\n"
                                f"â° Waktu: {time.strftime('%H:%M:%S', time.localtime())}\n"
                                f"ðŸ” Scan interval: 0.2 detik (realtime)\n"
                                f"ðŸš« TANPA REFRESH setelah klik rain\n\n"
                                f"<i>Successfully joined rain!</i>"
                            )
                            print("[SUCCESS_SCANNER] âœ… Notifikasi Telegram sukses dikirim!")
                        except Exception as e:
                            print(f"[SUCCESS_SCANNER] âŒ Error kirim Telegram: {e}")
                    
                    # Tetap kirim log biasa sebagai backup
                    await send_telegram_log("ðŸŽ‰ SUKSES TERDETEKSI - Bot berhenti klik otomatis", "SUCCESS")
                    return "success"
                
                # Cek already joined - PRIORITAS TINGGI
                already_found = await check_already_joined(page)
                if already_found and not already_detected:
                    already_detected = True
                    print("[SUCCESS_SCANNER] â„¹ï¸ Already joined ditemukan! BERHENTI KLIK!")
                    await send_telegram_log("â„¹ï¸ ALREADY JOINED TERDETEKSI - Bot berhenti klik otomatis", "INFO")
                    return "already"
                
                # HANYA klik checkbox jika belum ada sukses/already
                if not success_detected and not already_detected:
                    checkbox_found = await auto_click_checkbox_if_found(page)
                    if checkbox_found:
                        print("[SUCCESS_SCANNER] ðŸŽ¯ Checkbox ditemukan dan diklik otomatis!")
                        # Setelah klik checkbox, tunggu sebentar dan cek lagi notifikasi
                        await asyncio.sleep(1)
                        
                        # Cek ulang notifikasi setelah klik checkbox
                        success_found = await detect_success_notification_quick(page)
                        if success_found:
                            success_detected = True
                            print("[SUCCESS_SCANNER] âœ… SUKSES setelah klik checkbox! BERHENTI!")
                            await send_telegram_log("ðŸŽ‰ SUKSES setelah klik checkbox - Bot berhenti", "SUCCESS")
                            return "success"
                        
                        already_found = await check_already_joined(page)
                        if already_found:
                            already_detected = True
                            print("[SUCCESS_SCANNER] â„¹ï¸ Already joined setelah klik checkbox! BERHENTI!")
                            await send_telegram_log("â„¹ï¸ ALREADY JOINED setelah klik checkbox - Bot berhenti", "INFO")
                            return "already"
                else:
                    # Jika sudah ada sukses/already, jangan klik apa-apa lagi (kurangi log spam)
                    pass
                
                # Scan setiap 0.2 detik untuk responsivitas maksimal (realtime)
                await asyncio.sleep(0.2)
                
            except Exception as e:
                print(f"[SUCCESS_SCANNER] Error dalam scan: {e}")
                await asyncio.sleep(0.2)
                continue
                
    except asyncio.CancelledError:
        print("[SUCCESS_SCANNER] Background scanner dibatalkan")
        raise
    except Exception as e:
        print(f"[SUCCESS_SCANNER] Fatal error: {e}")
        return "error"

async def continuous_24h_scanner(page):
    """Scanner 24 jam untuk checkbox Turnstile dan notifikasi sukses/already - TANPA TIMEOUT"""
    print("[24H_SCANNER] ðŸ”„ Memulai scanner 24 jam untuk checkbox dan notifikasi...")
    await send_telegram_log("ðŸ”„ Scanner 24 jam dimulai - scan checkbox dan notifikasi setiap 0.2 detik", "INFO")
    
    # Statistik untuk tracking
    start_time = time.time()
    checkbox_clicks = 0
    success_detected = False
    already_detected = False
    
    try:
        while True:
            try:
                current_time = time.time()
                elapsed_hours = (current_time - start_time) / 3600
                
                # Log progress setiap jam
                if int(elapsed_hours) > int((current_time - start_time - 0.2) / 3600):
                    hours_passed = int(elapsed_hours)
                    print(f"[24H_SCANNER] â° {hours_passed} jam berlalu - checkbox diklik: {checkbox_clicks} kali")
                    await send_telegram_log(f"â° Scanner 24 jam: {hours_passed} jam berlalu, checkbox diklik: {checkbox_clicks} kali", "INFO")
                
                # PRIORITAS 1: Cek notifikasi sukses
                success_found = await detect_success_notification_quick(page)
                if success_found and not success_detected:
                    success_detected = True
                    elapsed_time = time.time() - start_time
                    print(f"[24H_SCANNER] ðŸŽ‰ SUKSES ditemukan setelah {elapsed_time/60:.1f} menit!")
                    
                    # Kirim notifikasi Telegram dengan statistik lengkap
                    if telegram:
                        try:
                            await telegram.send_message(
                                f"ðŸŽ‰ <b>SUKSES JOIN RAIN!</b>\n\n"
                                f"âœ… Notifikasi sukses berhasil terdeteksi\n"
                                f"ðŸŽ¯ Status: ENTERED\n"
                                f"â° Waktu scan: {elapsed_time/60:.1f} menit\n"
                                f"ðŸ–±ï¸ Checkbox diklik: {checkbox_clicks} kali\n"
                                f"ðŸ” Scan interval: 0.2 detik (realtime)\n"
                                f"ðŸš« TANPA REFRESH setelah klik rain\n"
                                f"ðŸ“Š Scanner 24 jam aktif\n\n"
                                f"<i>Successfully joined rain!</i>"
                            )
                            print("[24H_SCANNER] âœ… Notifikasi Telegram sukses dikirim!")
                        except Exception as e:
                            print(f"[24H_SCANNER] âŒ Error kirim Telegram: {e}")
                    
                    await send_telegram_log("ðŸŽ‰ SUKSES TERDETEKSI - Scanner 24 jam berhenti", "SUCCESS")
                    return "success"
                
                # PRIORITAS 2: Cek already joined
                already_found = await check_already_joined(page)
                if already_found and not already_detected:
                    already_detected = True
                    elapsed_time = time.time() - start_time
                    print(f"[24H_SCANNER] â„¹ï¸ Already joined ditemukan setelah {elapsed_time/60:.1f} menit!")
                    
                    # Kirim notifikasi Telegram
                    if telegram:
                        try:
                            await telegram.send_message(
                                f"â„¹ï¸ <b>ALREADY JOINED</b>\n\n"
                                f"â° Waktu scan: {elapsed_time/60:.1f} menit\n"
                                f"ðŸ–±ï¸ Checkbox diklik: {checkbox_clicks} kali\n"
                                f"ðŸ” Scan interval: 0.2 detik (realtime)\n"
                                f"ðŸš« TANPA REFRESH setelah klik rain\n"
                                f"ðŸ“Š Scanner 24 jam aktif\n\n"
                                f"You have already entered this rain!"
                            )
                        except Exception as e:
                            print(f"[24H_SCANNER] âŒ Error kirim Telegram: {e}")
                    
                    await send_telegram_log("â„¹ï¸ ALREADY JOINED TERDETEKSI - Scanner 24 jam berhenti", "INFO")
                    return "already"
                
                # PRIORITAS 3: Auto-klik checkbox jika belum ada sukses/already
                if not success_detected and not already_detected:
                    checkbox_found = await auto_click_checkbox_if_found(page)
                    if checkbox_found:
                        checkbox_clicks += 1
                        print(f"[24H_SCANNER] ðŸŽ¯ Checkbox #{checkbox_clicks} diklik otomatis!")
                        
                        # Setelah klik checkbox, tunggu sebentar dan cek lagi notifikasi
                        await asyncio.sleep(1)
                        
                        # Cek ulang notifikasi setelah klik checkbox
                        success_found = await detect_success_notification_quick(page)
                        if success_found:
                            success_detected = True
                            elapsed_time = time.time() - start_time
                            print(f"[24H_SCANNER] âœ… SUKSES setelah klik checkbox #{checkbox_clicks}!")
                            
                            if telegram:
                                try:
                                    await telegram.send_message(
                                        f"ðŸŽ‰ <b>SUKSES SETELAH KLIK CHECKBOX!</b>\n\n"
                                        f"âœ… Checkbox #{checkbox_clicks} berhasil\n"
                                        f"ðŸŽ¯ Notifikasi sukses terdeteksi\n"
                                        f"â° Waktu: {elapsed_time/60:.1f} menit\n"
                                        f"ðŸ” Scanner 24 jam aktif\n\n"
                                        f"<i>Successfully joined rain!</i>"
                                    )
                                except Exception as e:
                                    print(f"[24H_SCANNER] âŒ Error kirim Telegram: {e}")
                            
                            await send_telegram_log("ðŸŽ‰ SUKSES setelah klik checkbox - Scanner berhenti", "SUCCESS")
                            return "success"
                        
                        already_found = await check_already_joined(page)
                        if already_found:
                            already_detected = True
                            elapsed_time = time.time() - start_time
                            print(f"[24H_SCANNER] â„¹ï¸ Already joined setelah klik checkbox #{checkbox_clicks}!")
                            await send_telegram_log("â„¹ï¸ ALREADY JOINED setelah klik checkbox - Scanner berhenti", "INFO")
                            return "already"
                
                # Scan setiap 0.2 detik untuk responsivitas maksimal (realtime)
                await asyncio.sleep(0.2)
                
            except Exception as e:
                print(f"[24H_SCANNER] Error dalam scan: {e}")
                await asyncio.sleep(0.2)
                continue
                
    except asyncio.CancelledError:
        elapsed_time = time.time() - start_time
        print(f"[24H_SCANNER] Scanner dibatalkan setelah {elapsed_time/60:.1f} menit, checkbox diklik: {checkbox_clicks} kali")
        await send_telegram_log(f"â¹ï¸ Scanner 24 jam dibatalkan setelah {elapsed_time/60:.1f} menit", "WARNING")
        raise
    except Exception as e:
        elapsed_time = time.time() - start_time
        print(f"[24H_SCANNER] Fatal error setelah {elapsed_time/60:.1f} menit: {e}")
        await send_telegram_log(f"âŒ Scanner 24 jam error setelah {elapsed_time/60:.1f} menit: {e}", "ERROR")
        return "error"

async def handle_turnstile_challenge_with_refresh_retry(page):
    """Handle Turnstile challenge TANPA REFRESH setelah klik rain dan checkbox - scan success/already dengan interval 0.2 detik"""
    await send_telegram_log("ðŸš€ Memulai alur penanganan Turnstile TANPA REFRESH setelah klik rain...", "INFO")
    
    # Start background task untuk scan notifikasi sukses secara kontinyu dari awal
    success_scanner_task = asyncio.create_task(continuous_success_scanner(page))
    
    # Cek apakah success scanner sudah menemukan sukses dari awal
    if success_scanner_task.done():
        try:
            result = success_scanner_task.result()
            if result == "success":
                await send_telegram_log("ðŸŽ‰ SUKSES ditemukan oleh background scanner!", "SUCCESS")
                return "manual_success"
            elif result == "already":
                await send_telegram_log("â„¹ï¸ Already joined ditemukan oleh background scanner", "INFO")
                return "already_joined"
        except Exception as e:
            await send_telegram_log(f"âŒ Error pada success scanner: {e}", "ERROR")
    
    # LANGKAH 1: Tunggu iframe Turnstile muncul (TANPA REFRESH jika tidak ada)
    try:
        await send_telegram_log("â³ Menunggu iframe Cloudflare Turnstile...", "INFO")
        await page.wait_for_selector(IFRAME_TURNSTILE, timeout=30_000)
        await send_telegram_log("âœ… Iframe Turnstile terdeteksi!", "SUCCESS")
    except PWTimeout:
        await send_telegram_log("âŒ Tidak ada iframe Turnstile - cek hasil langsung", "WARNING")
        # Jika tidak ada iframe, mungkin tidak ada captcha sama sekali
        success_found = await detect_success_notification_quick(page)
        if success_found:
            success_scanner_task.cancel()
            await send_telegram_log("ðŸŽ‰ Sukses otomatis tanpa Turnstile!", "SUCCESS")
            return "instant_success"
        if await check_already_joined(page):
            success_scanner_task.cancel()
            await send_telegram_log("â„¹ï¸ Already joined - cooldown 3 menit", "INFO")
            return "already_joined"
        
        # TIDAK REFRESH - langsung tunggu dengan scanner
        await send_telegram_log("â³ Tidak ada iframe, tunggu dengan scanner tanpa refresh...", "INFO")
        try:
            result = await asyncio.wait_for(success_scanner_task, timeout=120)  # Tunggu 2 menit
            if result == "success":
                await send_telegram_log("ðŸŽ‰ SUKSES ditemukan oleh scanner!", "SUCCESS")
                return "manual_success"
            elif result == "already":
                await send_telegram_log("â„¹ï¸ Already joined ditemukan oleh scanner", "INFO")
                return "already_joined"
        except asyncio.TimeoutError:
            success_scanner_task.cancel()
            await send_telegram_log("â° Timeout 2 menit - tidak ada hasil", "WARNING")
            return "timeout_no_iframe"

    # LANGKAH 2: Tunggu iframe selesai loading
    await send_telegram_log("â³ Menunggu iframe selesai loading...", "INFO")
    loading_timeout = time.time() + 20  # Maksimal 20 detik tunggu loading
    
    while time.time() < loading_timeout:
        try:
            # Cek apakah masih loading
            if not await is_turnstile_loading(page):
                await send_telegram_log("âœ… Loading selesai - mengecek checkbox...", "SUCCESS")
                break
            await asyncio.sleep(1)
        except Exception:
            break
    
    # LANGKAH 3: Cek keberadaan checkbox setelah loading selesai
    checkbox_found = False
    try:
        iframe_count = await page.locator(IFRAME_TURNSTILE).count()
        if iframe_count > 0:
            fl = page.frame_locator(IFRAME_TURNSTILE)
            checkbox_selectors = [
                'input[type="checkbox"]',
                '[role="checkbox"]',
                'div[role="checkbox"]',
                'button[role="checkbox"]',
                '.cf-turnstile input[type="checkbox"]'
            ]
            
            for selector in checkbox_selectors:
                try:
                    if await fl.locator(selector).count() > 0:
                        checkbox_found = True
                        await send_telegram_log(f"âœ… Checkbox ditemukan: {selector}", "SUCCESS")
                        break
                except Exception:
                    continue
    except Exception as e:
        await send_telegram_log(f"âŒ Error saat cek checkbox: {e}", "ERROR")
    
    # LANGKAH 4: Jika tidak ada checkbox - TIDAK REFRESH, langsung tunggu dengan scanner
    if not checkbox_found:
        await send_telegram_log("âš ï¸ Iframe loaded tapi TIDAK ADA CHECKBOX!", "WARNING")
        await send_telegram_log("â³ TIDAK REFRESH - tunggu dengan scanner...", "INFO")
        try:
            result = await asyncio.wait_for(success_scanner_task, timeout=120)  # Tunggu 2 menit
            if result == "success":
                await send_telegram_log("ðŸŽ‰ SUKSES ditemukan oleh scanner!", "SUCCESS")
                return "manual_success"
            elif result == "already":
                await send_telegram_log("â„¹ï¸ Already joined ditemukan oleh scanner", "INFO")
                return "already_joined"
        except asyncio.TimeoutError:
            success_scanner_task.cancel()
            await send_telegram_log("â° Timeout 2 menit - tidak ada hasil", "WARNING")
            return "timeout_no_checkbox"

    # LANGKAH 5: Checkbox ditemukan â†’ klik dan tunggu notifikasi sukses TANPA BATAS WAKTU
    await send_telegram_log("ðŸŽ¯ Checkbox ditemukan - klik dan tunggu notifikasi TANPA BATAS WAKTU!", "SUCCESS")
    
    # Klik checkbox
    checkbox_clicked = await click_turnstile_checkbox(page)
    if not checkbox_clicked:
        await send_telegram_log("âŒ Gagal klik checkbox", "ERROR")
        await send_telegram_log("â³ TIDAK REFRESH - tunggu dengan scanner...", "INFO")
        try:
            result = await asyncio.wait_for(success_scanner_task, timeout=120)  # Tunggu 2 menit
            if result == "success":
                await send_telegram_log("ðŸŽ‰ SUKSES ditemukan oleh scanner!", "SUCCESS")
                return "manual_success"
            elif result == "already":
                await send_telegram_log("â„¹ï¸ Already joined ditemukan oleh scanner", "INFO")
                return "already_joined"
        except asyncio.TimeoutError:
            success_scanner_task.cancel()
            await send_telegram_log("â° Timeout 2 menit - tidak ada hasil", "WARNING")
            return "timeout_click_failed"
    
    await send_telegram_log("âœ… Checkbox berhasil diklik - menunggu notifikasi TANPA BATAS WAKTU!", "SUCCESS")
    
    # LANGKAH 6: Tunggu notifikasi sukses TANPA BATAS WAKTU setelah klik checkbox
    await send_telegram_log("â° Mulai menunggu notifikasi TANPA BATAS WAKTU (scan setiap 0.2 detik)...", "INFO")
    
    try:
        result = await success_scanner_task  # Tunggu tanpa timeout
        if result == "success":
            await send_telegram_log("ðŸŽ‰ SUKSES! Notifikasi ditemukan!", "SUCCESS")
            
            # Kirim notifikasi Telegram khusus sukses
            if telegram:
                try:
                    await telegram.send_message(
                        f"ðŸŽ‰ <b>SUKSES JOIN RAIN!</b>\n\n"
                        f"âœ… Checkbox Turnstile berhasil diklik\n"
                        f"ðŸŽ¯ Notifikasi sukses terdeteksi\n"
                        f"â° Scan interval: 0.2 detik (realtime)\n"
                        f"ðŸš« TANPA REFRESH setelah klik rain\n\n"
                        f"Konfirmasi: <i>Successfully joined rain!</i>"
                    )
                except Exception as e:
                    print(f"[TELEGRAM] Error sending notification: {e}")
            
            return "manual_success"
        elif result == "already":
            await send_telegram_log("â„¹ï¸ Already joined terdeteksi!", "INFO")
            
            # Kirim notifikasi Telegram
            if telegram:
                try:
                    await telegram.send_message(
                        f"â„¹ï¸ <b>ALREADY JOINED</b>\n\n"
                        f"â° Scan interval: 0.2 detik (realtime)\n"
                        f"ðŸš« TANPA REFRESH setelah klik rain\n\n"
                        f"You have already entered this rain!"
                    )
                except Exception as e:
                    print(f"[TELEGRAM] Error sending notification: {e}")
            
            return "already_joined"
        else:
            await send_telegram_log(f"âŒ Scanner mengembalikan hasil tidak dikenal: {result}", "ERROR")
            return "unknown_result"
            
    except Exception as e:
        await send_telegram_log(f"âŒ Error pada success scanner: {e}", "ERROR")
        return "scanner_error"

async def refresh_page_and_click_rain(page):
    """FUNGSI INI TIDAK DIGUNAKAN LAGI - TIDAK MELAKUKAN REFRESH SETELAH KLIK RAIN"""
    await send_telegram_log("ðŸš« FUNGSI REFRESH DINONAKTIFKAN - Tidak akan melakukan refresh setelah klik rain", "WARNING")
    return False

async def check_rain_info_page_and_refresh(page):
    """Dinonaktifkan: tidak menangani halaman informasi Rain, tidak ada refresh."""
    return False


async def simple_rain_execution(page) -> bool:
    """Eksekusi sederhana: refresh â†’ klik rain â†’ scan 24 jam untuk checkbox dan notifikasi"""
    
    # LANGKAH 1: Refresh halaman sekali
    try:
        current_url = page.url or ""
        print(f"[SIMPLE] URL saat ini: {current_url}")
        if not current_url or 'flip.gg' not in current_url:
            print("[SIMPLE] Navigasi ke flip.gg...")
            await page.goto(TARGET_URL, wait_until='domcontentloaded', timeout=15000)
        else:
            print("[SIMPLE] Refresh halaman sekali...")
            await page.reload(wait_until='domcontentloaded', timeout=15000)
        print("[SIMPLE] âœ… Refresh selesai!")
        
        # Tunggu halaman siap
        await page.wait_for_load_state('domcontentloaded', timeout=10000)
        await asyncio.sleep(3)  # Tunggu 3 detik untuk memastikan loading selesai
        
    except Exception as e:
        print(f"[SIMPLE] Error refresh: {e}")
    
    # LANGKAH 2: Klik Rain
    print("[SIMPLE] Mencari dan mengklik tombol Rain...")
    rain_clicked = False
    
    # Coba selector prioritas
    sel = await detect_active(page)
    if sel:
        rain_clicked = await click_join(page, sel)
    
    # Fallback berbasis teks jika selector prioritas gagal
    if not rain_clicked:
        for fs in [
            "button:has-text('Join now')",
            "button:has-text('Join')",
            "button:has-text('Rain')",
            "button:has-text('Enter')",
        ]:
            try:
                if await page.locator(fs).count() > 0:
                    btn = page.locator(fs).first
                    try:
                        await btn.scroll_into_view_if_needed()
                    except Exception:
                        pass
                    try:
                        await btn.click()
                        print(f"[SIMPLE] Klik fallback tombol: {fs}")
                        rain_clicked = True
                        break
                    except Exception as e:
                        print(f"[SIMPLE] Gagal klik fallback {fs}: {e}")
                        continue
            except Exception:
                continue
    
    if not rain_clicked:
        print("[SIMPLE] âŒ Tombol Rain tidak ditemukan - tidak ada yang bisa diklik")
        await send_telegram_log("âŒ Tombol Rain tidak ditemukan", "ERROR")
        return False
    
    print("[SIMPLE] âœ… Rain berhasil diklik!")
    await send_telegram_log("âœ… Rain berhasil diklik - memulai scan 24 jam", "SUCCESS")
    
    # LANGKAH 3: Mulai scan 24 jam untuk checkbox dan notifikasi
    print("[SIMPLE] ðŸ”„ Memulai scan 24 jam untuk checkbox Turnstile dan notifikasi sukses/already...")
    await send_telegram_log("ðŸ”„ Memulai scan 24 jam untuk checkbox dan notifikasi", "INFO")
    
    # Start scanner 24 jam tanpa timeout
    task = asyncio.create_task(continuous_24h_scanner(page))
    
    try:
        result = await task  # Tunggu tanpa timeout (24 jam)
        print(f"[SIMPLE] Scanner 24 jam selesai dengan hasil: {result}")
        
        if result in ("success", "already"):
            _save_fast_result('success')
            return True
        else:
            return False
            
    except Exception as e:
        print(f"[SIMPLE] Error scanner 24 jam: {e}")
        await send_telegram_log(f"âŒ Error scanner 24 jam: {e}", "ERROR")
        return False

async def handle_turnstile_challenge(page):
    """Wrapper untuk handle_turnstile_challenge_with_refresh_retry dengan fallback ke metode lama"""
    try:
        # Gunakan metode baru dengan refresh retry
        return await handle_turnstile_challenge_with_refresh_retry(page)
    except Exception as e:
        await send_telegram_log(f"âŒ Error pada metode refresh retry: {e}", "ERROR")
        await send_telegram_log("ðŸ”„ Fallback ke metode lama...", "WARNING")
        
        # Fallback ke metode lama (kode asli yang sudah ada)
        return await handle_turnstile_challenge_legacy(page)

async def handle_turnstile_challenge_legacy(page):
    """Handle Turnstile challenge dengan logika lama (backup)"""
    await send_telegram_log("Memulai alur penanganan Turnstile (legacy)...", "INFO")

    # LANGKAH 1: Tunggu iframe Turnstile muncul.
    try:
        await send_telegram_log("Menunggu iframe Cloudflare Turnstile...", "INFO")
        await page.wait_for_selector(IFRAME_TURNSTILE, timeout=30_000)
        await send_telegram_log("âœ… Iframe Turnstile terdeteksi!", "SUCCESS")
    except PWTimeout:
        await send_telegram_log("Tidak ada iframe Turnstile - cek hasil langsung", "WARNING")
        # Jika tidak ada iframe, mungkin tidak ada captcha sama sekali.
        success_found = await detect_success_notification(page, 5)
        if success_found:
            await send_telegram_log("âœ… Sukses otomatis tanpa Turnstile!", "SUCCESS")
            return "instant_success"
        if await check_already_joined(page):
            set_already_joined_cooldown()
            await send_telegram_log("â„¹ï¸ Already joined - cooldown 3 menit dimulai", "INFO")
            return "already_joined"
        return "no_turnstile"

    # LANGKAH 2: Tunggu iframe selesai loading dan cek keberadaan checkbox
    await send_telegram_log("Menunggu iframe selesai loading...", "INFO")
    loading_timeout = time.time() + 15  # Maksimal 15 detik tunggu loading
    
    while time.time() < loading_timeout:
        try:
            # Cek apakah masih loading
            if not await is_turnstile_loading(page):
                await send_telegram_log("Loading selesai - mengecek checkbox...", "INFO")
                break
            await asyncio.sleep(1)
        except Exception:
            break
    
    # Cek apakah checkbox benar-benar ada setelah loading selesai
    checkbox_found = False
    try:
        # Cek di iframe Turnstile
        iframe_count = await page.locator(IFRAME_TURNSTILE).count()
        if iframe_count > 0:
            fl = page.frame_locator(IFRAME_TURNSTILE)
            checkbox_selectors = [
                'input[type="checkbox"]',
                '[role="checkbox"]',
                'div[role="checkbox"]',
                'button[role="checkbox"]',
                '.cf-turnstile input[type="checkbox"]'
            ]
            
            for selector in checkbox_selectors:
                try:
                    if await fl.locator(selector).count() > 0:
                        checkbox_found = True
                        await send_telegram_log(f"âœ… Checkbox ditemukan: {selector}", "SUCCESS")
                        break
                except Exception:
                    continue
    except Exception as e:
        await send_telegram_log(f"âŒ Error saat cek checkbox: {e}", "ERROR")
    
    if not checkbox_found:
        await send_telegram_log("âš ï¸ Iframe loaded tapi tidak ada checkbox - perlu refresh!", "WARNING")
        return "no_checkbox"

    # LANGKAH 3: Loop klik checkbox sampai ada notifikasi sukses
    await send_telegram_log("ðŸŽ¯ Checkbox ditemukan - memulai loop klik sampai sukses!", "INFO")
    max_click_attempts = 10  # Maksimal 10 kali klik
    click_attempt = 0
    
    while click_attempt < max_click_attempts:
        click_attempt += 1
        await send_telegram_log(f"ðŸ”„ Percobaan klik checkbox #{click_attempt}/10", "INFO")
        
        try:
            # Klik checkbox
            checkbox_clicked = await click_turnstile_checkbox(page)
            if not checkbox_clicked:
                await send_telegram_log("âŒ Gagal klik checkbox - coba lagi...", "WARNING")
                await asyncio.sleep(2)
                continue
            
            await send_telegram_log("âœ… Checkbox berhasil diklik - menunggu hasil...", "SUCCESS")
            await asyncio.sleep(3)  # Tunggu sebentar untuk processing
            
            # Cek apakah ada notifikasi sukses
            success_found = await detect_success_notification(page, 10)
            if success_found:
                await send_telegram_log("ðŸŽ‰ SUKSES! Notifikasi sukses ditemukan setelah klik checkbox!", "SUCCESS")
                
                # Kirim notifikasi Telegram khusus sukses
                if telegram:
                    try:
                        await telegram.send_message(
                            f"ðŸŽ‰ <b>SUKSES KLIK CHECKBOX!</b>\n\n"
                            f"âœ… Checkbox Turnstile berhasil diklik\n"
                            f"ðŸŽ¯ Notifikasi sukses terdeteksi\n"
                            f"ðŸ”„ Percobaan ke-{click_attempt}\n"
                            f"â° Waktu: {time.strftime('%H:%M:%S', time.localtime())}"
                        )
                    except Exception as e:
                        print(f"[TELEGRAM] Error sending notification: {e}")
                
                return "manual_success"
            
            # Cek apakah already joined
            if await check_already_joined(page):
                await send_telegram_log("â„¹ï¸ Already joined terdeteksi setelah klik checkbox", "INFO")
                set_already_joined_cooldown()
                return "already_joined"
            
            # Cek apakah checkbox muncul lagi (stuck/reload)
            checkbox_still_there = False
            try:
                iframe_count = await page.locator(IFRAME_TURNSTILE).count()
                if iframe_count > 0:
                    fl = page.frame_locator(IFRAME_TURNSTILE)
                    if await fl.locator('input[type="checkbox"]').count() > 0:
                        checkbox_still_there = True
                        await send_telegram_log("ðŸ”„ Checkbox masih ada - kemungkinan stuck, akan klik lagi", "WARNING")
            except Exception:
                pass
            
            if not checkbox_still_there:
                # Checkbox hilang tapi tidak ada notifikasi sukses, tunggu lebih lama
                await send_telegram_log("â³ Checkbox hilang - tunggu notifikasi sukses lebih lama...", "INFO")
                success_found = await detect_success_notification(page, 15)
                if success_found:
                    await send_telegram_log("ðŸŽ‰ SUKSES! Notifikasi sukses ditemukan setelah tunggu!", "SUCCESS")
                    return "manual_success"
                else:
                    await send_telegram_log("âŒ Tidak ada notifikasi sukses - mungkin gagal", "ERROR")
                    break
            
            # Jika checkbox masih ada, lanjut loop untuk klik lagi
            await asyncio.sleep(2)
            
        except Exception as e:
            await send_telegram_log(f"âŒ Error saat klik checkbox #{click_attempt}: {e}", "ERROR")
            await asyncio.sleep(2)
            continue
    
    await send_telegram_log(f"âŒ Gagal mendapat notifikasi sukses setelah {max_click_attempts} kali klik checkbox", "ERROR")
    
    # LANGKAH 4: Fallback ke CapSolver jika manual gagal
    if capsolver and AUTO_SOLVE_CAPTCHA:
        await send_telegram_log("ðŸ”„ Fallback ke CapSolver...", "INFO")
        website_url, sitekey, action, cdata = await extract_turnstile_info(page)
        
        if sitekey and sitekey != "0x4AAAAAAADnPIDROlWd_wc":
            solved_token = await capsolver.solve_turnstile(
                website_url=website_url,
                website_key=sitekey,
                action=action,
                cdata=cdata
            )
            
            if solved_token:
                await send_telegram_log("âœ… Token berhasil didapat dari CapSolver!", "SUCCESS")
                token_injected = await inject_turnstile_token(page, solved_token)
                
                if token_injected:
                    await asyncio.sleep(2)
                    success_found = await detect_success_notification(page, 15)
                    if success_found:
                        await send_telegram_log("ðŸŽ‰ SUKSES dengan CapSolver!", "SUCCESS")
                        return "capsolver_success"
    
    await send_telegram_log("âŒ Semua metode penanganan Turnstile gagal", "ERROR")
    return "failed"

async def main():
    async with async_playwright() as p:
        await send_telegram_log(f"ðŸ”— Menghubungkan ke CDP: {CDP_URL}", "INFO")
        
        try:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
            await send_telegram_log("âœ… Koneksi CDP berhasil", "SUCCESS")
        except Exception as e:
            await send_telegram_log(f"âŒ Error koneksi CDP di {CDP_URL}: {e}", "ERROR")
            await send_telegram_log("âš ï¸ Pastikan profil GoLogin berhasil start dan DevTools endpoint (CDP) aktif", "WARNING")
            # Exit non-zero agar caller (watcher) mengetahui kegagalan dan tidak lanjut ke saldo/wnfw
            import os
            os._exit(3)

        try:
            # Gunakan context & page yang SUDAH ADA dari GoLogin. Dilarang membuat tab baru.
            if not browser.contexts:
                await send_telegram_log("âŒ ERROR: Tidak ada context aktif dari GoLogin. Kebijakan melarang membuat context/tab baru.", "ERROR")
                import os as _os
                _os._exit(3)
            ctx = browser.contexts[0]

            # Anti-tab-baru: injeksi script agar window.open/target=_blank tetap di tab yang sama
            try:
                await ctx.add_init_script(
                    """
                    (() => {
                        try {
                            // Paksa window.open tetap di tab yang sama
                            const _open = window.open;
                            window.open = function(url, target, features){
                                try { if (url) { location.href = url; } } catch(e) {}
                                return window;
                            };
                            // Ubah semua link target=_blank menjadi _self (awal + dynamic)
                            const patchLinks = (root) => {
                                try {
                                    const list = (root || document).querySelectorAll('a[target="_blank"]');
                                    for (const a of list) { a.setAttribute('target','_self'); }
                                } catch(e) {}
                            };
                            document.addEventListener('click', (e) => {
                                const a = e.target && e.target.closest ? e.target.closest('a[target="_blank"]') : null;
                                if (a) { a.setAttribute('target','_self'); }
                            }, true);
                            new MutationObserver(muts => {
                                muts.forEach(m => {
                                    m.addedNodes && m.addedNodes.forEach(n => {
                                        if (n && n.nodeType === 1) { patchLinks(n); }
                                    });
                                });
                            }).observe(document.documentElement, {childList:true, subtree:true});
                            // Patch awal
                            document.addEventListener('DOMContentLoaded', () => patchLinks(document));
                            patchLinks(document);
                        } catch (e) {}
                    })();
                    """
                )
            except Exception:
                pass

            # Ambil page yang sudah ada; JANGAN membuat new_page
            pages = ctx.pages if hasattr(ctx, 'pages') else []
            if not pages:
                await send_telegram_log("âŒ ERROR: Tidak ditemukan tab aktif pada GoLogin. Kebijakan melarang membuat tab baru.", "ERROR")
                import os as _os
                _os._exit(3)
            page = pages[0]

            # Penjaga terakhir: jika ada popup/tab baru muncul, tutup segera
            try:
                ctx.on('page', lambda p: asyncio.create_task(p.close()))
                page.on('popup', lambda p: asyncio.create_task(p.close()))
            except Exception:
                pass

            await send_telegram_log("ðŸ”„ Menggunakan tab yang sudah ada dari GoLogin", "INFO")
            # Pasang event agar checkbox di iframe langsung diklik saat frame CF attach/navigate
            try:
                page.on('frameattached', lambda fr: asyncio.create_task(auto_click_checkbox_if_found(page)))
                page.on('framenavigated', lambda fr: asyncio.create_task(auto_click_checkbox_if_found(page)))
            except Exception:
                pass
        except Exception as e:
            print(f"[BOOT] Error creating page: {e}")
            return

        # Mode fast-execute: langsung eksekusi tanpa loop monitoring panjang
        # Jalankan bot utama dengan eksekusi sederhana
        try:
            print("[SIMPLE] Memulai eksekusi sederhana: refresh â†’ klik rain â†’ scan 24 jam")
            await send_telegram_log("ðŸš€ Memulai eksekusi sederhana", "INFO")
            
