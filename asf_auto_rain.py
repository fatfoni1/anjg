# asf_auto_rain.py — Rain Bot (Monitor + Scrapless)
# Monitor (Chromium lokal) + Scrapless Join + Turnstile handling + retries
# + Anti-bot tweaks + Param: lightweight_profile, scrapeless_country_candidates, scrapeless_proxy_country
# Kompatibel dgn kode lama (jalankan_auto_rain, load_rain_accounts, save_rain_accounts).

import asyncio
import os
import random
import time
from typing import Awaitable, Callable, Dict, List, Optional

from playwright.async_api import Page, async_playwright

# ====================== CONFIG ======================
SCRAPELESS_HARD_TOKEN = "sk_oWFNX24s6BmkgldvtslLpFw5MaCSczD0kEpl8yDAiyb3ji0jE3zmWEm8XeFRZHjW"
SCRAPELESS_DEFAULT_COUNTRY = "NL"
SCRAPELESS_DEFAULT_TTL = 900
SCRAPELESS_SESSION_NAME = "FlipRain"

MONITOR_URL = "https://flip.gg/mines"

# Fingerprint dasar
VIEWPORT = {"width": 1366, "height": 768}
LOCALE = "id-ID"
TZ = "Asia/Jakarta"
DPR = 1

CHROME_CHANNEL_CANDIDATES = ["chrome", "chrome-dev"]
UA_CANDIDATES = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
]

MAX_RETRY = 3
RETRY_DELAY = 2

# ====================== LOGGING ======================
try:
    from asf_core import send_telegram  # type: ignore
except Exception:

    async def send_telegram(msg: str, context=None, disable_notif=False):
        print(msg)


def _tg(log_func, context):
    async def _send(msg: str):
        try:
            if log_func:
                r = log_func(msg, context)
                if asyncio.iscoroutine(r):
                    await r
            else:
                await send_telegram(msg, context)
        except Exception:
            print(msg)

    return _send


# ====================== HELPERS ======================
async def with_retry(
    fn: Callable[[], Awaitable],
    tg,
    name: str,
    max_attempts: int = MAX_RETRY,
    delay: int = RETRY_DELAY,
):
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as e:
            await tg(f"⚠️ {name} gagal attempt {attempt}/{max_attempts}: {e!r}")
            await asyncio.sleep(delay + random.uniform(0.1, 0.6))
    raise RuntimeError(f"{name} gagal setelah {max_attempts} attempt")


def _pick(list_like: List[str], default: Optional[str] = None) -> str:
    try:
        return random.choice(list_like)
    except Exception:
        return default or (list_like[0] if list_like else "")


def _normalize_bearer(token: str) -> str:
    t = (token or "").strip()
    if not t:
        return ""
    return t if t.lower().startswith("bearer ") else f"Bearer {t}"


async def _install_token(page: Page, token_raw: str):
    norm = _normalize_bearer(token_raw)
    if not norm:
        return
    await page.add_init_script(
        f"""
        (() => {{
            try {{
                const norm = {norm!r};
                localStorage.setItem('Authorization', norm);
                sessionStorage.setItem('Authorization', norm);
                document.cookie = "Authorization=" + norm + "; path=/; domain=.flip.gg; Secure; SameSite=None";
            }} catch(e) {{}}
        }})();
    """
    )


async def _verify_token(page: Page) -> bool:
    try:
        val = await page.evaluate(
            "() => localStorage.getItem('Authorization') || sessionStorage.getItem('Authorization')"
        )
        return bool(val and "Bearer" in val)
    except Exception:
        return False


async def _human_jitter(page: Page):
    try:
        await page.mouse.move(random.randint(10, 50), random.randint(10, 50))
        await asyncio.sleep(random.uniform(0.03, 0.12))
        await page.mouse.wheel(0, random.randint(120, 300))
        await asyncio.sleep(random.uniform(0.03, 0.12))
    except Exception:
        pass


