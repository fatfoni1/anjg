import asyncio
import random
import re
import time

from playwright.async_api import async_playwright

from asf_core import (
    get_balance,
    inject_and_validate_token_fast,
    load_accounts,
    send_telegram,
)

# =====================
# Konfigurasi kecil
# =====================
SINGLE_RELOAD_DELAY_S = 0.5  # jeda setelah reload saat reconnect


# Helper function untuk mengurangi duplikasi logging
async def send_message_and_log(message: str, context=None, log_func=None):
    """Helper function untuk mengirim telegram dan log sekaligus"""
    await send_telegram(message, context)
    if log_func:
        try:
            log_func(message)
        except Exception:
            pass


async def rp(min_s: float = 0.02, max_s: float = 0.05):
    await asyncio.sleep(random.uniform(min_s, max_s))


# =========================
# Reconnect guard (Wheel) - SINGLE RELOAD
# =========================
_WHEEL_DISC_PATTERNS = [
    r"you.?re.*disconnected",
    r"been\s+disconnected",
    r"please\s+refresh\s+the\s+page",
    r"reconnect",
    r"connection\s+lost",
    r"try\s+again",
]


async def _wheel_has_disconnect_banner(page) -> bool:
    """Cek indikasi disconnect via teks di body/overlay umum, plus navigator.onLine."""
    # 1) Cek teks di body
    try:
        body_txt = (await page.inner_text("body")) or ""
        low = body_txt.lower()
        for pat in _WHEEL_DISC_PATTERNS:
            if re.search(pat, low, re.I):
                return True
    except Exception:
        pass

    # 2) Cek toast/alert/banner yang umum
    try:
        overlay = page.locator(
            "div[class*='toast'], div[class*='alert'], div[class*='banner']"
        ).first
        if overlay:
            try:
                if await overlay.is_visible():
                    txt = ((await overlay.inner_text()) or "").lower()
                    for pat in _WHEEL_DISC_PATTERNS:
                        if re.search(pat, txt, re.I):
                            return True
            except Exception:
                pass
    except Exception:
        pass

    # 3) navigator.onLine
    try:
        online = await page.evaluate("navigator.onLine")
        if online is False:
            return True
    except Exception:
        pass

    return False


async def wheel_reconnect_guard(page, settle_ms: int = 15000) -> bool:
    """
    Panggil ini SEBELUM langkah kritikal.
    Jika terdeteksi disconnect ��� single reload → tunggu settle.
    Return True = aman lanjut, False = gagal recovery.
    Silent (tidak kirim log/telegram).
    """
    # Quick check
    try:
        if not await _wheel_has_disconnect_banner(page):
            return True
    except Exception:
        return True

    # ======== SINGLE RELOAD ========
    try:
        # reload cepat
        await page.reload(wait_until="domcontentloaded", timeout=12000)
    except Exception:
        # fallback reload default
        try:
            await page.reload()
        except Exception:
            return False  # gagal reload
    
    # beri kesempatan resource settle setelah reload
    try:
        await page.wait_for_load_state("networkidle", timeout=2000)
    except Exception:
        pass
    await asyncio.sleep(SINGLE_RELOAD_DELAY_S)  # delay singkat untuk stabilisasi
    # ======== END SINGLE RELOAD ========

    # Tunggu settle window
    end_t = time.monotonic() + (settle_ms / 1000.0)
    while time.monotonic() < end_t:
        try:
            if await _wheel_has_disconnect_banner(page):
                await asyncio.sleep(0.25)
                continue
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=2000)
        except Exception:
            pass
        try:
            if not await _wheel_has_disconnect_banner(page):
                return True
        except Exception:
            return True
        await asyncio.sleep(0.25)

    return False


# =========================
# Multiplier: baca ulang dari Telegram (kalau ada) + selector simpel
# =========================
def _parse_int_safe(val, default):
    try:
        if val is None:
            return default
        if isinstance(val, (int, float)):
            return int(val)
        s = str(val).strip().lower().replace("x", "").replace("×", "")
        return int(float(s))
    except Exception:
        return default


