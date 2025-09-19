import asyncio, time, json, re, os
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

# ================== LOAD CONFIG ==================
def load_config():
    try:
        with open('bot_config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[CONFIG] Error loading config: {e}")
        return {}

config = load_config()
FAST_EXECUTE = bool(config.get("fast_execute", False))

# ================== KONFIG ==================
CDP_URL = config.get("cdp_url", "http://127.0.0.1:9222")
TARGET_URL = config.get("target_url", "https://flip.gg/profile")
CHECK_INTERVAL_SEC = config.get("check_interval_sec", 5)
RELOAD_EVERY_SEC = config.get("reload_every_sec", 300)
TURNSTILE_WAIT_MS = config.get("turnstile_wait_ms", 600000)
AUTO_SOLVE_CAPTCHA = config.get("auto_solve_captcha", True)

# Initialize handlers (opsional)
capsolver = None
telegram = None

if config.get("capsolver_token") and config.get("capsolver_token") != "MASUKKAN_API_KEY_CAPSOLVER_DISINI":
    try:
        capsolver = CapsolverHandler(config.get("capsolver_token"))
        print("[INIT] Capsolver handler initialized")
    except Exception as e:
        print(f"[INIT] Capsolver init error: {e}")

if config.get("telegram_token") and config.get("chat_id"):
    try:
        telegram = TelegramNotifier(config.get("telegram_token"), config.get("chat_id"))
        print("[INIT] Telegram notifier initialized")
    except Exception as e:
        print(f"[INIT] Telegram init error: {e}")
else:
    print("[INIT] Telegram tidak dikonfigurasi (token/chat_id kosong)")

# Util telegram
aSYNC_LOG_EMOJI = {
    "INFO": "ℹ️",
    "SUCCESS": "✅",
    "WARNING": "⚠️",
    "ERROR": "❌",
    "DEBUG": "🔍",
}

async def send_event(message: str):
    if telegram:
        try:
            await telegram.send_message(f"🔔 {message}")
        except Exception as e:
            print(f"[TELEGRAM] Error sending event: {e}")

async def send_telegram_log(message: str, level: str = "INFO"):
    if telegram:
        try:
            emoji = aSYNC_LOG_EMOJI.get(level, "📝")
            await telegram.send_message(f"{emoji} <b>[{level}]</b> {message}")
        except Exception as e:
            print(f"[TELEGRAM] Error sending log: {e}")
    else:
        print(f"[{level}] {message}")

# === Selector kunci ===
BTN_ACTIVE = 'button.tss-pqm623-content.active'
PRIZEBOX_ACTIVE = 'button:has(.tss-1msi2sy-prizeBox.active)'
JOIN_TEXT_ACTIVE = 'span.tss-7bx55w-rainStartedText.active'

# Turnstile selectors
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

# Success/notification selectors (ketat)
SUCCESS_SELECTORS_SPECIFIC = [
    '.success-notification', '.rain-success', '.join-success', '.entry-success',
    '.notification.success', '.alert-success', '.toast-success',
    '.notification.success:has-text("joined")', '.notification.success:has-text("entered")',
    '.toast.success:has-text("joined")', '.toast.success:has-text("entered")',
    '.alert.success:has-text("joined")', '.alert.success:has-text("entered")',
    '.Toastify__toast--success', '.Toastify__toast', '.toast', '#toast-container .toast-success',
    'div[role="alert"], div[role="status"]',
    '.ant-notification-notice-success', '.ant-message-success', '.notyf__toast--success'
]
SUCCESS_KEYWORDS_SPECIFIC = [
    "successfully joined", "joined successfully", "successfully entered", "entered successfully",
    "rain joined", "joined the rain", "entered the rain", "participation confirmed", "entry confirmed",
    "success", "successfully", "you have entered", "you have joined", "you joined", "you entered",
    "entry successful", "congratulations", "congrats", "claimed", "reward added", "you are in",
    "welcome to rain", "entered", "joined"
]

ALREADY_JOINED_SELECTORS = [
    'div:has-text("already")', 'span:has-text("already")',
    'div:has-text("Already")', 'span:has-text("Already")',
    '.already-joined', '[class*="already"]',
    'div:has-text("sudah join")', 'div:has-text("sudah bergabung")',
    '.notification:has-text("already")', '.toast:has-text("already")'
]

# Helper penyimpanan hasil cepat
def _save_fast_result(status: str):
    try:
        path = os.path.join(os.path.dirname(__file__), 'fast_exec_result.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({"status": status, "ts": time.time()}, f)
    except Exception as e:
        print(f"[FAST-RESULT] Gagal simpan hasil: {e}")

# ========= Helper util =========
async def check_already_joined(page) -> bool:
    try:
        # main page
        for selector in ALREADY_JOINED_SELECTORS:
            if await page.locator(selector).count() > 0:
                el = page.locator(selector).first
                if await el.is_visible():
                    await send_telegram_log("Already joined terdeteksi", "INFO")
                    return True
        # frames
        for frame in page.frames:
            try:
                for selector in ALREADY_JOINED_SELECTORS:
                    if await frame.locator(selector).count() > 0:
                        el = frame.locator(selector).first
                        if await el.is_visible():
                            await send_telegram_log("Already joined terdeteksi di frame", "INFO")
                            return True
            except Exception:
                continue
        # keywords (fallback)
        for kw in ["already", "sudah", "duplicate", "participated", "entered before"]:
            if await page.locator(f'text=/{kw}/i').count() > 0:
                await send_telegram_log(f"Keyword already ditemukan: {kw}", "INFO")
                return True
            for frame in page.frames:
                try:
                    if await frame.locator(f'text=/{kw}/i').count() > 0:
                        await send_telegram_log(f"Keyword already ditemukan di frame: {kw}", "INFO")
                        return True
                except Exception:
                    continue
    except Exception as e:
        await send_telegram_log(f"Error cek already joined: {e}", "ERROR")
    return False

async def detect_success_notification_quick(page) -> bool:
    try:
        # keywords spesifik di main page
        for kw in SUCCESS_KEYWORDS_SPECIFIC:
            try:
                if await page.locator(f'text=/{re.escape(kw)}/i').count() > 0:
                    el = page.locator(f'text=/{re.escape(kw)}/i').first
                    if await el.is_visible():
                        return True
            except Exception:
                continue
        # keywords di frame flip.gg (exclude cf/turnstile)
        for frame in page.frames:
            try:
                fu = (frame.url or '').lower()
                if 'flip.gg' not in fu or any(x in fu for x in ['cloudflare', 'turnstile', 'challenges.cloudflare.com']):
                    continue
                for kw in SUCCESS_KEYWORDS_SPECIFIC:
                    try:
                        if await frame.locator(f'text=/{re.escape(kw)}/i').count() > 0:
                            el = frame.locator(f'text=/{re.escape(kw)}/i').first
                            if await el.is_visible():
                                return True
                    except Exception:
                        continue
            except Exception:
                continue
        # selectors spesifik di main page
        for sel in SUCCESS_SELECTORS_SPECIFIC:
            try:
                if await page.locator(sel).count() > 0:
                    el = page.locator(sel).first
                    if await el.is_visible():
                        txt = (await el.text_content() or '').lower()
                        if any(w in txt for w in ['joined', 'entered', 'success', 'confirmed']):
                            return True
            except Exception:
                continue
        # selectors spesifik di frame flip.gg
        for frame in page.frames:
            try:
                fu = (frame.url or '').lower()
                if 'flip.gg' not in fu or any(x in fu for x in ['cloudflare', 'turnstile', 'challenges.cloudflare.com']):
                    continue
                for sel in SUCCESS_SELECTORS_SPECIFIC:
                    try:
                        if await frame.locator(sel).count() > 0:
                            el = frame.locator(sel).first
                            if await el.is_visible():
                                txt = (await el.text_content() or '').lower()
                                if any(w in txt for w in ['joined', 'entered', 'success', 'confirmed']):
                                    return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False
    except Exception as e:
        print(f"[SUCCESS_QUICK] Error: {e}")
        return False

async def detect_active(page):
    # Pastikan sudah di flip.gg dan bukan blank/crash
    u = page.url or ''
    if not u or u.startswith('about:blank') or 'chrome-error://' in u or 'flip.gg' not in u:
        return None
    try:
        if await page.locator(PRIZEBOX_ACTIVE).count() > 0:
            await page.locator(PRIZEBOX_ACTIVE).first.wait_for(state="visible", timeout=1500)
            return PRIZEBOX_ACTIVE
    except Exception:
        pass
    try:
        if await page.locator(BTN_ACTIVE).count() > 0:
            await page.locator(BTN_ACTIVE).first.wait_for(state="visible", timeout=1500)
            return BTN_ACTIVE
    except Exception:
        pass
    try:
        loc = page.locator(f'button:has({JOIN_TEXT_ACTIVE})').first
        if await loc.count() > 0:
            await loc.wait_for(state="visible", timeout=1500)
            return f'button:has({JOIN_TEXT_ACTIVE})'
    except Exception:
        pass
    return None

async def click_join(page, btn_selector) -> bool:
    try:
        btn = page.locator(btn_selector).first
        await btn.scroll_into_view_if_needed()
        await btn.click()
        await send_telegram_log(f"Berhasil klik tombol Rain: {btn_selector}", "SUCCESS")
        return True
    except Exception as e:
        await send_telegram_log(f"Gagal klik tombol Rain: {e}", "ERROR")
        return False

async def auto_click_checkbox_if_found(page) -> bool:
    # Cek di iframe Turnstile terlebih dahulu
    try:
        if await page.locator(IFRAME_TURNSTILE).count() > 0:
            fl = page.frame_locator(IFRAME_TURNSTILE)
            # Beragam selector umum (diperkuat untuk Cloudflare Turnstile)
            sels = [
                'input[type="checkbox"]',
                '[role="checkbox"]',
                'div[role="checkbox"]',
                'button[role="checkbox"]',
                'label:has([type="checkbox"])',
                '.cf-turnstile',
                '.cf-challenge',
                'div[class*="cf-challenge"]',
                'div:has-text("Verify")',
                'div:has-text("verify")',
                'div:has-text("I am human")',
                'div:has-text("I am not a robot")',
                'span:has-text("Verify")',
                'button:has-text("Verify")',
                'button:has-text("I am human")',
                'button:has-text("I am not a robot")',
                'div[tabindex][role="button"]',
                'div[aria-checked="false"]',
                '[data-testid*="turnstile"]',
                '.cb-lb input[type="checkbox"]',
                'label.cb-lb',
                'label.cb-lb input'
            ]
            for s in sels:
                try:
                    if await fl.locator(s).count() > 0:
                        el = fl.locator(s).first
                        # pastikan visible
                        try:
                            vis = await el.is_visible()
                        except Exception:
                            vis = True
                        if vis:
                            await el.click(force=True, timeout=2000)
                            return True
                except Exception:
                    continue
    except Exception:
        pass
    # (Dinonaktifkan) Tidak klik checkbox di main page; Turnstile hanya berada di dalam iframe
    # (Dinonaktifkan) Tidak klik checkbox di frame selain iframe Cloudflare Turnstile
    # Fallback: klik tengah iframe (jika ada)
    try:
        count_ifr = await page.locator(IFRAME_TURNSTILE).count()
        if count_ifr and count_ifr > 0:
            # Coba klik tengah setiap iframe Turnstile yang terdeteksi (lebih robust)
            for idx in range(min(count_ifr, 5)):
                try:
                    iframe_el = page.locator(IFRAME_TURNSTILE).nth(idx)
                    box = await iframe_el.bounding_box()
                    if box and box["width"] > 5 and box["height"] > 5:
                        x = box["x"] + box["width"] / 2
                        y = box["y"] + box["height"] / 2
                        await page.mouse.click(x, y, delay=30)
                        return True
                except Exception:
                    continue
    except Exception:
        pass
    return False

async def continuous_24h_scanner(page):
    # Scanner non-fast: jalan terus, scan sukses/already dan klik checkbox bila muncul.
    await send_telegram_log("Scanner 24 jam dimulai", "INFO")
    try:
        while True:
            try:
                if await detect_success_notification_quick(page):
                    await send_telegram_log("SUKSES terdeteksi - stop scanner 24 jam", "SUCCESS")
                    return "success"
                if await check_already_joined(page):
                    await send_telegram_log("ALREADY JOINED terdeteksi - stop scanner 24 jam", "INFO")
                    return "already"
                # klik checkbox jika ada
                await auto_click_checkbox_if_found(page)
                await asyncio.sleep(0.2)
            except Exception as e:
                print(f"[24H_SCANNER] Error: {e}")
                await asyncio.sleep(0.2)
    except asyncio.CancelledError:
        raise

# ========= Eksekusi utama =========
async def simple_rain_execution(page) -> bool:
    # FAST MODE: mengikuti alur yang diminta
    # watcher deteksi active -> GoLogin refresh sekali -> klik Rain -> klik checkbox jika ada -> diam (no refresh, no scan 24 jam)
    if FAST_EXECUTE:
        try:
            cur = page.url or ""
            if not cur or 'flip.gg' not in cur:
                await page.goto(TARGET_URL, wait_until='domcontentloaded', timeout=15000)
            # Refresh sekali sesuai alur
            try:
                await page.reload(wait_until='domcontentloaded', timeout=15000)
            except Exception:
                pass
            await asyncio.sleep(1)
        except Exception as e:
            print(f"[FAST] Prep error: {e}")

        # Pre-scan: cek notifikasi sukses segera dan kirim jika ada
        try:
            if await detect_success_notification_quick(page):
                await send_telegram_log("🎉 Sukses terdeteksi (pre-scan fast mode) - langsung kirim", "SUCCESS")
        except Exception:
            pass

        # Klik Rain
        print("[FAST] Cari dan klik tombol Rain…")
        rain_clicked = False
        sel = await detect_active(page)
        if sel:
            rain_clicked = await click_join(page, sel)
        if not rain_clicked:
            for fs in [
                "button:has-text('Join now')",
                "button:has-text('Join')",
                "button:has-text('Rain')",
                "button:has-text('Enter')",
            ]:
                try:
                    if await page.locator(fs).count() > 0:
                        try:
                            await page.locator(fs).first.click()
                            rain_clicked = True
                            print(f"[FAST] Klik fallback tombol: {fs}")
                            break
                        except Exception:
                            continue
                except Exception:
                    continue
        if not rain_clicked:
            await send_telegram_log("Tombol Rain tidak ditemukan (fast mode)", "ERROR")
            return False

        # Klik checkbox KAPANPUN MUNCUL (berulang): klik setiap muncul, berhenti jika sukses/already atau timeout
        try:
            end_time = time.time() + 180  # 3 menit window aman untuk antisipasi kemunculan lambat
            has_logged = False
            while time.time() < end_time:
                # Berhenti lebih cepat jika sukses/already terdeteksi
                try:
                    if await detect_success_notification_quick(page):
                        await send_telegram_log("🎉 Sukses terdeteksi (fast mode) - hentikan loop checkbox", "SUCCESS")
                        break
                    if await check_already_joined(page):
                        await send_telegram_log("ℹ️ Already joined terdeteksi (fast mode) - hentikan loop checkbox", "INFO")
                        break
                except Exception:
                    pass

                try:
                    found = await auto_click_checkbox_if_found(page)
                    if found and not has_logged:
                        await send_telegram_log("✅ Checkbox Turnstile diklik (fast mode, berulang)", "SUCCESS")
                        has_logged = True
                    if found:
                        # beri jeda singkat agar UI memperbarui status
                        await asyncio.sleep(0.5)
                        continue
                except Exception:
                    pass

                await asyncio.sleep(0.2)
        except Exception as e:
            print(f"[SIMPLE-FAST] Error pada loop auto-klik checkbox: {e}")

        # Post-scan: cek cepat notifikasi sukses sekali lagi dan kirim jika ada
        try:
            if await detect_success_notification_quick(page):
                await send_telegram_log("🎉 Sukses terdeteksi (post-scan fast mode) - langsung kirim", "SUCCESS")
        except Exception:
            pass

        # Simpan hasil untuk cooldown 3 menit di watcher, lalu selesai (diam)
        _save_fast_result('success')
        await send_telegram_log("Fast mode selesai. Tidak ada scan lanjutan.", "INFO")
        return True

    # NON-FAST: refresh sekali → klik rain → jalankan scanner 24 jam sampai success/already
    try:
        cur = page.url or ""
        if not cur or 'flip.gg' not in cur:
            await page.goto(TARGET_URL, wait_until='domcontentloaded', timeout=15000)
        else:
            await page.reload(wait_until='domcontentloaded', timeout=15000)
        await page.wait_for_load_state('domcontentloaded', timeout=10000)
        await asyncio.sleep(2)
    except Exception as e:
        print(f"[SIMPLE] Error refresh: {e}")

    print("[SIMPLE] Cari dan klik tombol Rain…")
    rain_clicked = False
    sel = await detect_active(page)
    if sel:
        rain_clicked = await click_join(page, sel)
    if not rain_clicked:
        for fs in [
            "button:has-text('Join now')",
            "button:has-text('Join')",
            "button:has-text('Rain')",
            "button:has-text('Enter')",
        ]:
            try:
                if await page.locator(fs).count() > 0:
                    try:
                        await page.locator(fs).first.click()
                        rain_clicked = True
                        print(f"[SIMPLE] Klik fallback tombol: {fs}")
                        break
                    except Exception:
                        continue
            except Exception:
                continue
    if not rain_clicked:
        await send_telegram_log("Tombol Rain tidak ditemukan", "ERROR")
        return False

    await send_telegram_log("Rain diklik - mulai scanner 24 jam", "SUCCESS")
    result = await continuous_24h_scanner(page)
    if result in ("success", "already"):
        _save_fast_result('success')
        return True
    return False

# ========= MAIN (CDP) =========
async def main():
    async with async_playwright() as p:
        await send_telegram_log(f"Menghubungkan ke CDP: {CDP_URL}", "INFO")
        try:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
            await send_telegram_log("Koneksi CDP berhasil", "SUCCESS")
        except Exception as e:
            await send_telegram_log(f"Gagal konek CDP: {e}", "ERROR")
            return

        # Ambil context & page aktif tanpa membuat tab baru
        try:
            contexts = browser.contexts
            context = contexts[0] if contexts else None
            page = None
            if context:
                pages = context.pages
                page = pages[0] if pages else None
            if not page:
                # Jika tidak ada page, buat satu dan buka TARGET_URL
                context = context or (await browser.new_context())
                page = await context.new_page()
                try:
                    await page.goto(TARGET_URL, wait_until='domcontentloaded', timeout=20000)
                except Exception:
                    pass
        except Exception as e:
            await send_telegram_log(f"Gagal mengambil page dari CDP: {e}", "ERROR")
            return

        try:
            ok = await simple_rain_execution(page)
            print(f"[MAIN] Selesai, status: {ok}")
        except Exception as e:
            await send_telegram_log(f"Error eksekusi: {e}", "ERROR")

if __name__ == "__main__":
    asyncio.run(main())