async def _connect_local_chromium(headless: bool = False, ua: Optional[str] = None):
    pw = await async_playwright().start()

    last_err: Optional[Exception] = None

    # Coba channel Chrome yang umum secara berurutan: stable → dev
    for ch in CHROME_CHANNEL_CANDIDATES:
        try:
            browser = await pw.chromium.launch(channel=ch, headless=headless)
            ctx = await browser.new_context(
                viewport=VIEWPORT,
                device_scale_factor=DPR,
                locale=LOCALE,
                timezone_id=TZ,
                user_agent=ua or _pick(UA_CANDIDATES, UA_CANDIDATES[0]),
            )
            return browser, ctx, pw
        except Exception as e:
            last_err = e
            continue

    # Fallback: gunakan Chromium bawaan Playwright (tanpa channel)
    try:
        browser = await pw.chromium.launch(headless=headless)
        ctx = await browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=DPR,
            locale=LOCALE,
            timezone_id=TZ,
            user_agent=ua or _pick(UA_CANDIDATES, UA_CANDIDATES[0]),
        )
        return browser, ctx, pw
    except Exception as e:
        # Jika semua gagal, kembalikan error terakhir yang paling relevan
        raise last_err or e


async def _read_time_left(page: Page) -> Optional[int]:
    try:
        raw = await page.locator(
            "xpath=//span[normalize-space()='Time Left']/following-sibling::span[1]"
        ).inner_text()
        m, s = [int(x) for x in raw.strip().split(":", 1)]
        return m * 60 + s
    except Exception:
        return None


# ====================== SCRAPELESS HELPERS ======================
async def _scrapeless_ws(
    token: str,
    session_name: str,
    ttl: int = SCRAPELESS_DEFAULT_TTL,
    country: str = SCRAPELESS_DEFAULT_COUNTRY,
    proxy_country: Optional[str] = None,
) -> str:
    """
    Bangun endpoint WebSocket untuk koneksi Scrapeless.
    Sesuai dokumentasi resmi: https://docs.scrapeless.com/
    """
    base = "wss://browser.scrapeless.com/api/v2/browser"
    qs = (
        f"?token={token}&sessionTTL={int(ttl)}"
        f"&proxyCountry={proxy_country or country}"
    )
    return base + qs


async def _connect_scrapless(
    token: Optional[str] = None,
    session_name: Optional[str] = None,
    ttl: int = SCRAPELESS_DEFAULT_TTL,
    country: str = SCRAPELESS_DEFAULT_COUNTRY,
    proxy_country: Optional[str] = None,
    ua: Optional[str] = None,
):
    """
    Konek ke Scrapless via Playwright (remote). Jika gagal → fallback ke Chromium lokal headless.
    Return: (mode, browser, context, playwright)
    mode: 'scrapless' atau 'local'
    """
    token = token or SCRAPELESS_HARD_TOKEN
    session_name = session_name or SCRAPELESS_SESSION_NAME

    pw = None
    try:
        pw = await async_playwright().start()
        ws = await _scrapeless_ws(token, session_name, ttl=ttl, country=country, proxy_country=proxy_country)
        browser = await pw.chromium.connect(ws)
        ctx = await browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=DPR,
            locale=LOCALE,
            timezone_id=TZ,
            user_agent=ua or _pick(UA_CANDIDATES, UA_CANDIDATES[0]),
        )
        return "scrapless", browser, ctx, pw
    except Exception:
        # Tutup pw remote sebelum fallback
        try:
            if pw:
                await pw.__aexit__(None, None, None)
        except Exception:
            pass
        browser, ctx, pw2 = await _connect_local_chromium(headless=True, ua=ua)
        return "local", browser, ctx, pw2


async def _click_join(page: Page):
    """
    Klik tombol Join Rain. Coba beberapa selector umum dan abaikan overlay tip bila ada.
    """
    # Tutup overlay tip jika ada (best-effort)
    try:
        tip = page.locator("text=Tip Rain")
        if await tip.count() > 0:
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass
    except Exception:
        pass

    selectors = [
        ".tss-17nb17k-rainText.active",
        ".tss-1msi2sy-prizeBox.active",
        "button:has-text('Join')",
        "text=/\\bJoin\\b/i",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=2000)
            await loc.click(timeout=2000)
            return
        except Exception:
            continue
    raise Exception("Target Join tidak ditemukan atau klik gagal")


