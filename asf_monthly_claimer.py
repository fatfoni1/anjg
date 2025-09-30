from __future__ import annotations
import os
import asyncio
from decimal import Decimal, InvalidOperation
from typing import List, Dict, Optional, Tuple, Any

from playwright.async_api import async_playwright

from asf_core import (
    load_accounts,
    send_telegram,
    get_balance as core_get_balance,
    inject_and_validate_token_fast,
)

# ==============================
# Util dasar
# ==============================

def _base_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def read_monthly_names(path: Optional[str] = None) -> List[str]:
    """
    Baca daftar nama dari monthly.txt (satu nama per baris),
    - Trim whitespace, skip kosong/komentar
    - Uniq case-insensitive dengan mempertahankan urutan kemunculan pertama
    """
    monthly_path = path or os.path.join(_base_dir(), "monthly.txt")
    names: List[str] = []
    seen = set()

    if not os.path.exists(monthly_path):
        return names

    with open(monthly_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = (raw or "").strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            low = line.lower()
            if low in seen:
                continue
            seen.add(low)
            names.append(line)
    return names


def resolve_accounts_by_names(names: List[str]) -> List[Dict[str, str]]:
    """
    Cocokkan names (case-insensitive) ke akun.enc via load_accounts().
    Return list akun (dict) berisi minimal {name, token}, terurut sesuai urutan 'names'.
    Hanya yang match yang dikembalikan.
    """
    accounts = load_accounts() or []
    if not names or not accounts:
        return []

    # index by lower(name) -> first match
    idx: Dict[str, Dict[str, str]] = {}
    for acc in accounts:
        nm = (acc.get("name") or "").strip()
        if not nm:
            continue
        low = nm.lower()
        if low not in idx:
            idx[low] = {"name": nm, "token": acc.get("token", "")}

    result: List[Dict[str, str]] = []
    for n in names:
        low = (n or "").strip().lower()
        if not low:
            continue
        if low in idx and (idx[low].get("token") or ""):
            result.append(idx[low])
    return result


async def _get_balance_decimal(token: str) -> Decimal:
    try:
        val = core_get_balance(token)
        return Decimal(str(val))
    except (InvalidOperation, Exception):
        return Decimal("0")


async def _login_with_token(page, token: str, account_name: str) -> Tuple[bool, str]:
    """
    Gunakan util eksis untuk inject token ke flip.gg, dengan verifikasi cepat.
    """
    try:
        valid, reason = await inject_and_validate_token_fast(page, token, account_name)
        return valid, reason
    except Exception as e:
        return False, f"Login error: {e}"


async def _wait_and_click_claim(page) -> Tuple[bool, str]:
    """
    Tunggu tombol "Claim Prize" lalu klik. Locator robust, hindari class dinamis.
    Return (clicked, info_msg)
    """
    try:
        # Kandidat locator prioritas
        candidates = [
            page.get_by_role("button", name="Claim Prize"),
            page.locator("button:has-text('Claim Prize')"),
            page.locator("[role=button]:has-text('Claim Prize')"),
            page.locator("button:has-text('Claim')"),  # fallback lebih longgar
        ]

        # Tunggu salah satu tersedia
        button = None
        for cand in candidates:
            try:
                await cand.wait_for(state="visible", timeout=4000)
                button = cand
                break
            except Exception:
                continue

        if button is None:
            # Deteksi kondisi sudah claimed (informasi saja)
            try:
                claimed = await page.locator("*:has-text('Claimed')").first.is_visible()
                if claimed:
                    return False, "Sudah di-claim (button 'Claimed')"
            except Exception:
                pass
            return False, "Tombol Claim tidak ditemukan"

        # Klik dengan retry ringan jika overlay
        for _ in range(3):
            try:
                await button.scroll_into_view_if_needed()
                await asyncio.sleep(0.1)
                await button.click(timeout=1500)
                return True, "Klik Claim Prize"
            except Exception:
                await asyncio.sleep(0.25)
        return False, "Gagal klik tombol Claim"

    except Exception as e:
        return False, f"Error claim: {e}"


async def run_claim_monthly(context: Optional[Any], visible: bool = False):
    """
    Alur utama Claim Monthly (sequential, 1 browser + new context per akun):
    - Log awal
    - Baca monthly.txt, resolve ke akun.enc
    - Per akun: login via token, buka rewards, ambil saldo awal, klik claim, ambil saldo akhir
      - Sukses jika saldo akhir > saldo awal → log nominal yang bertambah
      - Jika tombol tidak ada → log warning
      - Jika error → log ringkas
    - Tutup context per akun, jeda 1.5 detik
    - Ringkasan akhir
    """
    names = read_monthly_names()

    if not names:
        await send_telegram("⚠️ monthly.txt kosong atau tidak ada.", context)
        return

    targets = resolve_accounts_by_names(names)
    if not targets:
        await send_telegram("⚠️ Tidak ada nama pada monthly.txt yang cocok dengan akun.enc", context)
        return

    total = len(targets)
    await send_telegram(f"⏳ Memproses Claim Monthly… target: {total} akun", context)

    sukses = 0
    gagal = 0
    gagal_list: List[str] = []

    headless = not bool(visible)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        try:
            for idx, acc in enumerate(targets, 1):
                name = acc.get("name") or "(noname)"
                token = acc.get("token") or ""

                await send_telegram(f"[{idx}/{total}] {name} — mulai", context)

                ctx = await browser.new_context()
                page = await ctx.new_page()

                try:
                    # Login via token (inject localStorage)
                    ok, reason = await _login_with_token(page, token, name)
                    if not ok:
                        gagal += 1
                        gagal_list.append(name)
                        await send_telegram(f"[{idx}/{total}] {name} | ❌ {reason}", context)
                        continue

                    # Buka halaman Monthly Bonus
                    try:
                        await page.goto("https://flip.gg/rewards#MonthlyBonus", timeout=12000)
                        await page.wait_for_load_state("domcontentloaded", timeout=8000)
                        await asyncio.sleep(0.3)
                    except Exception as e:
                        gagal += 1
                        gagal_list.append(name)
                        await send_telegram(
                            f"[{idx}/{total}] {name} | ❌ Gagal buka halaman rewards: {str(e)[:60]}",
                            context,
                        )
                        continue

                    # Ambil saldo awal via API (lebih stabil)
                    saldo_awal = await _get_balance_decimal(token)

                    # Claim
                    clicked, info = await _wait_and_click_claim(page)
                    if not clicked:
                        # Jika tombol tidak ditemukan atau sudah claimed
                        if "Claim tidak ditemukan" in info:
                            await send_telegram(
                                f"[{idx}/{total}] {name} | ⚠️ Tombol Claim tidak ditemukan",
                                context,
                            )
                        else:
                            await send_telegram(
                                f"[{idx}/{total}] {name} | ⚠️ {info}",
                                context,
                            )
                        # Tetap cek saldo akhir sebagai validasi pasif (mungkin auto-claim oleh UI)
                        await asyncio.sleep(0.4)

                    # Tunggu sedikit setelah klik agar backend memproses
                    await asyncio.sleep(1.2)

                    # Ambil saldo akhir
                    saldo_akhir = await _get_balance_decimal(token)

                    # Evaluasi
                    if saldo_akhir > saldo_awal:
                        diff = saldo_akhir - saldo_awal
                        sukses += 1
                        await send_telegram(
                            f"[{idx}/{total}] {name} | ✅ +${diff:.2f} (before: ${saldo_awal:.2f} → after: ${saldo_akhir:.2f})",
                            context,
                        )
                    else:
                        gagal += 1
                        gagal_list.append(name)
                        # Coba baca toast/error singkat dari UI
                        err_text = ""
                        try:
                            toast = page.locator("div:has-text('error'), div:has-text('Error'), [role='alert']").first
                            if await toast.is_visible():
                                err_text = (await toast.inner_text())[:60]
                        except Exception:
                            pass
                        detail = f"Error: {err_text}" if err_text else "Gagal/Saldo tidak bertambah"
                        await send_telegram(
                            f"[{idx}/{total}] {name} | ❌ {detail}",
                            context,
                        )
                except Exception as e:
                    gagal += 1
                    gagal_list.append(name)
                    await send_telegram(
                        f"[{idx}/{total}] {name} | ❌ Error tak terduga: {str(e)[:80]}",
                        context,
                    )
                finally:
                    try:
                        await ctx.close()
                    except Exception:
                        pass

                # Anti terlalu cepat: jeda 0.5 detik per akun (dipersingkat)
                if idx < total:
                    await asyncio.sleep(0.5)
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    # Ringkasan akhir
    if gagal_list:
        gagal_join = ", ".join(gagal_list)
        await send_telegram(
            f"Selesai. Target: {total}, Sukses: {sukses}, Gagal: {gagal}\nGagal: {gagal_join}",
            context,
        )
    else:
        await send_telegram(
            f"Selesai. Target: {total}, Sukses: {sukses}, Gagal: {gagal}",
            context,
        )
