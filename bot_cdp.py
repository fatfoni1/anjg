import asyncio, time, random, json, re
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
        with open('fast_exec_result.json', 'w') as f:
            json.dump({"status": status, "ts": time.time()}, f)
    except Exception:
        pass

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
    print("[INIT] Telegram notifier initialized")

async def send_event(message: str):
    """Kirim event feed sederhana ke Telegram (jika tersedia)."""
    if telegram:
        try:
            await telegram.send_message(f"🔔 {message}")
        except Exception as e:
            print(f"[TELEGRAM] Error sending event: {e}")

async def send_telegram_log(message: str, level: str = "INFO"):
    """Kirim log langsung ke Telegram dengan level yang berbeda"""
    if telegram:
        try:
            emoji_map = {
                "INFO": "ℹ️",
                "SUCCESS": "✅", 
                "WARNING": "⚠️",
                "ERROR": "❌",
                "DEBUG": "🔍"
            }
            emoji = emoji_map.get(level, "📝")
            await telegram.send_message(f"{emoji} <b>[{level}]</b> {message}")
        except Exception as e:
            print(f"[TELEGRAM] Error sending log: {e}")
    else:
        # Fallback ke print jika telegram tidak tersedia
        print(f"[{level}] {message}")

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
    print("[ALREADY_CHECK] Mengecek status already joined...")
    
    try:
        # Cek di main page
        for selector in ALREADY_JOINED_SELECTORS:
            if await page.locator(selector).count() > 0:
                element = page.locator(selector).first
                if await element.is_visible():
                    text = await element.text_content()
                    print(f"[ALREADY_CHECK] Already joined terdeteksi: {text}")
                    return True
        
        # Cek di semua frame
        for frame in page.frames:
            try:
                for selector in ALREADY_JOINED_SELECTORS:
                    if await frame.locator(selector).count() > 0:
                        element = frame.locator(selector).first
                        if await element.is_visible():
                            text = await element.text_content()
                            print(f"[ALREADY_CHECK] Already joined terdeteksi di frame: {text}")
                            return True
            except Exception:
                continue
        
        # Cek keyword already
        already_keywords = ["already", "sudah", "duplicate", "participated", "entered before"]
        for keyword in already_keywords:
            if await page.locator(f'text=/{keyword}/i').count() > 0:
                print(f"[ALREADY_CHECK] Keyword already ditemukan: {keyword}")
                return True
                
            # Cek juga di frame
            for frame in page.frames:
                try:
                    if await frame.locator(f'text=/{keyword}/i').count() > 0:
                        print(f"[ALREADY_CHECK] Keyword already ditemukan di frame: {keyword}")
                        return True
                except Exception:
                    continue
                    
    except Exception as e:
        print(f"[ALREADY_CHECK] Error: {e}")
    
    print("[ALREADY_CHECK] Tidak ada indikasi already joined")
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
            print("[RELOAD] Page crashed terdeteksi → reload Flip sekarang")
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
            await send_telegram_log("🎯 RAIN ACTIVE TERDETEKSI! (prizeBox)", "SUCCESS")
            return PRIZEBOX_ACTIVE
    except PWTimeout:
        pass
    except Exception as e:
        pass

    try:
        if await page.locator(BTN_ACTIVE).count() > 0:
            await page.locator(BTN_ACTIVE).first.wait_for(state="visible", timeout=1500)
            await send_telegram_log("🎯 RAIN ACTIVE TERDETEKSI! (button)", "SUCCESS")
            return BTN_ACTIVE
    except PWTimeout:
        pass
    except Exception as e:
        pass

    try:
        loc = page.locator(f'button:has({JOIN_TEXT_ACTIVE})').first
        if await loc.count() > 0:
            await loc.wait_for(state="visible", timeout=1500)
            await send_telegram_log("🎯 RAIN ACTIVE TERDETEKSI! (join text)", "SUCCESS")
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
        await send_telegram_log(f"✅ Berhasil klik tombol Rain: {btn_selector}", "SUCCESS")
        return True
    except Exception as e:
        await send_telegram_log(f"❌ Gagal klik tombol Rain: {e}", "ERROR")
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
    
    print("[TURNSTILE] Checkbox tidak ditemukan di manapun")
    return False

