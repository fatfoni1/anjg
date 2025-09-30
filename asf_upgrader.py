import asyncio
import random
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Browser, Page, async_playwright

from asf_core import (
    get_balance,
    get_profile,
    inject_and_validate_token_fast,
    load_accounts,
    send_telegram,
)
from feature_flags import is_enabled


# ---------- Helpers (JANGAN UBAH LOG/TEKS) ----------
async def send_message_and_log(
    message: str, context: Optional[Any] = None, log_func: Optional[Any] = None
):
    await send_telegram(message, context)
    if log_func:
        try:
            log_func(message)
        except Exception:
            pass


async def rp(min_s: float = 0.5, max_s: float = 1.0, headless: bool = False):
    if headless:
        min_s = min_s * 0.6
        max_s = max_s * 0.6
    await asyncio.sleep(random.uniform(min_s, max_s))


# ---------- Token inject ----------
async def robust_token_injection(
    page: Page, token: str, account_name: str, max_retries: int = 3, stop_check=None
) -> tuple[bool, str]:
    for attempt in range(max_retries):
        # Cek stop di awal setiap attempt injection
        if stop_check and stop_check():
            return False, "Proses dihentikan"
            
        try:
            base_timeout = 8000 + (attempt * 2000)
            page.set_default_timeout(base_timeout)
            ok, reason = await inject_and_validate_token_fast(page, token, account_name)
            if ok:
                return True, f"Token berhasil disuntik pada attempt {attempt + 1}"
            if attempt < max_retries - 1:
                # Cek stop sebelum sleep
                if stop_check and stop_check():
                    return False, "Proses dihentikan"
                await asyncio.sleep(1.0 + (attempt * 0.5))
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=base_timeout)
                    await asyncio.sleep(0.5)
                except Exception:
                    try:
                        await page.goto(
                            "https://flip.gg/upgrader",
                            wait_until="domcontentloaded",
                            timeout=base_timeout,
                        )
                        await asyncio.sleep(1.0)
                    except Exception:
                        pass
            else:
                return False, f"Token injection gagal setelah {max_retries} attempts: {reason}"
        except Exception as e:
            if attempt < max_retries - 1:
                # Cek stop sebelum sleep
                if stop_check and stop_check():
                    return False, "Proses dihentikan"
                await asyncio.sleep(1.0 + (attempt * 0.5))
                continue
            else:
                return False, f"Token injection error setelah {max_retries} attempts: {str(e)[:50]}"
    return False, f"Token injection gagal setelah {max_retries} attempts"


# ---------- DETEKSI & RECOVERY DISCONNECT ----------
_DISCONNECT_PATTERNS = [
    r"you.?re.*disconnected",
    r"been\s+disconnected",
    r"please\s+refresh\s+the\s+page",
    r"reconnect",
    r"connection\s+lost",
    r"try\s+again",
]


async def _page_has_disconnect_banner(page: Page) -> bool:
    for pat in _DISCONNECT_PATTERNS:
        try:
            loc = page.locator(f"text=/{pat}/i").first
            if await loc.is_visible():
                return True
        except Exception:
            pass
    try:
        overlay = page.locator(
            "div[class*='toast'], div[class*='alert'], div[class*='banner']"
        ).first
        if overlay and await overlay.is_visible():
            txt = (await overlay.inner_text() or "").lower()
            if any(re.search(p, txt, re.I) for p in _DISCONNECT_PATTERNS):
                return True
    except Exception:
        pass
    try:
        online = await page.evaluate("navigator.onLine")
        if online is False:
            return True
    except Exception:
        pass
    return False


# ---------- CEK "CONNECT" ----------
_CONNECT_SELECTORS = [
    "button:has-text('Connect')",
    "button:has-text('connect')",
    "text=/\\bConnect\\b/i",
    "div[role='dialog'] button:has-text('Connect')",
    "[class*='connect'] :text('Connect')",
]


async def _wait_upgrader_interactive(page: Page, total_timeout_ms: int = 25000) -> Tuple[bool, str]:
    deadline = time.monotonic() + (total_timeout_ms / 1000.0)
    search_selectors = [
        "input[placeholder='Search...']",
        "input[placeholder*='Search']",
        "input[type='text'][placeholder*='search' i]",
    ]
    last_reload = 0.0
    while time.monotonic() < deadline:
        if await _page_has_disconnect_banner(page):
            now = time.monotonic()
            if now - last_reload > 1.5:
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
                last_reload = now
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                await asyncio.sleep(0.4)
            await asyncio.sleep(0.2)
            continue

        for sel in search_selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible() and await el.is_enabled():
                    return True, "Upgrader ready"
            except Exception:
                pass
        await asyncio.sleep(0.25)
    return False, "Halaman upgrader belum interaktif (timeout)"