def get_current_multiplier(context, fallback: int) -> int:
    """
    Ambil multiplier terbaru dari Telegram context kalau ada.
    Prioritas key:
      - context.application.bot_data['wheel_multiplier']
      - context.application.bot_data['current_multiplier']
      - context.application.bot_data['multiplier']
    Kalau nggak ada, pakai fallback dari argumen fungsi.
    """
    keys = ("wheel_multiplier", "current_multiplier", "multiplier")
    try:
        if context and getattr(context, "application", None):
            data = context.application.bot_data or {}
            for k in keys:
                if k in data:
                    return _parse_int_safe(data.get(k), fallback)
    except Exception:
        pass
    return fallback


async def select_multiplier_simple(page, mult: int, attempts: int = 10) -> bool:
    """
    Pilih multiplier dengan selector sederhana & toleran varian text:
    Coba urutan:
      1) button:has-text('{mult}X')   (umum)
      2) button:has-text('x{mult}')   (prefix)
      3) role=button name regex untuk '50x'/'x50'/'50×'/'×50'
    Tanpa hard fail; kalau gagal, return False (bot lanjut tanpa spam error).
    """
    # pola teks yang sering muncul
    candidates_css = [
        f"button:has-text('{mult}X')",
        f"button:has-text('x{mult}')",
        f"button:has-text('{mult}×')",
        f"button:has-text('×{mult}')",
    ]

    patt = re.compile(rf"^\s*(?:[\u00D7xX]?\s*{mult}|{mult}\s*[\u00D7xX])\s*$", re.I)

    for _ in range(attempts):
        # 1) CSS langsung
        for sel in candidates_css:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    dis = await btn.get_attribute("disabled")
                    if dis is None:
                        await btn.click()
                        return True
            except Exception:
                pass

        # 2) ARIA role fallback
        try:
            btn2 = page.get_by_role("button", name=patt).first
            # periksa eksistensi dengan mencoba ambil attribute (menghindari .count() yang kadang error)
            try:
                _ = await btn2.get_attribute("class")
                dis = await btn2.get_attribute("disabled")
                if dis is None:
                    await btn2.click()
                    return True
            except Exception:
                pass
        except Exception:
            pass

        await asyncio.sleep(0.08)

    return False