async def wait_turnstile_token(page, timeout_ms):
    """Tunggu token Turnstile terisi (jika elemen ada)."""
    print("[TURNSTILE] Menunggu token Turnstile…")
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
            print("[TURNSTILE] Token terdeteksi 🥳")
            return val
        if i % 5 == 0:
            print(f"[TURNSTILE] …menunggu token ({i}s)")
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
    """Deteksi cepat notifikasi sukses tanpa timeout panjang"""
    try:
        # Cek di main page
        for selector in SUCCESS_SELECTORS:
            if await page.locator(selector).count() > 0:
                element = page.locator(selector).first
                if await element.is_visible():
                    text = await element.text_content()
                    print(f"[SUCCESS_QUICK] Notifikasi sukses ditemukan di main page: {text}")
                    return True
        
        # Cek di semua frame/iframe
        for frame in page.frames:
            try:
                frame_url = (frame.url or "").lower()
                # Skip frame cloudflare/turnstile
                if any(k in frame_url for k in ['cloudflare', 'turnstile', 'challenges.cloudflare.com']):
                    continue
                    
                for selector in SUCCESS_SELECTORS:
                    if await frame.locator(selector).count() > 0:
                        element = frame.locator(selector).first
                        if await element.is_visible():
                            text = await element.text_content()
                            print(f"[SUCCESS_QUICK] Notifikasi sukses ditemukan di frame {frame.url}: {text}")
                            return True
            except Exception:
                continue
        
        # Check for success keywords
        success_keywords = ["successfully", "success", "joined", "entered", "complete", "done", "berhasil"]
        for keyword in success_keywords:
            if await page.locator(f'text=/{keyword}/i').count() > 0:
                print(f"[SUCCESS_QUICK] Keyword sukses ditemukan di main page: {keyword}")
                return True
                
            # Cek juga di frame flip.gg (exclude cf/turnstile)
            for frame in page.frames:
                try:
                    frame_url = (frame.url or "").lower()
                    if 'flip.gg' not in frame_url:
                        continue
                    if any(k in frame_url for k in ['cloudflare', 'turnstile', 'challenges.cloudflare.com']):
                        continue
                        
                    if await frame.locator(f'text=/{keyword}/i').count() > 0:
                        print(f"[SUCCESS_QUICK] Keyword sukses ditemukan di frame {frame.url}: {keyword}")
                        return True
                except Exception:
                    continue
                    
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
                                print(f"[AUTO_CHECKBOX] ✅ Checkbox diklik otomatis di iframe: {selector}")
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
                                print(f"[AUTO_CHECKBOX] ✅ Checkbox Turnstile diklik otomatis di main page: {selector}")
                                return True
                        except Exception:
                            # Jika tidak bisa cek parent, coba klik jika selector mengandung pattern Turnstile
                            if any(pattern in selector for pattern in ["cb-", "checkbox", "turnstile", "verify"]):
                                await element.click(force=True, timeout=2000)
                                print(f"[AUTO_CHECKBOX] ✅ Checkbox diklik otomatis di main page (pattern): {selector}")
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
                                    print(f"[AUTO_CHECKBOX] ✅ Checkbox diklik otomatis di frame CF: {selector}")
                                    return True
                                else:
                                    # Untuk frame lain, cek konteks
                                    try:
                                        parent = element.locator('xpath=..')
                                        parent_text = await parent.text_content()
                                        if parent_text and ("verify" in parent_text.lower() or "human" in parent_text.lower()):
                                            await element.click(force=True, timeout=2000)
                                            print(f"[AUTO_CHECKBOX] ✅ Checkbox diklik otomatis di frame: {selector}")
                                            return True
                                    except Exception:
                                        pass
                    except Exception:
                        continue
            except Exception:
                continue
        
        return False
        
    except Exception as e:
        print(f"[AUTO_CHECKBOX] Error: {e}")
        return False

async def continuous_success_scanner(page):
    """Background task untuk scan notifikasi sukses secara kontinyu di frame flip.gg"""
    print("[SUCCESS_SCANNER] Memulai background scanner untuk notifikasi sukses...")
    
    try:
        while True:
            try:
                # Cek notifikasi sukses
                success_found = await detect_success_notification_quick(page)
                if success_found:
                    print("[SUCCESS_SCANNER] ✅ SUKSES ditemukan!")
                    return "success"
                
                # Cek already joined
                already_found = await check_already_joined(page)
                if already_found:
                    print("[SUCCESS_SCANNER] ℹ️ Already joined ditemukan!")
                    return "already"
                
                # PENTING: Cek dan klik checkbox kapanpun ditemukan
                checkbox_found = await auto_click_checkbox_if_found(page)
                if checkbox_found:
                    print("[SUCCESS_SCANNER] 🎯 Checkbox ditemukan dan diklik otomatis!")
                
                # Scan setiap 0.5 detik untuk responsivitas tinggi
                await asyncio.sleep(0.5)
                
            except Exception as e:
                print(f"[SUCCESS_SCANNER] Error dalam scan: {e}")
                await asyncio.sleep(1)
                continue
                
    except asyncio.CancelledError:
        print("[SUCCESS_SCANNER] Background scanner dibatalkan")
        raise
    except Exception as e:
        print(f"[SUCCESS_SCANNER] Fatal error: {e}")
        return "error"

