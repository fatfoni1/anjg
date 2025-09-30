from __future__ import annotations
import concurrent.futures
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from asf_core import get_profile


@dataclass
class MonthlyBonusInfo:
    success: bool
    bonus: Optional[float] = None
    wagered: Optional[float] = None
    available_on_dt: Optional[datetime] = None
    available_on_str: str = "N/A"
    claimable: Optional[bool] = None
    error: Optional[str] = None


def _parse_available_on(iso_str: Optional[str]) -> Tuple[Optional[datetime], str]:
    """
    Parse ISO date (with Z) into UTC datetime and formatted string "dd MM YYYY".
    Returns (datetime|None, formatted_str)
    """
    if not iso_str or not isinstance(iso_str, str):
        return None, "N/A"
    try:
        s = iso_str.strip()
        # Normalize trailing Z to +00:00 for fromisoformat compatibility
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        # Ensure aware in UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt, dt.strftime("%d %m %Y")
    except Exception:
        return None, "N/A"


def get_monthly_bonus(token: str) -> MonthlyBonusInfo:
    """
    Ambil informasi monthly bonus untuk satu akun dari endpoint user (via get_profile).
    - Mengharapkan struktur user["monthlyBonus"] = { bonus, wagered, availableOn }
    - Normalisasi output dalam MonthlyBonusInfo.
    Catatan: get_profile() sudah menangani requests & error dasar, mengembalikan dict.
    """
    try:
        if not token or not isinstance(token, str) or len(token.strip()) < 10:
            return MonthlyBonusInfo(success=False, error="Token kosong/pendek")

        user = get_profile(token)
        if not user or not isinstance(user, dict):
            return MonthlyBonusInfo(success=False, error="Profile kosong/tidak valid")

        mb = user.get("monthlyBonus", {}) or {}
        if not isinstance(mb, dict) or not mb:
            # Tidak semua akun punya monthlyBonus; anggap tidak tersedia
            return MonthlyBonusInfo(success=False, error="Monthly bonus tidak tersedia")

        bonus = mb.get("bonus")
        wagered = mb.get("wagered")
        av_on = mb.get("availableOn")
        dt, av_str = _parse_available_on(av_on)

        # claimable jika sekarang >= availableOn
        now_utc = datetime.now(timezone.utc)
        claimable = bool(dt and now_utc >= dt)

        try:
            bonus_f = float(bonus) if bonus is not None else None
        except Exception:
            bonus_f = None
        try:
            wagered_f = float(wagered) if wagered is not None else None
        except Exception:
            wagered_f = None

        return MonthlyBonusInfo(
            success=True,
            bonus=bonus_f,
            wagered=wagered_f,
            available_on_dt=dt,
            available_on_str=av_str,
            claimable=claimable,
        )
    except Exception as e:
        return MonthlyBonusInfo(success=False, error=f"Error: {str(e)}")


def get_all_monthly_bonus(
    accounts: List[Dict[str, str]],
    max_workers: int = 5,
    retries: int = 0,
) -> List[Dict[str, Any]]:
    """
    Ambil monthly bonus untuk seluruh akun secara paralel (ThreadPool).
    - accounts: list of {name, token}
    - retries: jumlah retry ringan untuk error non-sukses (default 0 = tanpa retry)
    Return list of dict per akun: {
      name, success, bonus, wagered, available_on_dt, available_on_str, claimable, error
    }
    """
    results: List[Dict[str, Any]] = []
    accounts = accounts or []

    def _worker(acc: Dict[str, str]) -> Dict[str, Any]:
        name = (acc.get("name") or "").strip()
        token = acc.get("token") or ""
        last_err = None
        attempt = 0
        while attempt <= max(0, retries):
            info = get_monthly_bonus(token)
            if info.success:
                return {
                    "name": name,
                    "success": True,
                    "bonus": info.bonus,
                    "wagered": info.wagered,
                    "available_on_dt": info.available_on_dt,
                    "available_on_str": info.available_on_str,
                    "claimable": info.claimable,
                    "error": None,
                }
            last_err = info.error or "Gagal"
            attempt += 1
        return {
            "name": name,
            "success": False,
            "bonus": None,
            "wagered": None,
            "available_on_dt": None,
            "available_on_str": "N/A",
            "claimable": None,
            "error": last_err,
        }

    if max_workers <= 1 or len(accounts) <= 1:
        for acc in accounts:
            results.append(_worker(acc))
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_worker, acc) for acc in accounts]
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                # Should not happen since worker handles errors, but keep guard
                results.append({
                    "name": "",
                    "success": False,
                    "bonus": None,
                    "wagered": None,
                    "available_on_dt": None,
                    "available_on_str": "N/A",
                    "claimable": None,
                    "error": f"Worker error: {e}",
                })
    return results


def format_monthly_bonus_report(results: List[Dict[str, Any]]) -> str:
    """
    Buat laporan ringkas untuk Telegram (parse_mode=HTML), menggunakan <pre> agar rapi.
    - Urutkan: claimable True dulu, lalu bonus desc.
    - Tampilkan ringkasan: total akun, jumlah klaimable, total klaimable $.
    """
    results = results or []

    def _bonus_val(r):
        try:
            return float(r.get("bonus") or 0.0)
        except Exception:
            return 0.0

    def _sort_key(r):
        # Klaimable dulu (True > False), lalu bonus desc
        return (1 if r.get("claimable") else 0, _bonus_val(r))

    # Sort descending by sort key
    results_sorted = sorted(results, key=_sort_key, reverse=True)

    # Stats
    total = len(results_sorted)
    claimable_count = sum(1 for r in results_sorted if r.get("success") and r.get("claimable"))
    non_claimable_count = sum(1 for r in results_sorted if r.get("success") and not r.get("claimable"))
    sum_claimable = sum(_bonus_val(r) for r in results_sorted if r.get("success") and r.get("claimable"))

    # Padding widths
    max_name = max((len((r.get("name") or "")) for r in results_sorted), default=10)
    max_bonus = max((len(f"{_bonus_val(r):.2f}") for r in results_sorted if r.get("success")), default=4)

    lines: List[str] = []
    header = (
        "💸 Monthly Bonus\n"
        f"Akun: {total} | ✅ Klaimable: {claimable_count} | ❌ Tidak: {non_claimable_count}\n"
        f"Σ Klaimable: ${sum_claimable:.2f}\n"
    )
    lines.append(header)

    # Detail lines
    for idx, r in enumerate(results_sorted, 1):
        name = (r.get("name") or "").ljust(max_name)
        if r.get("success"):
            bonus_str = f"{_bonus_val(r):.2f}".rjust(max_bonus)
            avail = r.get("available_on_str") or "N/A"
            flag = "✅" if r.get("claimable") else "❌"
            line = f"{str(idx).rjust(3)}. {name} | Bonus: {bonus_str} | Avail: {avail} | {flag}"
        else:
            err = (r.get("error") or "Error").strip()
            line = f"{str(idx).rjust(3)}. {name} | Error: {err}"
        lines.append(line)

    content = "\n".join(lines)
    return f"<pre>{content}</pre>"