async def _wait_turnstile(page: Page, timeout_sec: int = 180):
    """
    Tunggu hingga Cloudflare Turnstile solved. Periksa window.turnstile.getResponse().
    Timeout default 180 detik.
    """
    deadline = time.monotonic() + max(1, int(timeout_sec))
    while time.monotonic() < deadline:
        try:
            resp = await page.evaluate(
                "() => (window && window.turnstile && window.turnstile.getResponse && window.turnstile.getResponse()) || ''"
            )
            if resp:
                return
        except Exception:
            pass
        await asyncio.sleep(1.5)
    raise Exception("Turnstile tidak terselesaikan dalam batas waktu")


# ====================== ACCOUNT I/O ======================
AKUN_RAIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "akunrain.txt")


def load_rain_accounts() -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    if not os.path.exists(AKUN_RAIN_FILE):
        return out
    with open(AKUN_RAIN_FILE, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            raw = (line or "").strip()
            if not raw or raw.startswith("#"):
                continue
            if "=" not in raw:
                continue
            name, token = raw.split("=", 1)
            if name and token:
                out.append({"name": name.strip(), "token": token.strip()})
    return out


def save_rain_accounts(accounts: List[Dict[str, str]]):
    try:
        with open(AKUN_RAIN_FILE, "w", encoding="utf-8") as f:
            for acc in accounts:
                if acc.get("name") and acc.get("token"):
                    f.write(f"{acc['name']}={acc['token']}\n")
    except Exception as e:
        print(f"⚠️ save_rain_accounts error: {e!r}")


# ====================== SCRAPELESS (join rain) ======================
async def jalankan_scrapless_join(
    context,
    log_func=None,
    stop_event=None,
    lightweight_profile=False,
    scrapeless_token=None,
    scrapeless_proxy_country=None,
    scrapeless_country_candidates=None,
    user_agent=None,
):
    """
    Scrapless Join flow:
    - Connect ke Scrapless (pakai token hardcoded by default), fallback ke lokal headless jika gagal.
    - Buka flip.gg/mines.
    - Loop tunggu tombol aktif (.tss-17nb17k-rainText.active / .tss-1msi2sy-prizeBox.active).
    - Jika aktif → klik Join (skip Tip Rain), lalu tunggu Turnstile solved (timeout 180s) dengan retry.
    - Semua step penting memakai with_retry (max_attempts=3 default).
    - Apapun hasilnya (sukses/timeout/error) → log lalu kembali ke monitor.
    """
    tg = _tg(log_func, context)

    # Pilih negara untuk scrapless
    country = (
        (scrapeless_proxy_country or "").strip()
        or (scrapeless_country_candidates[0] if scrapeless_country_candidates else "")
        or SCRAPELESS_DEFAULT_COUNTRY
    )

    mode = None
    browser = ctx = pw = page = None

    async def _do_connect():
        nonlocal mode, browser, ctx, pw
        mode, browser, ctx, pw = await _connect_scrapless(
            token=scrapeless_token or SCRAPELESS_HARD_TOKEN,
            session_name=SCRAPELESS_SESSION_NAME,
            ttl=SCRAPELESS_DEFAULT_TTL,
            country=country,
            proxy_country=scrapeless_proxy_country,
            ua=user_agent,
        )

    try:
        # Connect dengan retry
        await with_retry(_do_connect, tg, name="Scrapless connect")
        await tg(f"🔌 Scrapless mode: {mode}")

        # Buka halaman mines
        page = await ctx.new_page()
        await page.goto(MONITOR_URL, wait_until="domcontentloaded", timeout=120_000)

        # Loop tunggu tombol aktif (maks 120 detik) dengan reload periodik
        async def _wait_active():
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if stop_event and getattr(stop_event, "is_set", lambda: False)():
                    raise Exception("Dihentikan oleh pengguna")
                try:
                    cnt = await page.locator(
                        ".tss-17nb17k-rainText.active, .tss-1msi2sy-prizeBox.active"
                    ).count()
                    if cnt and cnt > 0:
                        return
                except Exception:
                    pass
                await asyncio.sleep(1.5)
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=60_000)
                except Exception:
                    pass
            raise Exception("Tombol Join tidak aktif dalam batas waktu")

        await with_retry(_wait_active, tg, name="Wait active join button")

        # Klik Join dengan retry
        await with_retry(lambda: _click_join(page), tg, name="Klik Join")
        await tg("✅ Join diklik, menunggu Turnstile diselesaikan…")

        # Tunggu Turnstile solved dengan retry (tiap attempt 180s)
        await with_retry(lambda: _wait_turnstile(page, timeout_sec=180), tg, name="Turnstile")
        await tg("🎉 Turnstile terselesaikan. Scrapless selesai.")

    except Exception as e:
        await tg(f"⚠️ Scrapless error: {e!r}")
    finally:
        # Tutup resource Scrapless/local headless
        for obj in [page, ctx, browser]:
            try:
                await obj.close()
            except Exception:
                pass
        try:
            if pw:
                await pw.__aexit__(None, None, None)
        except Exception:
            pass