async def _reconnect_if_needed(page: Page, wait_ms: int = 20000) -> bool:
    if await _page_has_disconnect_banner(page):
        try:
            await page.reload(wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        ok, _ = await _wait_upgrader_interactive(page, total_timeout_ms=wait_ms)
        return ok
    return True


async def _connect_visible(page: Page) -> bool:
    try:
        for sel in _CONNECT_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc and await loc.is_visible():
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


async def _ensure_not_connect_or_reinject(
    page: Page,
    token: str,
    account_name: str,
    headless: bool,
    max_cycles: int = 3,
) -> tuple[bool, str]:
    for _ in range(max_cycles):
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=12000)
        except Exception:
            pass
        ok, _ = await _wait_upgrader_interactive(page, total_timeout_ms=20000)
        if not ok:
            try:
                await page.reload(wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            await rp(0.3, 0.6, headless)
            continue

        if not await _connect_visible(page):
            return True, "Sudah login (tidak ada Connect)"

        inj_ok, inj_reason = await robust_token_injection(page, token, account_name, max_retries=3, stop_check=None)
        if not inj_ok:
            try:
                await page.reload(wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass
            await rp(0.5, 1.0, headless)
        else:
            try:
                await page.goto(
                    "https://flip.gg/upgrader", wait_until="domcontentloaded", timeout=20000
                )
            except Exception:
                pass
            await rp(0.6, 1.2, headless)

        if not await _connect_visible(page):
            return True, "Reinject sukses (Connect hilang)"

    return False, "Masih muncul 'Connect' setelah reinject berkali-kali"


# ---------- Ensure Upgrader Ready ----------
async def _ensure_upgrader_ready(
    page, token: str, account_name: str, stop_check
) -> tuple[bool, str]:
    ok, why = await robust_token_injection(page, token, account_name, max_retries=3, stop_check=stop_check)
    if not ok:
        return False, why

    max_attempts = 2
    for attempt in range(max_attempts):
        if stop_check():
            return False, "Proses dihentikan"
        try:
            base_timeout = 8000 + (attempt * 2000)
            await page.goto("https://flip.gg/upgrader", timeout=base_timeout)
            await page.wait_for_load_state("domcontentloaded", timeout=6000)
            ok2, why2 = await _wait_upgrader_interactive(page, total_timeout_ms=20000)
            if ok2:
                return True, "Upgrader ready"
            if attempt < max_attempts - 1:
                try:
                    await page.reload()
                    await asyncio.sleep(1)
                    continue
                except Exception:
                    pass
            return False, why2
        except Exception as e:
            if attempt < max_attempts - 1:
                try:
                    await page.reload()
                    await asyncio.sleep(1)
                    continue
                except Exception:
                    pass
            return (
                False,
                f"Halaman upgrader tidak ready setelah {max_attempts} percobaan: {str(e)[:50]}",
            )
    return False, f"Halaman upgrader tidak ready setelah {max_attempts} percobaan"


# ---------- Search & Select Item (dengan REFRESH RONDE) ----------
async def _search_and_open_item(page: Page, query: str, headless: bool, name: str) -> bool:
    """
    Beberapa RONDE:
      - isi search
      - jika list/target tidak ketemu → REFRESH halaman → ulang lagi
    """
    rounds = 4
    for round_idx in range(1, rounds + 1):
        try:
            # kotak search
            search_selectors = [
                "input[placeholder='Search...']",
                "input[placeholder*='Search']",
                "input[type='text'][placeholder*='search' i]",
            ]
            search_el = None
            for sel in search_selectors:
                try:
                    el = await page.wait_for_selector(sel, timeout=8000)
                    if el and await el.is_visible():
                        search_el = el
                        break
                except Exception:
                    continue
            if not search_el:
                raise Exception("Search box tidak ditemukan")

            await search_el.click()
            await asyncio.sleep(0.2)
            try:
                await search_el.press("Control+a")
            except Exception:
                pass
            await asyncio.sleep(0.1)
            await search_el.fill(query)
            await asyncio.sleep(0.4)
            try:
                await search_el.press("Enter")
            except Exception:
                await page.keyboard.press("Enter")
            await asyncio.sleep(1.0)

            if not await _reconnect_if_needed(page):
                return False

            # tunggu list muncul
            list_selectors = [
                "div[class*='itemsList']",
                "div[class*='items-list']",
                "div[class*='search-results']",
                ".items-container",
            ]
            have_list = False
            for sel in list_selectors:
                try:
                    await page.wait_for_selector(sel, timeout=8000)
                    have_list = True
                    break
                except Exception:
                    continue

            if not have_list:
                # RONDE berikutnya refresh
                if round_idx < rounds:
                    await send_message_and_log(
                        f"[{name}] ⚠️ Search attempt {round_idx}: list tidak muncul, refresh...",
                        None,
                        None,
                    )
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    await asyncio.sleep(0.6)
                    continue
                else:
                    return False

            # cari target
            items = page.locator(
                "div[class*='itemsList'] div[class*='lootbox'], div[class*='items-list'] div[class*='lootbox'], [class*='lootbox']"
            )
            try:
                count = await items.count()
            except Exception:
                count = 0

            if count == 0:
                if round_idx < rounds:
                    await send_message_and_log(
                        f"[{name}] ⚠️ Item attempt {round_idx}: '{query}' tidak ditemukan, mencoba lagi...",
                        None,
                        None,
                    )
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    await asyncio.sleep(0.6)
                    continue
                else:
                    return False

            # filter target dengan beberapa cara
            target = None
            try:
                cand = items.filter(has=page.locator("span[class*='lootboxName']"), has_text=query)
                if await cand.count() > 0:
                    target = cand
            except Exception:
                pass
            if target is None:
                try:
                    cand = items.filter(has_text=query)
                    if await cand.count() > 0:
                        target = cand
                except Exception:
                    pass
            if target is None:
                try:
                    cand = page.locator(f"div[class*='lootbox']:has-text('{query}')")
                    if await cand.count() > 0:
                        target = cand
                except Exception:
                    pass

            if target is None or await target.count() == 0:
                if round_idx < rounds:
                    await send_message_and_log(
                        f"[{name}] ⚠️ Item attempt {round_idx}: '{query}' tidak ditemukan, mencoba lagi...",
                        None,
                        None,
                    )
                    try:
                        await page.reload(wait_until="domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    await asyncio.sleep(0.6)
                    continue
                else:
                    return False

            # klik thumbnail
            click_selectors = [
                "img[class*='lootboxImg']",
                "img[class*='lootbox-img']",
                "img",
                "[class*='lootbox']",
            ]
            for sel in click_selectors:
                try:
                    thumb = target.locator(sel).first
                    if await thumb.is_visible():
                        await thumb.scroll_into_view_if_needed()
                        await asyncio.sleep(0.2)
                        await thumb.click()
                        await asyncio.sleep(0.8)
                        return True
                except Exception:
                    continue

            # gagal klik → refresh dan coba next ronde
            if round_idx < rounds:
                await send_message_and_log(
                    f"[{name}] ⚠️ Item attempt {round_idx}: gagal klik, refresh...",
                    None,
                    None,
                )
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
                await asyncio.sleep(0.6)
                continue
            else:
                return False

        except Exception:
            if round_idx < rounds:
                try:
                    await page.reload(wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
                await asyncio.sleep(0.6)
                continue
            else:
                return False
    return False


# ===================== WORKER =====================
async def _process_account(
    acc_index: int,
    total_akun: int,
    acc: Dict[str, str],
    browser: Browser,
    search_query: str,
    bet_mode: str,
    bet_amount: str,
    headless: bool,
    context: Optional[Any],
    log_func: Optional[Any],
    counters: Dict[str, Any],
    lock: asyncio.Lock,
    stop_event: Optional[Any],
    delay_between_accounts: float,
    roll_direction: str = "roll_under",
):
    def diminta_stop() -> bool:
        """Cek apakah user meminta stop dari berbagai sumber"""
        # Cek stop_event jika ada
        if stop_event and getattr(stop_event, "is_set", None) and stop_event.is_set():
            return True
        # Cek bot_data stop flag
        if context and getattr(context, "application", None):
            try:
                return bool(context.application.bot_data.get("stop_upgrade"))
            except Exception:
                return False
        return False

    akun_index = acc_index
    name = acc.get("name", "?")
    token = acc.get("token", "")
    simple_log = f"[{akun_index}/{total_akun}] {name}"

    # Cek stop di awal sebelum memproses akun
    if diminta_stop():
        return  # Keluar tanpa pesan, biar tidak spam

    # ------ Page baru per akun (browser permanen) ------
    page = await browser.new_page()  # <- TIDAK bikin context manual
    page.set_default_timeout(20000)

    try:
        # Cek stop sebelum get profile
        if diminta_stop():
            return  # Keluar tanpa pesan, biar tidak spam
            
        profile = get_profile(token)
        if not profile:
            await send_message_and_log(f"[{name}] ❌ Token tidak aktif", context, log_func)
            async with lock:
                counters["akun_gagal"] += 1
            await rp(0.2, 0.3, headless)
            return

        # saldo + loading message
        saldo_awal = None
        try:
            current_balance = get_balance(token)
            if current_balance is None:
                await send_message_and_log(
                    f"[{name}] ❌ Gagal mendapatkan info saldo", context, log_func
                )
                await rp(0.2, 0.3, headless)
                return
            saldo_awal = current_balance
            emoji_list = ["💰", "🏦", "💎", "🪙"]
            selected_emoji = emoji_list[akun_index % len(emoji_list)]
            loading_msg = (
                f"{simple_log}  |  {selected_emoji} {current_balance:.4f}  |  ⏳ Loading..."
            )
            await send_telegram(loading_msg, context)
            if log_func:
                try:
                    log_func(loading_msg)
                except Exception:
                    pass

            bm = (bet_mode or "").strip().lower()
            if bm == "manual":
                required_amount = float(bet_amount)
                if current_balance < required_amount:
                    await send_message_and_log(
                        f"⚠️ Saldo tidak mencukupi. Saldo: {current_balance:.4f}, Dibutuhkan: {required_amount:.4f}",
                        context,
                        log_func,
                    )
                    async with lock:
                        counters["akun_gagal"] += 1
                    await rp(0.2, 0.3, headless)
                    return
            elif bm == "max":
                if current_balance < 0.1:
                    await send_message_and_log(
                        f"⚠️ Saldo tidak mencukupi untuk bet Max. Saldo: {current_balance:.4f}, Minimal: 0.10",
                        context,
                        log_func,
                    )
                    async with lock:
                        counters["akun_gagal"] += 1
                    await rp(0.2, 0.3, headless)
                    return
        except Exception as e:
            await send_telegram(f"[{name}] ❌ Error validasi saldo: {e}", context)
            if log_func:
                try:
                    log_func(f"[{name}] ❌ Error validasi saldo: {e}")
                except Exception:
                    pass
            await rp(0.2, 0.3, headless)
            return

        # Cek stop sebelum ensure ready
        if diminta_stop():
            return  # Keluar tanpa pesan, biar tidak spam

        # Ensure ready
        ready, reason = await _ensure_upgrader_ready(page, token, name, diminta_stop)
        if not ready:
            await send_telegram(f"⛔ {reason}", context)
            if log_func:
                try:
                    log_func(f"⛔ {reason}")
                except Exception:
                    pass
            async with lock:
                counters["akun_gagal"] += 1
            await rp(0.2, 0.3, headless)
            return

        # Cek stop sebelum pastikan tidak "Connect"
        if diminta_stop():
            return  # Keluar tanpa pesan, biar tidak spam

        # Pastikan tidak "Connect"
        ok_conn, why_conn = await _ensure_not_connect_or_reinject(
            page, token, name, headless=headless, max_cycles=3
        )
        if not ok_conn:
            await send_message_and_log(f"[{name}] ❌ {why_conn}", context, log_func)
            async with lock:
                counters["akun_gagal"] += 1
            await rp(0.2, 0.3, headless)
            return

        # Validasi token benar2 aktif
        token_validated = False
        for validation_attempt in range(3):
            # Cek stop di awal setiap attempt validasi
            if diminta_stop():
                return  # Keluar tanpa pesan, biar tidak spam
                
            try:
                balance_indicators = [
                    "[class*='balance']",
                    "[class*='wallet']",
                    "[class*='coin']",
                    "text=/balance/i",
                    "text=/wallet/i",
                ]
                found_indicator = False
                for indicator in balance_indicators:
                    try:
                        element = page.locator(indicator).first
                        if await element.is_visible():
                            found_indicator = True
                            break
                    except Exception:
                        continue

                if found_indicator:
                    try:
                        search_box = page.locator("input[placeholder*='Search']").first
                        if await search_box.is_visible() and await search_box.is_enabled():
                            token_validated = True
                            await send_message_and_log(
                                f"[{name}] ✅ Token berhasil divalidasi dan aktif",
                                context,
                                log_func,
                            )
                            break
                    except Exception:
                        pass

                if not token_validated:
                    if validation_attempt < 2:
                        await send_message_and_log(
                            f"[{name}] ⚠️ Token validation attempt {validation_attempt + 1}: Belum aktif, mencoba lagi...",
                            context,
                            log_func,
                        )
                        # Cek stop sebelum sleep panjang
                        if diminta_stop():
                            return  # Keluar tanpa pesan, biar tidak spam
                        await asyncio.sleep(2.0)
                        try:
                            await page.reload(wait_until="domcontentloaded", timeout=10000)
                            await asyncio.sleep(1.0)
                        except Exception:
                            pass
                        ok_conn, _ = await _ensure_not_connect_or_reinject(
                            page, token, name, headless=headless, max_cycles=1
                        )
                        if not ok_conn:
                            continue
                        continue
                    else:
                        raise Exception("Token tidak aktif setelah 3 attempts")

            except Exception as e:
                if validation_attempt < 2:
                    await send_message_and_log(
                        f"[{name}] ⚠️ Token validation attempt {validation_attempt + 1} error: {e}, mencoba lagi...",
                        context,
                        log_func,
                    )
                    # Cek stop sebelum sleep panjang
                    if diminta_stop():
                        return  # Keluar tanpa pesan, biar tidak spam
                    await asyncio.sleep(2.0)
                    continue
                else:
                    await send_message_and_log(
                        f"[{name}] ❌ Token validation gagal setelah 3 attempts: {e}",
                        context,
                        log_func,
                    )
                    async with lock:
                        counters["akun_gagal"] += 1
                    await rp(0.2, 0.3, headless)
                    return

        if not token_validated:
            await send_message_and_log(
                f"[{name}] ❌ Token tidak dapat divalidasi, skip akun ini",
                context,
                log_func,
            )
            async with lock:
                counters["akun_gagal"] += 1
            await rp(0.2, 0.3, headless)
            return

        # ----------------- Search + pilih item (dengan refresh ronde) -----------------
        if not await _reconnect_if_needed(page):
            await send_message_and_log(
                f"[{name}] ❌ Gagal: halaman disconnect (sebelum search)", context, log_func
            )
            await rp(0.2, 0.3, headless)
            return

        ok_item = await _search_and_open_item(page, search_query, headless, name)
        if not ok_item:
            await send_message_and_log(
                f"[{name}] ❌ Item dengan nama '{search_query}' tidak ditemukan setelah 3 attempts",
                context,
                log_func,
            )
            await rp(0.2, 0.3, headless)
            return

        if diminta_stop():
            await send_telegram("🛑 Upgrader dihentikan.", context)
            return

        # ----------------- Bet -----------------
        bet_success = False
        for bet_attempt in range(3):
            ok_conn, why_conn = await _ensure_not_connect_or_reinject(
                page, token, name, headless=headless, max_cycles=1
            )
            if not ok_conn:
                await send_message_and_log(f"[{name}] ❌ {why_conn}", context, log_func)
                await rp(0.2, 0.3, headless)
                return

            if not await _reconnect_if_needed(page):
                await send_message_and_log(
                    f"[{name}] ❌ Gagal: halaman disconnect (bet)", context, log_func
                )
                await rp(0.2, 0.3, headless)
                return
            try:
                await asyncio.sleep(0.5)
                bet_selectors = [
                    "span.tss-ttah9x-amountInput",
                    "span[class*='amountInput']",
                    "[class*='amount-input']",
                    "input[type='number']",
                ]
                amount_box = None
                input_amount = None
                for sel in bet_selectors:
                    try:
                        await page.wait_for_selector(sel, timeout=8000)
                        amount_box = page.locator(sel).first
                        if await amount_box.is_visible():
                            input_amount = amount_box.locator("input").first
                            if await input_amount.is_visible():
                                break
                    except Exception:
                        continue
                if not amount_box or not input_amount:
                    raise Exception("Bet input tidak ditemukan")

                await amount_box.scroll_into_view_if_needed()
                await asyncio.sleep(0.3)

                bm = (bet_mode or "").strip().lower()
                if bm == "max":
                    max_selectors = [
                        "span.tss-1t2wlb1-inputButtons button:has-text('Max')",
                        "span[class*='inputButtons'] button:has-text('Max')",
                        "button:has-text('Max')",
                        "[class*='max-button']",
                    ]
                    max_clicked = False
                    for sel in max_selectors:
                        try:
                            max_btn = page.locator(sel).first
                            if await max_btn.is_visible():
                                await max_btn.scroll_into_view_if_needed()
                                await asyncio.sleep(0.3)
                                await max_btn.click()
                                await asyncio.sleep(0.5)
                                max_clicked = True
                                break
                        except Exception:
                            continue
                    if not max_clicked:
                        raise Exception("Tombol Max tidak ditemukan atau tidak bisa diklik")
                else:
                    await input_amount.click()
                    await asyncio.sleep(0.2)
                    try:
                        await input_amount.click(click_count=3)
                    except Exception:
                        pass
                    await asyncio.sleep(0.1)
                    await input_amount.fill(str(bet_amount))
                    await asyncio.sleep(0.4)
                    try:
                        await input_amount.press("Enter")
                    except Exception:
                        pass

                await asyncio.sleep(0.4)
                try:
                    val = await input_amount.input_value()
                    if not val or float(val) <= 0:
                        if bet_attempt < 2:
                            await send_message_and_log(
                                f"[{name}] ⚠️ Bet attempt {bet_attempt + 1}: Nilai bet tidak valid, mencoba lagi...",
                                context,
                                log_func,
                            )
                            await asyncio.sleep(1.0)
                            continue
                        else:
                            await send_telegram(
                                f"[{name}] ⚠️ Nilai bet belum terisi valid setelah 3 attempts.",
                                context,
                            )
                    else:
                        bet_success = True
                        break
                except Exception:
                    if bet_attempt < 2:
                        await send_message_and_log(
                            f"[{name}] ⚠️ Bet attempt {bet_attempt + 1}: Error validasi bet, mencoba lagi...",
                            context,
                            log_func,
                        )
                        await asyncio.sleep(1.0)
                        continue
                    else:
                        bet_success = True
                        break
            except Exception as e:
                if bet_attempt < 2:
                    await send_message_and_log(
                        f"[{name}] ⚠️ Bet attempt {bet_attempt + 1} error: {e}, mencoba lagi...",
                        context,
                        log_func,
                    )
                    await asyncio.sleep(1.0)
                    continue
                else:
                    await send_message_and_log(
                        f"[{name}] ❌ Gagal set jumlah bet setelah 3 attempts: {e}",
                        context,
                        log_func,
                    )
                    await rp(0.2, 0.3, headless)
                    return

        if not bet_success:
            await send_message_and_log(
                f"[{name}] ❌ Set bet gagal setelah 3 attempts", context, log_func
            )
            await rp(0.2, 0.3, headless)
            return

        # ----------------- Roll Direction (optional) -----------------
        ok_conn, why_conn = await _ensure_not_connect_or_reinject(
            page, token, name, headless=headless, max_cycles=1
        )
        if not ok_conn:
            await send_message_and_log(f"[{name}] ❌ {why_conn}", context, log_func)
            await rp(0.2, 0.3, headless)
            return
        if not await _reconnect_if_needed(page):
            await send_message_and_log(
                f"[{name}] ❌ Gagal: halaman disconnect (roll direction)", context, log_func
            )
            await rp(0.2, 0.3, headless)
            return
        try:
            roll_selectors = [
                "span.tss-7qkwiw-roll",
                "span[class*='roll']",
                "[class*='roll-button']",
                "[class*='rollButton']",
            ]
            roll_clicked = False
            for selector in roll_selectors:
                try:
                    roll_elements = page.locator(selector)
                    count = await roll_elements.count()
                    for i in range(count):
                        el = roll_elements.nth(i)
                        if await el.is_visible():
                            text_lower = (await el.inner_text()).lower()
                            is_target = (
                                roll_direction == "roll_over"
                                and ("over" in text_lower or "up" in text_lower)
                            ) or (
                                roll_direction == "roll_under"
                                and ("under" in text_lower or "down" in text_lower)
                            )
                            if is_target:
                                await el.scroll_into_view_if_needed()
                                await rp(0.2, 0.5, headless)
                                await el.click()
                                await rp(0.4, 0.8, headless)
                                roll_clicked = True
                                break
                    if roll_clicked:
                        break
                except Exception:
                    continue
            if not roll_clicked:
                try:
                    if roll_direction == "roll_over":
                        roll_btn = page.locator("text=/roll.*over/i, text=/over/i").first
                    else:
                        roll_btn = page.locator("text=/roll.*under/i, text=/under/i").first
                    if await roll_btn.is_visible():
                        await roll_btn.scroll_into_view_if_needed()
                        await rp(0.2, 0.5, headless)
                        await roll_btn.click()
                        await rp(0.4, 0.8, headless)
                        roll_clicked = True
                except Exception:
                    pass
            if not roll_clicked:
                await send_message_and_log(
                    f"[{name}] ⚠️ Tombol roll direction tidak ditemukan, melanjutkan dengan default",
                    context,
                    log_func,
                )
        except Exception as e:
            await send_message_and_log(
                f"[{name}] ⚠️ Error saat pilih roll direction: {e}, melanjutkan upgrade",
                context,
                log_func,
            )

        if diminta_stop():
            await send_telegram("🛑 Upgrader dihentikan.", context)
            return

        # ----------------- Upgrade -----------------
        ok_conn, why_conn = await _ensure_not_connect_or_reinject(
            page, token, name, headless=headless, max_cycles=1
        )
        if not ok_conn:
            await send_message_and_log(f"[{name}] ❌ {why_conn}", context, log_func)
            await rp(0.2, 0.3, headless)
            return

        if not await _reconnect_if_needed(page):
            await send_message_and_log(
                f"[{name}] ❌ Gagal: halaman disconnect (upgrade)", context, log_func
            )
            await rp(0.2, 0.3, headless)
            return
        try:
            upgrade_root = page.locator(
                "div.tss-1rybq8x-upgradeButton, div[class*='upgradeButton']"
            ).first
            await upgrade_root.scroll_into_view_if_needed()
            await rp(0.2, 0.5, headless)
            upgrade_label = upgrade_root.locator("span:has-text('Upgrade')").first
            clicked = False
            for _ in range(25):
                if diminta_stop():
                    return  # Keluar tanpa pesan, biar tidak spam
                if not await _reconnect_if_needed(page):
                    continue
                try:
                    await upgrade_label.click(timeout=1000)
                    clicked = True
                    break
                except Exception:
                    try:
                        await upgrade_root.click(timeout=1000)
                        clicked = True
                        break
                    except Exception:
                        await rp(0.4, 0.8, headless)
            if not clicked:
                await send_telegram(f"[{name}] ❌ Gagal klik Upgrade (timeout)", context)
                await rp(0.2, 0.3, headless)
                return

            upgrade_root = page.locator(
                "div.tss-1rybq8x-upgradeButton, div[class*='upgradeButton']"
            ).first
            for _ in range(20):
                if diminta_stop():
                    return  # Keluar tanpa pesan, biar tidak spam
                if await _reconnect_if_needed(page) is False:
                    continue
                cls = await upgrade_root.get_attribute("class") or ""
                if "disabled" in cls:
                    break
                await asyncio.sleep(0.5)

            for _ in range(120):
                if diminta_stop():
                    return  # Keluar tanpa pesan, biar tidak spam
                if await _reconnect_if_needed(page) is False:
                    continue
                cls = await upgrade_root.get_attribute("class") or ""
                if "disabled" not in cls:
                    await send_telegram(f"[{name}] ✅ Rolling selesai.", context)
                    if log_func:
                        try:
                            log_func(f"[{name}] ✅ Rolling selesai.")
                        except Exception:
                            pass
                    break
                await asyncio.sleep(0.5)

            # cek hasil via saldo
            if saldo_awal is not None:
                try:
                    saldo_akhir = get_balance(token)
                    if saldo_akhir is not None:
                        async with lock:
                            counters["akun_sukses"] += 1
                        if saldo_akhir > saldo_awal:
                            profit = saldo_akhir - saldo_awal
                            async with lock:
                                counters["akun_menang"] += 1
                                counters["total_profit"] += profit
                                counters["winners_list"].append(
                                    {"name": name, "profit": profit, "saldo": saldo_akhir}
                                )
                            win_msg = (
                                "🎉 <b>MENANG!</b> 🎉\n"
                                f"👤 <b>{name}</b>\n"
                                f"💰 Profit: <b>+{profit:.4f}</b>\n"
                                f"💳 Saldo: <b>{saldo_akhir:.4f}</b>\n"
                                "━━━━━━━━━━━━━━━━━━━━"
                            )
                            await send_telegram(win_msg, context)
                            if log_func:
                                try:
                                    log_func(win_msg)
                                except Exception:
                                    pass
                        else:
                            loss = saldo_awal - saldo_akhir
                            async with lock:
                                counters["akun_kalah"] += 1
                                counters["total_loss"] += loss
                    else:
                        async with lock:
                            counters["akun_error"] += 1
                        await send_telegram(
                            f"[{name}] ⚠️ Tidak dapat menentukan hasil (gagal cek saldo akhir)",
                            context,
                        )
                        if log_func:
                            try:
                                log_func(
                                    f"[{name}] ⚠️ Tidak dapat menentukan hasil (gagal cek saldo akhir)"
                                )
                            except Exception:
                                pass
                except Exception as e:
                    async with lock:
                        counters["akun_error"] += 1
                    await send_telegram(f"[{name}] ❌ Error cek hasil: {e}", context)
                    if log_func:
                        try:
                            log_func(f"[{name}] ❌ Error cek hasil: {e}")
                        except Exception:
                            pass
        except Exception as e:
            await send_message_and_log(f"[{name}] ❌ Gagal klik Upgrade: {e}", context, log_func)
            await rp(0.2, 0.3, headless)
            return
    except Exception as e:
        await send_message_and_log(f"[{name}] ❌ Error tak terduga: {e}", context, log_func)
        async with lock:
            counters["akun_error"] += 1
    finally:
        try:
            await page.close()
        except Exception:
            pass

    if delay_between_accounts > 0:
        if not diminta_stop():
            await asyncio.sleep(delay_between_accounts)


# ---------- Browser pool permanen ----------
async def _launch_pool(pw, size: int, headless: bool) -> List[Browser]:
    browsers: List[Browser] = []
    for _ in range(size):
        try:
            b = await pw.chromium.launch(
                channel="chrome", headless=headless, args=["--disable-dev-shm-usage"]
            )
        except Exception:
            b = await pw.chromium.launch(headless=headless, args=["--disable-dev-shm-usage"])
        browsers.append(b)
    return browsers


# ===================== DISPATCHER =====================
async def jalankan_upgrader(
    search_query: str,
    bet_mode: str,
    bet_amount: str = "0.01",
    headless: bool = False,  # SELALU visible default
    context: Optional[Any] = None,
    akun_list: Optional[List[Dict[str, str]]] = None,
    stop_event: Optional[Any] = None,
    log_func: Optional[Any] = None,
    delay_between_accounts: float = 0.0,
    select_by: str = "name",
    select_value: str = "",
    max_concurrency: int = 3,  # DEFAULT 3 worker (untuk “akun bersaldo” juga)
    roll_direction: str = "roll_under",
):
    try:
        if not is_enabled("upgrader"):
            await send_telegram("🚧 Fitur Upgrader: coming soon", context)
            return
    except Exception:
        pass

    akun = akun_list if akun_list is not None else load_accounts()
    if not akun:
        await send_telegram("❌ Tidak ada akun tersedia.", context)
        return

    total_akun = len(akun)
    counters = {
        "akun_sukses": 0,
        "akun_gagal": 0,
        "akun_menang": 0,
        "akun_kalah": 0,
        "akun_error": 0,
        "winners_list": [],
        "total_profit": 0.0,
        "total_loss": 0.0,
    }
    lock = asyncio.Lock()

    start_msg = (
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🚀 <b>UPGRADER DIMULAI!</b> 🚀\n"
        f"🎯 Target Item: <b>{search_query}</b>\n"
        f"👥 Total Akun: <b>{total_akun}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await send_telegram(start_msg, context)
    if log_func:
        try:
            log_func(start_msg)
        except Exception:
            pass

    # ------ pool 3 browser permanen (atau sesuai max_concurrency) ------
    worker_size = max(1, int(max_concurrency or 3))

    async with async_playwright() as pw:
        browsers = await _launch_pool(pw, worker_size, headless=False)  # visible
        sem = asyncio.Semaphore(worker_size)

        # antrian akun digunakan agar setiap browser kerja bergilir
        queue: asyncio.Queue = asyncio.Queue()
        for i, acc in enumerate(akun, start=1):
            queue.put_nowait((i, acc))

        async def worker(worker_id: int, browser: Browser):
            while not queue.empty():
                try:
                    async with sem:
                        acc_index, acc = await queue.get()
                        await _process_account(
                            acc_index=acc_index,
                            total_akun=total_akun,
                            acc=acc,
                            browser=browser,  # <- browser permanen per worker
                            search_query=search_query,
                            bet_mode=bet_mode,
                            bet_amount=bet_amount,
                            headless=headless,
                            context=context,
                            log_func=log_func,
                            counters=counters,
                            lock=lock,
                            stop_event=stop_event,
                            delay_between_accounts=delay_between_accounts,
                            roll_direction=roll_direction,
                        )
                finally:
                    try:
                        queue.task_done()
                    except Exception:
                        pass

        tasks = [asyncio.create_task(worker(i, browsers[i])) for i in range(worker_size)]
        await asyncio.gather(*tasks)

        # tutup browser pool setelah semua akun selesai
        for b in browsers:
            try:
                await b.close()
            except Exception:
                pass

    # Cek stop sebelum menampilkan ringkasan
    def diminta_stop_main() -> bool:
        """Cek apakah user meminta stop dari berbagai sumber"""
        # Cek stop_event jika ada
        if stop_event and getattr(stop_event, "is_set", None) and stop_event.is_set():
            return True
        # Cek bot_data stop flag
        if context and getattr(context, "application", None):
            try:
                return bool(context.application.bot_data.get("stop_upgrade"))
            except Exception:
                return False
        return False

    if diminta_stop_main():
        await send_telegram("🛑 Upgrader dihentikan.", context)
        return

    akun_sukses = counters["akun_sukses"]
    akun_gagal = counters["akun_gagal"]
    akun_kalah = counters["akun_kalah"]
    akun_error = counters["akun_error"]
    akun_menang = counters["akun_menang"]
    winners_list = counters["winners_list"]
    total_profit = counters["total_profit"]
    total_loss = counters["total_loss"]

    print(
        f"DEBUG: total_akun={total_akun}, akun_sukses={akun_sukses}, akun_gagal={akun_gagal}, akun_error={akun_error}"
    )

    summary_msg = "╔══════════════════════════════╗\n"
    summary_msg += "║    📊 <b>RINGKASAN UPGRADER</b>    ║\n"
    summary_msg += "╚══════════════════════════════╝\n\n"
    summary_msg += "📋 <b>STATISTIK AKUN:</b>\n"
    summary_msg += f"├─ 👥 Total akun: <b>{total_akun}</b>\n"
    summary_msg += f"├─ ✅ Sukses: <b>{akun_sukses}</b>\n"
    summary_msg += f"├─ ❌ Gagal: <b>{akun_gagal}</b>\n"
    summary_msg += f"└─ ⚠️ Error: <b>{akun_error}</b>\n\n"
    summary_msg += "🎮 <b>HASIL PERMAINAN:</b>\n"
    win_rate = (akun_menang / akun_sukses * 100) if akun_sukses > 0 else 0
    summary_msg += f"├─ 🎉 Menang: <b>{akun_menang}</b> ({win_rate:.1f}%)\n"
    summary_msg += f"└─ 💔 Kalah: <b>{akun_kalah}</b> ({100-win_rate:.1f}%)\n\n"
    summary_msg += "💰 <b>RINGKASAN KEUANGAN:</b>\n"
    if total_profit > 0:
        summary_msg += f"├─ 💰 Total Profit: <b>+{total_profit:.4f}</b>\n"
    if total_loss > 0:
        summary_msg += f"├─ 💸 Total Loss: <b>-{total_loss:.4f}</b>\n"
    net_result = total_profit - total_loss
    if net_result > 0:
        summary_msg += f"└─ 📈 Net Result: <b>+{net_result:.4f}</b> 🟢\n\n"
    elif net_result < 0:
        summary_msg += f"└─ 📉 Net Result: <b>{net_result:.4f}</b> 🔴\n\n"
    else:
        summary_msg += f"└─ ⚖️ Net Result: <b>{net_result:.4f}</b> ⚪\n\n"

    if winners_list:
        winners_list.sort(key=lambda x: x["profit"], reverse=True)
        summary_msg += "🏆 <b>HALL OF FAME - PEMENANG:</b>\n"
        summary_msg += "┌─────────────────────────────────┐\n"
        for i, winner in enumerate(winners_list, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🏅"
            summary_msg += f"│ {medal} <b>{winner['name']}</b>\n"
            summary_msg += f"│    💰 Profit: <b>+{winner['profit']:.4f}</b>\n"
            summary_msg += f"│    💳 Saldo: <b>{winner['saldo']:.4f}</b>\n"
            if i < len(winners_list):
                summary_msg += "├─────────────────────────────────┤\n"
        summary_msg += "└─────────────────────────────────┘\n"
    else:
        summary_msg += "🤷‍♂️ <b>Tidak ada pemenang kali ini</b>\n"
        summary_msg += "keleh kabeh asu\n"

    await send_telegram(summary_msg, context)
    if log_func:
        try:
            log_func(f"\n{summary_msg}")
        except Exception:
            pass