# =========================
# Main Wheel Auto Bet
# =========================
async def jalankan_auto_bet(
    multiplier: int,
    bet_amount: str,
    allin: bool,
    headless: bool,
    context=None,
    akun_list=None,
    stop_event=None,
    log_func=None,
):
    akun = akun_list if akun_list is not None else load_accounts()
    if not akun:
        await send_telegram("❌ Tidak ada akun tersedia.", context)
        return

    total_akun = len(akun)

    # Header pembuka
    mode_text = "All-in" if allin else f"Manual ({bet_amount})"
    start_msg = "━━━━━━━━━━━━━━━━━━━━━━━━\n"
    start_msg += "🎰 <b>WHEEL AUTO BET DIMULAI!</b> 🎰\n"
    start_msg += f"🎯 Multiplier: <b>{multiplier}X</b>\n"
    start_msg += f"💰 Mode Bet: <b>{mode_text}</b>\n"
    start_msg += f"👥 Total Akun: <b>{total_akun}</b>\n"
    start_msg += "━━━━━━━━━━━━━━━━━━━━━━━━"
    await send_telegram(start_msg, context)
    if log_func:
        try:
            log_func(start_msg)
        except Exception:
            pass

    def diminta_stop() -> bool:
        """Cek apakah user meminta stop dari Telegram bot"""
        if stop_event and getattr(stop_event, "is_set", None) and stop_event.is_set():
            return True
        if context and getattr(context, "application", None):
            try:
                return bool(context.application.bot_data.get("stop_bet", False))
            except Exception:
                return False
        return False

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        try:
            for idx, acc in enumerate(akun, start=1):
                if diminta_stop():
                    await send_telegram("🛑 Auto bet dihentikan.", context)
                    break

                # Log simple per akun
                simple_log = f"[{idx}/{total_akun}] {acc['name']}"

                # Context/page baru per akun
                ctx = await browser.new_context()
                page = await ctx.new_page()

                try:
                    # 1) Ambil saldo dulu
                    saldo = get_balance(acc["token"])
                    if saldo is None:
                        await send_message_and_log(
                            f"[{acc['name']}] ❌ Gagal mendapatkan info saldo", context, log_func
                        )
                        continue

                    emoji_list = ["🎰", "🎲", "🎯", "🎪"]
                    selected_emoji = emoji_list[idx % len(emoji_list)]
                    loading_msg = f"{simple_log}  |  {selected_emoji} {saldo:.4f}  |  ⏳ Loading..."
                    await send_message_and_log(loading_msg, context, log_func)

                    if saldo < 0.1:
                        await send_message_and_log(
                            f"⚠️ Saldo tidak mencukupi. Saldo: {saldo:.4f}, Minimal: 0.10",
                            context,
                            log_func,
                        )
                        continue

                    # 2) Validasi token cepat
                    token_valid, reason = await inject_and_validate_token_fast(
                        page, acc["token"], acc["name"]
                    )
                    if not token_valid:
                        await send_message_and_log(f"⛔ {reason}", context, log_func)
                        continue

                    await rp(0.05, 0.1)

                    # 3) Buka wheel dan pastikan elemen kunci ada (retry 3x)
                    input_selector = "input.MuiInputBase-inputAdornedStart"
                    ready = False
                    for attempt in range(3):
                        if diminta_stop():
                            await send_telegram("🛑 Auto bet dihentikan.", context)
                            return

                        await page.goto("https://flip.gg/wheel", timeout=20000)
                        try:
                            await page.wait_for_load_state("domcontentloaded", timeout=15000)
                        except Exception:
                            pass

                        # Reconnect guard (silent) -> ini akan melakukan single reload kalau perlu
                        ok = await wheel_reconnect_guard(page)
                        if not ok:
                            await rp(0.4, 0.9)
                            continue

                        if diminta_stop():
                            await send_telegram("🛑 Auto bet dihentikan.", context)
                            return

                        try:
                            await page.wait_for_selector(input_selector, timeout=8000)
                            ready = True
                            break
                        except Exception:
                            if diminta_stop():
                                await send_telegram("🛑 Auto bet dihentikan.", context)
                                return
                            await rp(0.6, 1.2)
                            # reload biasa (single), bukan reconnect
                            try:
                                await page.reload()
                            except Exception:
                                pass
                            await rp(0.4, 0.9)

                    if diminta_stop():
                        await send_telegram("🛑 Auto bet dihentikan.", context)
                        return

                    if not ready:
                        await send_message_and_log(
                            "⛔ Halaman wheel belum siap / token tidak aktif", context, log_func
                        )
                        continue

                    # 4) Tentukan jumlah bet
                    if allin:
                        jumlah_bet = round(saldo - 0.01, 2)
                        mode = "All-in"
                    else:
                        jumlah_bet = float(bet_amount)
                        mode = "Manual"

                    # Helper function untuk memastikan bet amount dan multiplier terisi
                    async def ensure_bet_setup(page, amount, mult):
                        """Pastikan bet amount dan multiplier terisi dengan benar"""
                        # Isi amount
                        try:
                            input_elem = await page.query_selector(input_selector)
                            if input_elem:
                                current_value = await input_elem.input_value()
                                if not current_value or float(current_value or "0") != amount:
                                    await page.locator(input_selector).fill("")
                                    await rp(0.02, 0.05)
                                    await page.locator(input_selector).fill(str(amount))
                                    await rp(0.02, 0.05)
                        except Exception:
                            # Fallback: isi ulang
                            await page.locator(input_selector).fill(str(amount))
                            await rp(0.02, 0.05)
                        
                        # Pilih multiplier
                        await select_multiplier_simple(page, mult)
                        await rp(0.02, 0.05)

                    # Isi amount dan pilih multiplier pertama kali
                    await ensure_bet_setup(page, jumlah_bet, multiplier)
                    await send_telegram(f"[{acc['name']}] ✍️ {mode} bet: {jumlah_bet}", context)

                    # 5) Pilih multiplier: BACA ULANG dari Telegram setelah recover
                    if not await wheel_reconnect_guard(page):
                        continue
                    # Setelah reconnect, pastikan bet setup ulang
                    cur_mult = get_current_multiplier(context, multiplier)
                    await ensure_bet_setup(page, jumlah_bet, cur_mult)

                    # 6) Klik BET — sebelum klik, re-baca multiplier dan pastikan setup
                    if not await wheel_reconnect_guard(page):
                        continue
                    cur_mult = get_current_multiplier(context, cur_mult)
                    await ensure_bet_setup(page, jumlah_bet, cur_mult)

                    bet_selector = "button:has-text('BET')"
                    bet_clicked = False

                    # Coba klik BET cepat
                    for _ in range(20):
                        if diminta_stop():
                            await send_telegram("🛑 Auto bet dihentikan.", context)
                            return

                        if not await wheel_reconnect_guard(page):
                            await asyncio.sleep(0.05)
                            continue

                        # Pastikan bet setup ulang setelah reconnect
                        await ensure_bet_setup(page, jumlah_bet, cur_mult)

                        try:
                            await page.click(bet_selector, timeout=100)
                            await rp(0.02, 0.05)
                            bet_clicked = True
                            await send_telegram(
                                f"[{acc['name']}] 🌀 Klik BET (x{cur_mult}).", context
                            )
                            break
                        except Exception:
                            await asyncio.sleep(0.05)

                    # Polling hingga aktif lalu klik instan
                    if not bet_clicked:
                        for _ in range(300):
                            if diminta_stop():
                                await send_telegram("🛑 Auto bet dihentikan.", context)
                                return

                            if not await wheel_reconnect_guard(page):
                                await asyncio.sleep(0.05)
                                continue

                            # Pastikan bet setup ulang setelah reconnect
                            await ensure_bet_setup(page, jumlah_bet, cur_mult)

                            try:
                                await page.click(bet_selector, timeout=200)
                                await rp(0.02, 0.05)
                                bet_clicked = True
                                await send_telegram(
                                    f"[{acc['name']}] 🌀 Klik BET (x{cur_mult}).", context
                                )
                                break
                            except Exception:
                                pass
                            await asyncio.sleep(0.2)

                    if not bet_clicked:
                        await send_telegram(
                            f"[{acc['name']}] ⚠️ Gagal klik BET (tidak aktif 60 detik), lanjut akun berikutnya.",
                            context,
                        )
                        continue

                    # 7) Tunggu indikasi rolling
                    roll_countdown_selector = "div.tss-mffl47-countdown, div[class*='countdown']"
                    rolling_detected = False
                    for _ in range(75):  # ~15 detik @0.2s
                        if diminta_stop():
                            await send_telegram("🛑 Auto bet dihentikan.", context)
                            return

                        if not await wheel_reconnect_guard(page):
                            await asyncio.sleep(0.2)
                            continue

                        try:
                            elem = await page.query_selector(roll_countdown_selector)
                            if elem:
                                txt = (await elem.inner_text() or "").strip().upper()
                                if "ROLLING" in txt:
                                    rolling_detected = True
                                    break
                        except Exception:
                            pass

                        await asyncio.sleep(0.2)

                    if rolling_detected:
                        await send_telegram(
                            f"[{acc['name']}] 🔄 Rolling dimulai, lanjut akun berikutnya.", context
                        )
                        continue
                    else:
                        await send_telegram(
                            f"[{acc['name']}] ⚠️ Tidak terdeteksi rolling dalam 15 detik, lanjut akun berikutnya.",
                            context,
                        )
                        continue

                except Exception as e:
                    await send_telegram(
                        f"[{acc['name']}] ❌ Error: {e}. Lanjut akun berikutnya.", context
                    )
                finally:
                    try:
                        await ctx.close()
                    except Exception:
                        pass
        finally:
            await browser.close()