# ====================== MONITOR ======================
async def jalankan_rain_monitor(
    context,
    log_func=None,
    stop_event=None,
    headless=False,
    monitor_user_agent=None,
    monitor_url=None,
    visible=None,
):
    tg = _tg(log_func, context)
    # Backward-compat: jika 'visible' diberikan, override headless
    try:
        if visible is not None:
            headless = not bool(visible)
    except Exception:
        pass
    browser = ctx = pw = page = None
    try:
        accounts = load_rain_accounts()
        if not accounts:
            await tg("⚠️ Tidak ada akun di akunrain.txt")
            return
        token_first = accounts[0]["token"]

        await tg(f"🖥️ Start Monitor (headless={headless}) pakai akun {accounts[0]['name']}")
        browser, ctx, pw = await _connect_local_chromium(headless=headless, ua=monitor_user_agent)
        page = await ctx.new_page()
        await _install_token(page, token_first)
        await page.goto(monitor_url or MONITOR_URL, wait_until="domcontentloaded", timeout=120_000)

        if not await _verify_token(page):
            await tg("❌ Token monitor gagal disuntik")
            return
        await tg("✅ Token monitor OK, mulai loop")

        while True:
            if stop_event and getattr(stop_event, "is_set", lambda: False)():
                break
            secs = await _read_time_left(page)
            if secs is None:
                await asyncio.sleep(1.0)
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=60_000)
                except:
                    pass
                continue

            m, s = divmod(secs, 60)
            await tg(f"⏱️ Time Left: {m:02d}:{s:02d}")
            if secs <= 180:
                await tg("⚡ Time ≤ 03:00 → buka Scrapless (selalu headless)")
                await jalankan_scrapless_join(context, log_func=log_func, stop_event=stop_event)
                await tg("↩️ Scrapless selesai, kembali monitor")
            await asyncio.sleep(2.0 + random.uniform(0.0, 0.6))
            try:
                await page.reload(wait_until="domcontentloaded", timeout=60_000)
            except:
                pass
    except Exception as e:
        await tg(f"💥 Monitor error: {e!r}")
    finally:
        for obj in [page, ctx, browser]:
            try:
                await obj.close()
            except:
                pass
        try:
            if pw:
                await pw.__aexit__(None, None, None)
        except:
            pass


# ====================== WRAPPER ======================
async def jalankan_auto_rain(context, *args, **kwargs):
    # Map visible → headless (monitor only)
    # Parameter 'visible' dari Telegram bot dikonversi ke 'headless' untuk monitor
    # Scrapeless selalu menggunakan headless mode
    headless = kwargs.get("headless", False)
    if "visible" in kwargs:
        vis = bool(kwargs.pop("visible"))
        headless = not vis  # visible=True → headless=False, visible=False → headless=True

    # Buang argumen lama yang tidak relevan untuk fungsi monitor
    for k in [
        "capsolver_api_key",
        "max_concurrency", 
        "keep_open_all",
        "akun_list",
        "scrapeless_token",
        "scrapeless_session_name_prefix",
        "live_screenshot_dir",
        "scrapeless_session_recording",
        "scrapeless_session_ttl",
        "visible",  # Parameter visible sudah dikonversi ke headless, hapus dari kwargs
    ]:
        kwargs.pop(k, None)

    # Panggil monitor dengan parameter yang sudah dibersihkan
    return await jalankan_rain_monitor(
        context,
        log_func=kwargs.get("log_func"),
        stop_event=kwargs.get("stop_event"),
        headless=headless,  # Parameter headless yang sudah dikonversi dari visible
        monitor_user_agent=kwargs.get("monitor_user_agent"),
        monitor_url=kwargs.get("monitor_url"),
    )


__all__ = [
    "jalankan_auto_rain",
    "jalankan_rain_monitor",
    "load_rain_accounts",
    "save_rain_accounts",
]