async def handle_turnstile_challenge_with_refresh_retry(page):
    """Handle Turnstile challenge dengan sistem refresh dan retry - batas 1 menit setelah klik checkbox"""
    await send_telegram_log("🚀 Memulai alur penanganan Turnstile dengan batas waktu 1 menit...", "INFO")
    
    # Start background task untuk scan notifikasi sukses secara kontinyu dari awal
    success_scanner_task = asyncio.create_task(continuous_success_scanner(page))
    
    refresh_attempt = 0
    
    while True:  # Loop unlimited sampai ada hasil atau timeout
        refresh_attempt += 1
        await send_telegram_log(f"🔄 Refresh attempt #{refresh_attempt}", "INFO")
        
        # Cek apakah success scanner sudah menemukan sukses dari awal
        if success_scanner_task.done():
            try:
                result = success_scanner_task.result()
                if result == "success":
                    await send_telegram_log("🎉 SUKSES ditemukan oleh background scanner!", "SUCCESS")
                    # Tutup GoLogin dan kembali ke watcher dengan cooldown 3 menit
                    return "manual_success"
                elif result == "already":
                    await send_telegram_log("ℹ️ Already joined ditemukan oleh background scanner", "INFO")
                    # Tutup GoLogin dan kembali ke watcher dengan cooldown 3 menit
                    return "already_joined"
            except Exception as e:
                await send_telegram_log(f"❌ Error pada success scanner: {e}", "ERROR")
        
        # LANGKAH 1: Tunggu iframe Turnstile muncul
        try:
            await send_telegram_log("⏳ Menunggu iframe Cloudflare Turnstile...", "INFO")
            await page.wait_for_selector(IFRAME_TURNSTILE, timeout=30_000)
            await send_telegram_log("✅ Iframe Turnstile terdeteksi!", "SUCCESS")
        except PWTimeout:
            await send_telegram_log("❌ Tidak ada iframe Turnstile - cek hasil langsung", "WARNING")
            # Jika tidak ada iframe, mungkin tidak ada captcha sama sekali
            success_found = await detect_success_notification_quick(page)
            if success_found:
                success_scanner_task.cancel()
                await send_telegram_log("🎉 Sukses otomatis tanpa Turnstile!", "SUCCESS")
                return "instant_success"
            if await check_already_joined(page):
                success_scanner_task.cancel()
                await send_telegram_log("ℹ️ Already joined - tutup GoLogin dan cooldown 3 menit", "INFO")
                return "already_joined"
            
            # Jika tidak ada iframe dan tidak ada sukses, refresh dan coba lagi
            await send_telegram_log("🔄 Tidak ada iframe, refresh dan klik rain lagi...", "WARNING")
            await refresh_page_and_click_rain(page)
            continue

        # LANGKAH 2: Tunggu iframe selesai loading
        await send_telegram_log("⏳ Menunggu iframe selesai loading...", "INFO")
        loading_timeout = time.time() + 20  # Maksimal 20 detik tunggu loading
        
        while time.time() < loading_timeout:
            try:
                # Cek apakah masih loading
                if not await is_turnstile_loading(page):
                    await send_telegram_log("✅ Loading selesai - mengecek checkbox...", "SUCCESS")
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
                            await send_telegram_log(f"✅ Checkbox ditemukan: {selector}", "SUCCESS")
                            break
                    except Exception:
                        continue
        except Exception as e:
            await send_telegram_log(f"❌ Error saat cek checkbox: {e}", "ERROR")
        
        # LANGKAH 4: Jika tidak ada checkbox setelah loading selesai → refresh dan coba lagi
        if not checkbox_found:
            await send_telegram_log("⚠️ Iframe loaded tapi TIDAK ADA CHECKBOX!", "WARNING")
            await send_telegram_log("🔄 Refresh halaman dan klik rain lagi...", "INFO")
            await refresh_page_and_click_rain(page)
            continue

        # LANGKAH 5: Checkbox ditemukan → klik dan tunggu notifikasi sukses MAKSIMAL 1 MENIT
        await send_telegram_log("🎯 Checkbox ditemukan - klik dan tunggu notifikasi maksimal 1 menit!", "SUCCESS")
        
        # Klik checkbox
        checkbox_clicked = await click_turnstile_checkbox(page)
        if not checkbox_clicked:
            await send_telegram_log("❌ Gagal klik checkbox", "ERROR")
            await send_telegram_log("🔄 Refresh dan coba lagi...", "INFO")
            await refresh_page_and_click_rain(page)
            continue
        
        await send_telegram_log("✅ Checkbox berhasil diklik - menunggu notifikasi maksimal 1 menit!", "SUCCESS")
        
        # LANGKAH 6: Tunggu notifikasi sukses MAKSIMAL 1 MENIT setelah klik checkbox
        wait_start = time.time()
        max_wait_time = 60  # 1 menit = 60 detik
        
        await send_telegram_log(f"⏰ Mulai menunggu notifikasi selama {max_wait_time} detik...", "INFO")
        
        while time.time() - wait_start < max_wait_time:
            # Cek apakah background scanner menemukan hasil
            if success_scanner_task.done():
                try:
                    result = success_scanner_task.result()
                    if result == "success":
                        elapsed_time = time.time() - wait_start
                        await send_telegram_log(f"🎉 SUKSES! Notifikasi ditemukan dalam {elapsed_time:.1f} detik!", "SUCCESS")
                        
                        # Kirim notifikasi Telegram khusus sukses
                        if telegram:
                            try:
                                await telegram.send_message(
                                    f"🎉 <b>SUKSES JOIN RAIN!</b>\n\n"
                                    f"✅ Checkbox Turnstile berhasil diklik\n"
                                    f"🎯 Notifikasi sukses terdeteksi\n"
                                    f"⏰ Waktu tunggu: {elapsed_time:.1f} detik\n"
                                    f"🔄 Refresh attempt ke-{refresh_attempt}\n\n"
                                    f"Konfirmasi: <i>Successfully joined rain!</i>"
                                )
                            except Exception as e:
                                print(f"[TELEGRAM] Error sending notification: {e}")
                        
                        # Tutup GoLogin dan kembali ke watcher dengan cooldown 3 menit
                        return "manual_success"
                    elif result == "already":
                        elapsed_time = time.time() - wait_start
                        await send_telegram_log(f"ℹ️ Already joined terdeteksi dalam {elapsed_time:.1f} detik!", "INFO")
                        
                        # Kirim notifikasi Telegram
                        if telegram:
                            try:
                                await telegram.send_message(
                                    f"ℹ️ <b>ALREADY JOINED</b>\n\n"
                                    f"⏰ Waktu tunggu: {elapsed_time:.1f} detik\n"
                                    f"🔄 Refresh attempt ke-{refresh_attempt}\n\n"
                                    f"You have already entered this rain!"
                                )
                            except Exception as e:
                                print(f"[TELEGRAM] Error sending notification: {e}")
                        
                        # Tutup GoLogin dan kembali ke watcher dengan cooldown 3 menit
                        return "already_joined"
                except Exception as e:
                    await send_telegram_log(f"❌ Error pada success scanner: {e}", "ERROR")
                    break
            
            # Update progress setiap 10 detik
            elapsed = time.time() - wait_start
            if int(elapsed) % 10 == 0 and int(elapsed) > 0:
                remaining = max_wait_time - elapsed
                await send_telegram_log(f"⏳ Menunggu notifikasi... sisa {remaining:.0f} detik", "INFO")
            
            await asyncio.sleep(0.5)
        
        # LANGKAH 7: Timeout 1 menit - tidak ada notifikasi sukses/already
        elapsed_time = time.time() - wait_start
        await send_telegram_log(f"⏰ TIMEOUT! Tidak ada notifikasi setelah {elapsed_time:.1f} detik", "WARNING")
        await send_telegram_log("🔄 Refresh halaman dan klik rain lagi...", "INFO")
        
        # Refresh dan coba lagi
        await refresh_page_and_click_rain(page)
        continue
    
    # Ini tidak akan pernah tercapai karena loop while True, tapi untuk safety
    success_scanner_task.cancel()
    return "timeout_continue"

async def refresh_page_and_click_rain(page):
    """Refresh halaman dan klik tombol rain lagi - TIDAK MEMBUKA TAB BARU"""
    try:
        await send_telegram_log("🔄 Melakukan refresh halaman (tanpa tab baru)...", "INFO")
        
        # Pastikan kita tetap di halaman yang sama, hanya refresh
        current_url = page.url
        await send_telegram_log(f"📍 URL saat ini: {current_url}", "DEBUG")
        
        # Refresh halaman yang sudah ada
        await page.reload(wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        
        # Verifikasi masih di halaman yang sama
        new_url = page.url
        await send_telegram_log(f"📍 URL setelah refresh: {new_url}", "DEBUG")
        
        # Tunggu dan klik tombol rain lagi
        await send_telegram_log("🎯 Mencari dan klik tombol rain setelah refresh...", "INFO")
        
        # Coba deteksi active dan klik
        max_wait_time = 15  # Maksimal 15 detik tunggu tombol rain
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            sel = await detect_active(page)
            if sel:
                clicked = await click_join(page, sel)
                if clicked:
                    await send_telegram_log("✅ Berhasil klik tombol rain setelah refresh!", "SUCCESS")
                    return True
            await asyncio.sleep(1)
        
        # Fallback: coba selector umum
        fallback_selectors = [
            "button:has-text('Join now')",
            "button:has-text('Join')",
            "button:has-text('Rain')",
            "button:has-text('Enter')",
        ]
        
        for selector in fallback_selectors:
            try:
                if await page.locator(selector).count() > 0:
                    btn = page.locator(selector).first
                    await btn.scroll_into_view_if_needed()
                    await btn.click()
                    await send_telegram_log(f"✅ Berhasil klik fallback tombol: {selector}", "SUCCESS")
                    return True
            except Exception:
                continue
        
        await send_telegram_log("❌ Tidak dapat menemukan tombol rain setelah refresh", "ERROR")
        return False
        
    except Exception as e:
        await send_telegram_log(f"❌ Error saat refresh dan klik rain: {e}", "ERROR")
        return False

async def close_info_modal_if_present(page):
    """Tutup dialog informasi Rain jika muncul dengan klik di luar/backdrop/ESC."""
    try:
        # Deteksi konten dialog
        dlg = page.locator('.MuiDialogContent-root')
        if await dlg.count() > 0:
            # Coba klik backdrop Material-UI
            try:
                back = page.locator('.MuiBackdrop-root').first
                if await back.count() > 0:
                    await back.click(force=True)
                    await asyncio.sleep(0.2)
            except Exception:
                pass
            # Coba tekan Escape
            try:
                await page.keyboard.press('Escape')
            except Exception:
                pass
            # Coba klik koordinat kecil di pojok (di luar dialog)
            try:
                await page.mouse.click(5, 5)
            except Exception:
                pass
    except Exception:
        pass

async def click_rain_with_30s_retry(page, max_attempts: int = 4) -> bool:
    """Klik Rain lalu tunggu maksimal 30 detik untuk notifikasi sukses/already.
    Jika tidak muncul, reload dan ulangi hingga max_attempts. Juga auto-handle checkbox.
    Return True jika sukses/already, False jika gagal semua percobaan.
    """
    async def try_click_rain_once_local() -> bool:
        # Coba selector prioritas
        sel = await detect_active(page)
        if sel:
            return await click_join(page, sel)
        # Fallback berbasis teks
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
                        print(f"[CLICK] Klik fallback tombol: {fs}")
                        return True
                    except Exception as e:
                        print(f"[CLICK] Gagal klik fallback {fs}: {e}")
                        continue
            except Exception:
                continue
        return False

    for attempt in range(1, max_attempts + 1):
        print(f"[FAST-30S] Attempt {attempt}/{max_attempts}: klik Rain dan tunggu 30s untuk notifikasi…")
        # Tutup dialog info jika muncul
        await close_info_modal_if_present(page)

        # Klik Rain
        clicked = await try_click_rain_once_local()
        if not clicked:
            print("[FAST-30S] Tombol Rain tidak ditemukan pada attempt ini → reload…")
            try:
                await page.reload(wait_until='networkidle')
                await asyncio.sleep(2)
            except Exception:
                pass
            continue

        # Start scanner yang juga auto-klik checkbox saat ditemukan
        task = asyncio.create_task(continuous_success_scanner(page))
        result = None
        try:
            result = await asyncio.wait_for(task, timeout=30)
        except asyncio.TimeoutError:
            try:
                task.cancel()
            except Exception:
                pass
            result = None
        except Exception as e:
            print(f"[FAST-30S] Error scanner: {e}")
            result = None

        if result in ("success", "already"):
            # Kirim notifikasi
            if telegram:
                try:
                    if result == 'success':
                        await telegram.send_message("🎉 <b>SUKSES JOIN RAIN!</b>\n\nKonfirmasi: <i>Successfully joined rain!</i>")
                    else:
                        await telegram.send_message("ℹ️ <b>ALREADY JOINED</b>\n\nYou have already entered this rain!")
                except Exception:
                    pass
            _save_fast_result('success')
            return True

        # Tidak ada hasil dalam 30 detik → reload dan coba lagi
        print("[FAST-30S] Tidak ada notifikasi dalam 30 detik → reload dan retry…")
        try:
            await page.reload(wait_until='networkidle')
            await asyncio.sleep(2)
        except Exception:
            pass

    return False

async def handle_turnstile_challenge(page):
    """Wrapper untuk handle_turnstile_challenge_with_refresh_retry dengan fallback ke metode lama"""
    try:
        # Gunakan metode baru dengan refresh retry
        return await handle_turnstile_challenge_with_refresh_retry(page)
    except Exception as e:
        await send_telegram_log(f"❌ Error pada metode refresh retry: {e}", "ERROR")
        await send_telegram_log("🔄 Fallback ke metode lama...", "WARNING")
        
        # Fallback ke metode lama (kode asli yang sudah ada)
        return await handle_turnstile_challenge_legacy(page)

async def handle_turnstile_challenge_legacy(page):
    """Handle Turnstile challenge dengan logika lama (backup)"""
    await send_telegram_log("Memulai alur penanganan Turnstile (legacy)...", "INFO")

    # LANGKAH 1: Tunggu iframe Turnstile muncul.
    try:
        await send_telegram_log("Menunggu iframe Cloudflare Turnstile...", "INFO")
        await page.wait_for_selector(IFRAME_TURNSTILE, timeout=30_000)
        await send_telegram_log("✅ Iframe Turnstile terdeteksi!", "SUCCESS")
    except PWTimeout:
        await send_telegram_log("Tidak ada iframe Turnstile - cek hasil langsung", "WARNING")
        # Jika tidak ada iframe, mungkin tidak ada captcha sama sekali.
        success_found = await detect_success_notification(page, 5)
        if success_found:
            await send_telegram_log("✅ Sukses otomatis tanpa Turnstile!", "SUCCESS")
            return "instant_success"
        if await check_already_joined(page):
            set_already_joined_cooldown()
            await send_telegram_log("ℹ️ Already joined - cooldown 3 menit dimulai", "INFO")
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
                        await send_telegram_log(f"✅ Checkbox ditemukan: {selector}", "SUCCESS")
                        break
                except Exception:
                    continue
    except Exception as e:
        await send_telegram_log(f"❌ Error saat cek checkbox: {e}", "ERROR")
    
    if not checkbox_found:
        await send_telegram_log("⚠️ Iframe loaded tapi tidak ada checkbox - perlu refresh!", "WARNING")
        return "no_checkbox"

    # LANGKAH 3: Loop klik checkbox sampai ada notifikasi sukses
    await send_telegram_log("🎯 Checkbox ditemukan - memulai loop klik sampai sukses!", "INFO")
    max_click_attempts = 10  # Maksimal 10 kali klik
    click_attempt = 0
    
    while click_attempt < max_click_attempts:
        click_attempt += 1
        await send_telegram_log(f"🔄 Percobaan klik checkbox #{click_attempt}/10", "INFO")
        
        try:
            # Klik checkbox
            checkbox_clicked = await click_turnstile_checkbox(page)
            if not checkbox_clicked:
                await send_telegram_log("❌ Gagal klik checkbox - coba lagi...", "WARNING")
                await asyncio.sleep(2)
                continue
            
            await send_telegram_log("✅ Checkbox berhasil diklik - menunggu hasil...", "SUCCESS")
            await asyncio.sleep(3)  # Tunggu sebentar untuk processing
            
            # Cek apakah ada notifikasi sukses
            success_found = await detect_success_notification(page, 10)
            if success_found:
                await send_telegram_log("🎉 SUKSES! Notifikasi sukses ditemukan setelah klik checkbox!", "SUCCESS")
                
                # Kirim notifikasi Telegram khusus sukses
                if telegram:
                    try:
                        await telegram.send_message(
                            f"🎉 <b>SUKSES KLIK CHECKBOX!</b>\n\n"
                            f"✅ Checkbox Turnstile berhasil diklik\n"
                            f"🎯 Notifikasi sukses terdeteksi\n"
                            f"🔄 Percobaan ke-{click_attempt}\n"
                            f"⏰ Waktu: {time.strftime('%H:%M:%S', time.localtime())}"
                        )
                    except Exception as e:
                        print(f"[TELEGRAM] Error sending notification: {e}")
                
                return "manual_success"
            
            # Cek apakah already joined
            if await check_already_joined(page):
                await send_telegram_log("ℹ️ Already joined terdeteksi setelah klik checkbox", "INFO")
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
                        await send_telegram_log("🔄 Checkbox masih ada - kemungkinan stuck, akan klik lagi", "WARNING")
            except Exception:
                pass
            
            if not checkbox_still_there:
                # Checkbox hilang tapi tidak ada notifikasi sukses, tunggu lebih lama
                await send_telegram_log("⏳ Checkbox hilang - tunggu notifikasi sukses lebih lama...", "INFO")
                success_found = await detect_success_notification(page, 15)
                if success_found:
                    await send_telegram_log("🎉 SUKSES! Notifikasi sukses ditemukan setelah tunggu!", "SUCCESS")
                    return "manual_success"
                else:
                    await send_telegram_log("❌ Tidak ada notifikasi sukses - mungkin gagal", "ERROR")
                    break
            
            # Jika checkbox masih ada, lanjut loop untuk klik lagi
            await asyncio.sleep(2)
            
        except Exception as e:
            await send_telegram_log(f"❌ Error saat klik checkbox #{click_attempt}: {e}", "ERROR")
            await asyncio.sleep(2)
            continue
    
    await send_telegram_log(f"❌ Gagal mendapat notifikasi sukses setelah {max_click_attempts} kali klik checkbox", "ERROR")
    
    # LANGKAH 4: Fallback ke CapSolver jika manual gagal
    if capsolver and AUTO_SOLVE_CAPTCHA:
        await send_telegram_log("🔄 Fallback ke CapSolver...", "INFO")
        website_url, sitekey, action, cdata = await extract_turnstile_info(page)
        
        if sitekey and sitekey != "0x4AAAAAAADnPIDROlWd_wc":
            solved_token = await capsolver.solve_turnstile(
                website_url=website_url,
                website_key=sitekey,
                action=action,
                cdata=cdata
            )
            
            if solved_token:
                await send_telegram_log("✅ Token berhasil didapat dari CapSolver!", "SUCCESS")
                token_injected = await inject_turnstile_token(page, solved_token)
                
                if token_injected:
                    await asyncio.sleep(2)
                    success_found = await detect_success_notification(page, 15)
                    if success_found:
                        await send_telegram_log("🎉 SUKSES dengan CapSolver!", "SUCCESS")
                        return "capsolver_success"
    
    await send_telegram_log("❌ Semua metode penanganan Turnstile gagal", "ERROR")
    return "failed"

async def main():
    async with async_playwright() as p:
        print("[BOOT] Connect CDP:", CDP_URL)
        
        try:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
            print("[BOOT] CDP connection berhasil")
        except Exception as e:
            print(f"[BOOT] Error connecting to CDP at {CDP_URL}: {e}")
            print("[BOOT] Pastikan profil GoLogin berhasil start dan DevTools endpoint (CDP) aktif.")
            # Exit non-zero agar caller (watcher) mengetahui kegagalan dan tidak lanjut ke saldo/wnfw
            import os
            os._exit(3)

        try:
            # Gunakan context & page yang SUDAH ADA dari GoLogin. Dilarang membuat tab baru.
            if not browser.contexts:
                print("[BOOT] ERROR: Tidak ada context aktif dari GoLogin. Kebijakan melarang membuat context/tab baru.")
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
                print("[BOOT] ERROR: Tidak ditemukan tab aktif pada GoLogin. Kebijakan melarang membuat tab baru.")
                import os as _os
                _os._exit(3)
            page = pages[0]

            # Penjaga terakhir: jika ada popup/tab baru muncul, tutup segera
            try:
                ctx.on('page', lambda p: asyncio.create_task(p.close()))
                page.on('popup', lambda p: asyncio.create_task(p.close()))
            except Exception:
                pass

            print("[BOOT] Reuse tab yang sudah ada dari GoLogin")
        except Exception as e:
            print(f"[BOOT] Error creating page: {e}")
            return

        # Mode fast-execute: langsung eksekusi tanpa loop monitoring panjang
        if FAST_EXECUTE:
            print("[FAST] Mode fast_execute aktif. Menjalankan eksekusi cepat.")

            async def detect_text_in_flip_frames(text: str, timeout_sec: int) -> bool:
                """Cari teks pada main page dan frames domain flip.gg (exclude cf/turnstile). Partial match, case-insensitive."""
                deadline = time.time() + timeout_sec
                while time.time() < deadline:
                    try:
                        # main page
                        for sel in [f"text=/{re.escape(text)}/i", f"span:has-text('{text}')", f"div:has-text('{text}')"]:
                            try:
                                if await page.locator(sel).count() > 0:
                                    el = page.locator(sel).first
                                    if await el.is_visible():
                                        return True
                            except Exception:
                                pass
                        # frames flip.gg (kecuali cf/turnstile)
                        for fr in page.frames:
                            u = (fr.url or '').lower()
                            if not u or 'flip.gg' not in u:
                                continue
                            if any(k in u for k in ['cloudflare', 'turnstile', 'challenges.cloudflare.com']):
                                continue
                            for sel in [f"text=/{re.escape(text)}/i", f"span:has-text('{text}')", f"div:has-text('{text}')"]:
                                try:
                                    if await fr.locator(sel).count() > 0:
                                        el = fr.locator(sel).first
                                        if await el.is_visible():
                                            return True
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    await asyncio.sleep(0.5)
                return False

            async def click_rain_once() -> bool:
                # Coba deteksi active standar dulu
                sel = await detect_active(page)
                if sel:
                    return await click_join(page, sel)
                # Fallback: coba selector berbasis teks umum
                fallback_selectors = [
                    "button:has-text('Join now')",
                    "button:has-text('Join')",
                    "button:has-text('Rain')",
                    "button:has-text('Enter')",
                ]
                for fs in fallback_selectors:
                    try:
                        if await page.locator(fs).count() > 0:
                            btn = page.locator(fs).first
                            try:
                                await btn.scroll_into_view_if_needed()
                            except Exception:
                                pass
                            try:
                                await btn.click()
                                print(f"[CLICK] Klik fallback tombol: {fs}")
                                return True
                            except Exception as e:
                                print(f"[CLICK] Gagal klik fallback {fs}: {e}")
                                continue
                    except Exception:
                        continue
                return False

            # buka target (+ wait & polling), fallback ke homepage jika perlu
            async def try_click_with_wait(total_wait: int = 30) -> bool:
                deadline = time.time() + total_wait
                scroll_y = 0
                while time.time() < deadline:
                    if await click_rain_once():
                        return True
                    # Scroll ringan untuk memicu lazy load/visibility
                    try:
                        scroll_y += 400
                        await page.evaluate("y => { window.scrollBy(0, y); }", 400)
                    except Exception:
                        pass
                    await asyncio.sleep(1)
                return False

            try:
                # Jangan navigasi ke URL; hanya reload tab yang sama
                await page.reload(wait_until="networkidle")
                await asyncio.sleep(2)
            except Exception as e:
                print(f"[FAST] Gagal reload awal: {e}")

            # Mulai watcher notifikasi global (exclude frame CF)
            async def notification_watchdog(page):
                """Pantau notifikasi success/already di semua frame flip.gg (exclude cf). Return 'success' atau 'already'."""
                targets = [('success', 'Successfully joined rain!'), ('already', 'You have already entered this rain!')]
                while True:
                    for label, text in targets:
                        try:
                            if await detect_text_in_flip_frames(text, 1):
                                return label
                        except Exception:
                            pass
                    await asyncio.sleep(0.3)

            notif_task = asyncio.create_task(notification_watchdog(page))

            ok = await try_click_with_wait(60)
            if not ok:
                # reload sekali sebelum fallback
                try:
                    await page.reload(wait_until="networkidle")
                    await asyncio.sleep(2)
                    ok = await try_click_with_wait(45)
                except Exception:
                    pass
            if not ok:
                # Dilarang navigasi ke homepage. Coba reload lagi sebagai fallback
                try:
                    await page.reload(wait_until="networkidle")
                    await asyncio.sleep(2)
                    ok = await try_click_with_wait(45)
                except Exception as e:
                    print(f"[FAST] Gagal fallback reload: {e}")
                    ok = False

            if not ok:
                print("[FAST] Tidak ada tombol Rain yang bisa diklik.")
                _save_fast_result('failed')
                return

            # Alur baru sesuai SOP + diferensiasi CRASH vs LOADING vs CapSolver
            result = await click_rain_with_30s_retry(page, max_attempts=4)
            if result:
                return
            print("[FAST] Gagal join rain dalam 4 percobaan 30 detik. Mengakhiri eksekusi cepat.")
            if telegram:
                try:
                    await telegram.send_message("❌ Gagal join rain dalam 4 percobaan (30 detik per percobaan).")
                except Exception:
                    pass
            _save_fast_result('failed')
            return

            success_after_checkbox = False
            crashed_prev = False
            for attempt in range(1, 4):
                print(f"[FAST] Attempt {attempt}/3: klik Join Rain + tangani Turnstile")

                # Cek apakah notifikasi global sudah terdeteksi
                if 'notif_task' in locals() and notif_task and notif_task.done():
                    label = None
                    try:
                        label = notif_task.result()
                    except Exception:
                        label = None
                    if label:
                        if telegram:
                            try:
                                if label == 'success':
                                    await telegram.send_message("🎉 <b>SUKSES JOIN RAIN!</b>\n\nKonfirmasi: <i>Successfully joined rain!</i>")
                                else:
                                    await telegram.send_message("ℹ️ <b>ALREADY JOINED</b>\n\nYou have already entered this rain!")
                            except Exception:
                                pass
                        # Diam 1 menit sebelum menutup GoLogin (exit bot)
                        await asyncio.sleep(60)
                        _save_fast_result('success')
                        return

                # Reload hanya jika attempt sebelumnya terdeteksi CRASH Turnstile
                if attempt > 1 and crashed_prev:
                    print("[FAST] Attempt sebelumnya CRASH Turnstile → reload halaman sebelum lanjut")
                    try:
                        await page.reload(wait_until='networkidle')
                        await asyncio.sleep(2)
                    except Exception as e:
                        print(f"[FAST] Reload gagal: {e}")
                        crashed_prev = True
                        continue

                # STEP 1: Klik Join Rain (selalu di SETIAP attempt)
                try_wait = 10 if attempt > 1 else 15
                if not await try_click_with_wait(try_wait):
                    print("[FAST] Tombol Rain tidak ditemukan pada attempt ini.")
                    crashed_prev = await is_turnstile_crashed(page) or await check_page_crashed(page)
                    continue

                # STEP 2: Tangani Turnstile secara komprehensif (klik checkbox / suntik CapSolver)
                ts_result = await handle_turnstile_challenge(page)

                if ts_result in ("instant_success", "manual_success", "capsolver_success"):
                    # Sukses dari jalur Turnstile (manual/invisible/CapSolver)
                    success_after_checkbox = True
                    # Kirim notifikasi sukses dan langsung exit untuk tutup GoLogin
                    if telegram:
                        try:
                            await telegram.send_message(
                                "🎉 <b>SUKSES JOIN RAIN!</b>\n\nKonfirmasi: <i>Successfully joined rain!</i>"
                            )
                        except Exception:
                            pass
                    _save_fast_result('success')
                    return
                elif ts_result == "already_joined":
                    # Deteksi ALREADY: kirim notif dan langsung exit untuk tutup GoLogin
                    if telegram:
                        try:
                            await telegram.send_message(
                                "ℹ️ <b>ALREADY JOINED</b>\n\nYou have already entered this rain!"
                            )
                        except Exception:
                            pass
                    _save_fast_result('success')
                    return
                elif ts_result == "no_turnstile":
                    # Tidak ada Turnstile; cek notifikasi sukses langsung
                    if await detect_text_in_flip_frames("Successfully joined rain!", 30):
                        success_after_checkbox = True
                        break
                    # Tidak sukses, bukan crash
                    crashed_prev = False
                    continue
                elif ts_result == "no_checkbox":
                    # Iframe muncul tapi tidak ada checkbox → refresh halaman dan coba klik Rain lagi
                    print("[FAST] Iframe Turnstile tanpa checkbox → refresh dan ulangi klik Rain.")
                    try:
                        await page.reload(wait_until='networkidle')
                        await asyncio.sleep(2)
                    except Exception as e:
                        print(f"[FAST] Refresh gagal setelah no_checkbox: {e}")
                    crashed_prev = False
                    continue
                else:
                    # ts_result == 'failed' atau nilai lain → bedakan crash vs loading
                    cf_crashed = await is_turnstile_crashed(page)
                    cf_loading = await is_turnstile_loading(page)
                    if cf_crashed:
                        print("[FAST] Gagal attempt: CRASH Turnstile terdeteksi (akan reload di attempt berikutnya)")
                        crashed_prev = True
                    elif cf_loading:
                        print("[FAST] Gagal attempt: Turnstile masih LOADING (tanpa reload)")
                        crashed_prev = False
                    else:
                        print("[FAST] Gagal attempt: Tidak sukses dan tidak terindikasi crash/ loading")
                        crashed_prev = False
                    continue

            if not success_after_checkbox:
                print("[FAST] Gagal mencapai status sukses setelah 3 attempt.")
                if telegram:
                    try:
                        await telegram.send_message(
                            "❌ Gagal join rain setelah 3 percobaan. Akan menghentikan GoLogin dan mengembalikan Watcher ke mode cek active."
                        )
                    except Exception:
                        pass
                _save_fast_result('failed')
                return

            # deteksi notifikasi akhir: success ATAU already (exclude frame CF)
            success = await detect_text_in_flip_frames("Successfully joined rain!", 60)
            if not success:
                already_end = await detect_text_in_flip_frames("You have already entered this rain!", 60)
                if not already_end:
                    print("[FAST] Tidak menemukan notifikasi success/already pada tahap akhir.")
                    _save_fast_result('failed')
                    return
                # Already detected
                if telegram:
                    await telegram.send_message("ℹ️ <b>ALREADY JOINED</b>\n\nYou have already entered this rain!")
                await asyncio.sleep(60)
                _save_fast_result('success')
                return

            # Success detected: kirim notif, diam 1 menit, lalu kembali ke watcher
            if telegram:
                await telegram.send_message("🎉 <b>SUKSES JOIN RAIN!</b>\n\nKonfirmasi: <i>Successfully joined rain!</i>")
            await asyncio.sleep(60)
            _save_fast_result('success')
            return

        # ====== MODE LAMA (loop monitoring) tetap seperti sebelumnya ======
        last_reload = 0.0
        last_join = 0.0
        loop_i = 0
        consecutive_errors = 0
        
        # Cek saldo Capsolver di awal
        if capsolver:
            try:
                balance = await capsolver.get_balance()
                if telegram and balance is not None:
                    await telegram.send_balance_notification(balance)
            except Exception as e:
                print(f"[INIT] Error checking Capsolver balance: {e}")
        
        while True:
            loop_i += 1
            print(f"\n===== LOOP {loop_i} =====")
            try:
                consecutive_errors = 0
                last_reload = await page_reload_if_needed(page, last_reload)

                sel = await detect_active(page)
                if not sel:
                    print(f"[IDLE] Tidak ada active, tidur {CHECK_INTERVAL_SEC}s")
                    await asyncio.sleep(CHECK_INTERVAL_SEC + random.random())
                    continue

                print("[ACTIVE] Active terdeteksi!")

                if await click_join(page, sel):
                    last_join = now()
                    turnstile_result = await handle_turnstile_challenge(page)
                    if turnstile_result in ["capsolver_success", "manual_success", "instant_success"]:
                        print("[FLOW] Turnstile selesai, cek notifikasi sukses...")
                        success_detected = await detect_success_notification(page, 15)
                        if success_detected:
                            print("[FLOW] Sukses terdeteksi!")
                            if telegram:
                                await telegram.send_message("🎉 Bot berhasil join rain!")
                        else:
                            print("[FLOW] Tidak ada notifikasi sukses.")
                    elif turnstile_result == "no_turnstile":
                        success_detected = await detect_success_notification(page, 5)
                        if success_detected:
                            print("[FLOW] Sukses join tanpa Turnstile!")
                            if telegram:
                                await telegram.send_message("🎉 Sukses join tanpa Turnstile!")
                    elif turnstile_result == "no_checkbox":
                        print("[FLOW] Iframe Turnstile loaded tetapi tidak ada checkbox → refresh dan coba lagi.")
                        try:
                            await page.goto(TARGET_URL, wait_until="networkidle", timeout=30000)
                            await asyncio.sleep(2)
                        except Exception as e:
                            print(f"[FLOW] Refresh gagal setelah no_checkbox: {e}")
                        continue
                    else:
                        print("[FLOW] Gagal menyelesaikan Turnstile")

                    await asyncio.sleep(10)
                else:
                    print("[WARN] gagal klik tombol walau active.")
                    await asyncio.sleep(2)

            except Exception as e:
                consecutive_errors += 1
                print(f"[ERROR] Loop error #{consecutive_errors}: {repr(e)}")
                is_crashed = await check_page_crashed(page)
                if is_crashed:
                    print("[ERROR] Page crashed terdeteksi, TAPI force reload saat crash DINONAKTIFKAN")
                if consecutive_errors >= 3:
                    print("[ERROR] Terlalu banyak error berturut-turut, force reload...")
                    try:
                        await send_event(f"Force reload: error beruntun {consecutive_errors}")
                        reload_success = False
                        for reload_retry in range(3):
                            try:
                                print(f"[ERROR] Force reload percobaan #{reload_retry + 1}")
                                await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)
                                await asyncio.sleep(3)
                                if not await check_page_crashed(page):
                                    last_reload = now()
                                    consecutive_errors = 0
                                    reload_success = True
                                    print("[ERROR] Force reload berhasil")
                                    break
                                else:
                                    print(f"[ERROR] Page masih crashed setelah reload #{reload_retry + 1}")
                                    await asyncio.sleep(5)
                            except Exception as reload_error:
                                print(f"[ERROR] Force reload #{reload_retry + 1} gagal: {reload_error}")
                                await asyncio.sleep(10)
                        if not reload_success:
                            print("[ERROR] Semua percobaan force reload gagal")
                    except Exception as reload_error:
                        print(f"[ERROR] Force reload gagal: {reload_error}")
                if telegram and consecutive_errors <= 3:
                    await telegram.send_error_notification(f"Bot error: {str(e)}")
                await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
