import asyncio
import html
import os
import re
import warnings

import requests

warnings.filterwarnings("ignore", category=UserWarning)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from asf_auto_rain import jalankan_auto_rain, load_rain_accounts, save_rain_accounts
from asf_core import (
    API_REDEEM_URL,
    TG_TOKEN,
    get_balance,
    get_profile,
    get_vip,
    load_accounts,
    load_seed_phrases,
    save_accounts,
    save_seed_phrases,
    validate_token_requests_fast,
)
from asf_monthly_bonus import format_monthly_bonus_report, get_all_monthly_bonus
from asf_monthly_claimer import run_claim_monthly
from asf_token_refresher import add_accounts_via_seed, refresh_invalid_tokens
from asf_upgrader import jalankan_upgrader
from asf_wheel import jalankan_auto_bet
from feature_flags import is_enabled

# ==== STATE CONSTANTS ====
WHEEL_MULT, WHEEL_BET_TYPE, WHEEL_BET_VALUE, WHEEL_BROWSER, WHEEL_FILTER, WHEEL_CONFIRM = range(6)
KLAIM_JUMLAH, KLAIM_DELAY, KLAIM_PILIH_KUPON, KLAIM_KODE, KLAIM_CONFIRM = range(100, 105)
TAMBAH_NAMA, TAMBAH_TOKEN = range(200, 202)
HAPUS_PILIH = 300
CEKAKUN_SORT, CEKAKUN_RUNNING = 400, 401
EDIT_SELECT, EDIT_NAME, EDIT_TOKEN, EDIT_SEARCH = 500, 501, 502, 503
UPG_JUMLAH, UPG_SEARCH, UPG_BET_MODE, UPG_BET_AMOUNT, UPG_ROLL_DIRECTION, UPG_DELAY, UPG_FILTER, UPG_PARALEL, UPG_CONFIRM = range(
    600, 609
)

# New states for additional features
KELOLA_AKUN_MENU = 700
SEED_MENU, SEED_NAMA, SEED_PHRASE, SEED_PILIH_AKUN = range(710, 714)
VALIDASI_CONFIRM = 720
LIHAT_AKUN = 730
# Claim Monthly states
CLAIM_MONTHLY_CONFIRM = 900
# Seed Add states
SEED_ADD_BROWSER = 901

# Auto Rain states
RAIN_MENU = 1000
RAIN_BROWSER = 1001
RAIN_CONCURRENCY = 1002
RAIN_CAPSOLVER = 1003
RAIN_CONFIRM = 1004

# Kelola Akun Rain states
RAIN_MANAGE = 1010
RAIN_ADD_NAME = 1011
RAIN_ADD_TOKEN = 1012
RAIN_EDIT_SELECT = 1013
RAIN_EDIT_NAME = 1014
RAIN_EDIT_TOKEN = 1015
RAIN_DELETE_SELECT = 1016


# Helper: main menu keyboard
def _main_menu_keyboard():
    return [
        [InlineKeyboardButton("🧰 Fitur Utama", callback_data="fitur_utama")],
        [
            InlineKeyboardButton("📊 Cek Akun", callback_data="cekakun"),
            InlineKeyboardButton("⚙️ Kelola Akun", callback_data="kelola_akun"),
        ],
        [
            InlineKeyboardButton("🔒 Kelola Seed", callback_data="kelola_seed"),
            InlineKeyboardButton("🔐 Validasi Token", callback_data="validasi_token"),
        ],
        [InlineKeyboardButton("🔄 Refresh Token", callback_data="refreshtoken")],
    ]

# ==== REFRESH/SEED-ADD: INPUT KONKURENSI ====
async def seed_add_prompt_conc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    # Simpan pilihan browser
    context.user_data["seed_add_browser"] = q.data  # 'show' atau 'headless'
    # Set flag menunggu input angka
    context.user_data["seed_add_waiting_conc"] = True
    await q.edit_message_text(
        "⚙️ Masukkan jumlah Chromium paralel (1-5):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]),
    )
    # Tetap di state REFRESH_CONFIRM
    return REFRESH_CONFIRM


async def seed_add_concurrency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Hanya proses jika memang sedang menunggu input concurrency
    if not context.user_data.get("seed_add_waiting_conc"):
        return REFRESH_CONFIRM

    text = (getattr(update, 'message', None) and update.message.text or "").strip()
    try:
        n = int(text)
        if n < 1 or n > 5:
            raise ValueError()
    except Exception:
        await update.message.reply_text(
            "❌ Input tidak valid. Masukkan angka 1-5:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]),
        )
        return REFRESH_CONFIRM

    # Simpan nilai dan reset flag
    context.user_data["seed_add_conc"] = n
    context.user_data["seed_add_waiting_conc"] = False

    # Tentukan mode headless dari pilihan browser sebelumnya
    browser_choice = context.user_data.get("seed_add_browser", "headless")
    headless = (browser_choice != "show")

    # Reset stop flag dan tampilkan tombol Stop
    try:
        context.application.bot_data["stop_refresh"] = False
    except Exception:
        pass

    await update.message.reply_text(
        "🚀 Menjalankan tambah akun via seed…",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Stop", callback_data="STOP_REFRESH")]]),
    )

    async def _runner(chat_id: int):
        try:
            await add_accounts_via_seed(
                headless=headless,
                context=context,
                log_func=None,
                stop_event=None,
                max_concurrency=int(context.user_data.get("seed_add_conc", 1)),
            )
        except Exception as e:
            try:
                await context.bot.send_message(chat_id=chat_id, text=f"❌ Error seed-add: {e}")
            except Exception:
                pass
        # Kembali ke menu utama
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="📍 Pilih menu:",
                reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
            )
        except Exception:
            pass

    # Jalankan task async
    context.application.create_task(_runner(update.message.chat.id))
    return REFRESH_CONFIRM


# ==== BACK HANDLER ====
async def claim_monthly_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    keyboard = [
        [InlineKeyboardButton("👁️ Tampilkan Browser", callback_data="show")],
        [InlineKeyboardButton("🕶️ Headless (default)", callback_data="headless")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    await q.edit_message_text(
        "🎁 Claim Monthly\n\nOpsi mode browser:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CLAIM_MONTHLY_CONFIRM


async def claim_monthly_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    headless = q.data != "show"

    await q.edit_message_text(
        "🚀 Menjalankan Claim Monthly…",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
        ),
    )

    async def _runner():
        try:
            await run_claim_monthly(context=context, visible=(not headless))
        except Exception as e:
            await context.bot.send_message(
                chat_id=q.message.chat.id, text=f"❌ Error Claim Monthly: {e}"
            )
        await context.bot.send_message(
            chat_id=q.message.chat.id,
            text="📍 Pilih menu:",
            reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
        )

    context.application.create_task(_runner())
    return ConversationHandler.END


async def kembali_ke_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text(
        "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
    )
    return ConversationHandler.END


# ==== CEK AKUN HANDLERS ====
async def cekakun_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    keyboard = [
        [InlineKeyboardButton("📋 Cek Nama Akun", callback_data="name")],
        [InlineKeyboardButton("💰 Cek Saldo", callback_data="saldo")],
        [InlineKeyboardButton("🎲 Cek Wager", callback_data="wd")],
        [InlineKeyboardButton("🏅 Cek Level", callback_data="level")],
        [InlineKeyboardButton("💸 Cek Monthly Bonus", callback_data="monthly_bonus")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    await q.edit_message_text(
        "📊 Pilih jenis informasi akun:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CEKAKUN_SORT


async def cekakun_sort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["cek_mode"] = q.data
    if q.data == "name":
        title = "Cek Nama Akun"
    elif q.data == "saldo":
        title = "Cek Saldo"
    elif q.data == "wd":
        title = "Cek Wager"
    elif q.data == "level":
        title = "Cek Level"
    else:
        title = "Cek Monthly Bonus"
    keyboard = [
        [InlineKeyboardButton("🚀 Mulai", callback_data="start_cek")],
        [InlineKeyboardButton("🔙 Batal", callback_data="back")],
    ]
    await q.edit_message_text(
        f"📊 Konfirmasi: {title}?", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CEKAKUN_RUNNING


async def cekakun_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    mode = context.user_data["cek_mode"]

    if mode == "name":
        akun = load_accounts()
        if not akun:
            await context.bot.send_message(chat_id=q.message.chat.id, text="❌ Tidak ada akun.")
        else:
            # Hitung panjang maksimal untuk padding yang rapi
            max_name_len = max(len(acc["name"]) for acc in akun) if akun else 10

            lines = []
            for i, acc in enumerate(akun):
                no = f"{i+1}.".ljust(
                    4
                )  # Nomor berdasarkan urutan array (konsisten dengan fitur lain)
                name = acc["name"].ljust(
                    max_name_len
                )  # Nama dengan padding sesuai panjang maksimal
                lines.append(f"{no} {name}")

            text = "<pre>" + html.escape("📋 Daftar Akun:\n\n" + "\n".join(lines)) + "</pre>"
            MAX_TG = 4096
            if len(text) <= MAX_TG:
                await context.bot.send_message(chat_id=q.message.chat.id, text=text, parse_mode="HTML")
            else:
                content = text
                if text.startswith("<pre>") and text.endswith("</pre>"):
                    content = text[5:-6]
                lines2 = content.split("\n")
                part1_lines = []
                for line in lines2:
                    candidate = "\n".join(part1_lines + [line])
                    if len(candidate) + 11 <= MAX_TG:
                        part1_lines.append(line)
                    else:
                        break
                part2_lines = lines2[len(part1_lines):]
                tmp2 = []
                for line in part2_lines:
                    candidate = "\n".join(tmp2 + [line])
                    if len(candidate) + 11 <= MAX_TG:
                        tmp2.append(line)
                    else:
                        break
                part2_lines = tmp2
                text1 = "<pre>" + "\n".join(part1_lines) + "</pre>"
                await context.bot.send_message(chat_id=q.message.chat.id, text=text1, parse_mode="HTML")
                if part2_lines:
                    text2 = "<pre>" + "\n".join(part2_lines) + "</pre>"
                    await context.bot.send_message(chat_id=q.message.chat.id, text=text2, parse_mode="HTML")
        await context.bot.send_message(
            chat_id=q.message.chat.id,
            text="📍 Pilih menu:",
            reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
        )
        return ConversationHandler.END

    # Mode monthly_bonus diproses sinkron (tanpa tombol stop khusus), tapi tetap kirim menu akhir
    if mode == "monthly_bonus":
        akun = load_accounts()
        if not akun:
            await context.bot.send_message(chat_id=q.message.chat.id, text="❌ Tidak ada akun.")
            await context.bot.send_message(
                chat_id=q.message.chat.id,
                text="📍 Pilih menu:",
                reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
            )
            return ConversationHandler.END
        # Jalankan cek monthly bonus secara paralel ringan (tanpa stop)
        try:
            await context.bot.send_message(
                chat_id=q.message.chat.id, text="⏳ Memproses cek Monthly Bonus..."
            )
            results = get_all_monthly_bonus(akun, max_workers=5, retries=1)
            # Simpan nama akun yang claimable (✅) ke monthly.txt
            try:
                claimable_names = [
                    (r.get("name") or "").strip()
                    for r in results
                    if r.get("success") and r.get("claimable") and (r.get("name") or "").strip()
                ]
                monthly_path = os.path.join(os.path.dirname(__file__), "monthly.txt")
                with open(monthly_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(claimable_names))
            except Exception as fe:
                await context.bot.send_message(
                    chat_id=q.message.chat.id, text=f"⚠️ Gagal menulis monthly.txt: {fe}"
                )
            report = format_monthly_bonus_report(results)
            # Antisipasi error "Message is too long" (batas Telegram 4096 char)
            MAX_TG = 4096
            if len(report) <= MAX_TG:
                await context.bot.send_message(
                    chat_id=q.message.chat.id, text=report, parse_mode="HTML"
                )
            else:
                # Ambil isi di dalam <pre> agar aman dipotong per baris
                content = report
                if report.startswith("<pre>") and report.endswith("</pre>"):
                    content = report[5:-6]
                lines = content.split("\n")

                # Bangun bagian 1 seoptimal mungkin agar selalu <= MAX_TG
                part1_lines = []
                for line in lines:
                    candidate = "\n".join(part1_lines + [line])
                    if len(candidate) + 11 <= MAX_TG:  # +11 untuk <pre></pre>
                        part1_lines.append(line)
                    else:
                        break

                # Sisa untuk bagian 2
                part2_lines = lines[len(part1_lines):]

                # Pangkas bagian 2 agar <= MAX_TG jika perlu
                tmp2 = []
                for line in part2_lines:
                    candidate = "\n".join(tmp2 + [line])
                    if len(candidate) + 11 <= MAX_TG:
                        tmp2.append(line)
                    else:
                        break
                part2_lines = tmp2

                # Kirim dua bagian
                text1 = "<pre>" + "\n".join(part1_lines) + "</pre>"
                await context.bot.send_message(
                    chat_id=q.message.chat.id, text=text1, parse_mode="HTML"
                )

                if part2_lines:
                    text2 = "<pre>" + "\n".join(part2_lines) + "</pre>"
                    await context.bot.send_message(
                        chat_id=q.message.chat.id, text=text2, parse_mode="HTML"
                    )
        except Exception as e:
            await context.bot.send_message(
                chat_id=q.message.chat.id, text=f"❌ Error monthly bonus: {e}"
            )
        # Always back to menu
        await context.bot.send_message(
            chat_id=q.message.chat.id,
            text="📍 Pilih menu:",
            reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
        )
        return ConversationHandler.END

    await q.edit_message_text(
        "📊 Memproses…",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⛔ Stop", callback_data="stop_cek")]]
        ),
    )
    context.application.bot_data["stop_cek"] = False

    async def _runner():
        akun = load_accounts()
        if not akun:
            await context.bot.send_message(chat_id=q.message.chat.id, text="❌ Tidak ada akun.")
            await context.bot.send_message(
                chat_id=q.message.chat.id,
                text="📍 Pilih menu:",
                reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
            )
            return

        data = []
        loop = asyncio.get_running_loop()

        for acc in akun:
            if context.application.bot_data.get("stop_cek"):
                await context.bot.send_message(
                    chat_id=q.message.chat.id, text="🚫 Proses dihentikan."
                )
                await context.bot.send_message(
                    chat_id=q.message.chat.id,
                    text="📍 Pilih menu:",
                    reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
                )
                return

            await asyncio.sleep(0)
            profile = await loop.run_in_executor(None, lambda: get_profile(acc["token"]))
            entry = {"name": acc["name"]}

            if mode == "saldo":
                entry["value"] = profile.get("wallet", 0)
            elif mode == "wd":
                entry["value"] = profile.get("wagerNeededForWithdraw", 0)
            elif mode == "level":
                vip = await loop.run_in_executor(None, lambda: get_vip(acc["token"]))
                level_str = vip.get("currentLevel", {}).get("name", "-")
                next_data = vip.get("nextLevel", {})
                exp_max = next_data.get("wagerNeeded", "N/A")
                exp = profile.get("wager", 0)
                try:
                    exp_str = f"{int(exp):,} / {int(exp_max):,}"
                except Exception:
                    exp_str = f"{int(exp):,} / N/A"
                entry["level"] = level_str
                entry["exp"] = exp_str
                entry["value"] = int(exp)

            data.append(entry)
            await asyncio.sleep(0)

        if context.application.bot_data.get("stop_cek"):
            return

        if mode == "level":
            data.sort(key=lambda x: -x["value"])
            header = "🏅 Cek Level & EXP:\n"

            # Hitung panjang maksimal untuk padding yang rapi
            max_name_len = max(len(d["name"]) for d in data) if data else 10
            max_level_len = max(len(str(d.get("level", "-"))) for d in data) if data else 5

            lines = []
            for i, d in enumerate(data):
                no = f"{i+1}.".ljust(4)  # Nomor dengan padding 4 karakter
                name = d["name"].ljust(max_name_len)  # Nama dengan padding sesuai panjang maksimal
                level = str(d.get("level", "-")).center(max_level_len)  # Level di tengah
                exp = d.get("exp", "0")
                lines.append(f"{no} {name} | Lv: {level} | EXP: {exp}")
        else:
            data.sort(key=lambda x: -x["value"])
            header = {
                "saldo": "💰 Cek Saldo:\n",
                "wd": "🎲 Cek Wager:\n",
            }[mode]

            # Hitung panjang maksimal untuk padding yang rapi
            max_name_len = max(len(d["name"]) for d in data) if data else 10
            max_value_len = max(len(f"{d['value']:.2f}") for d in data) if data else 8

            lines = []
            for i, d in enumerate(data):
                no = f"{i+1}.".ljust(4)  # Nomor dengan padding 4 karakter
                name = d["name"].ljust(max_name_len)  # Nama dengan padding sesuai panjang maksimal
                value = f"{d['value']:.2f}".rjust(max_value_len)  # Nilai rata kanan
                lines.append(f"{no} {name} | {value}")

        text = "<pre>" + html.escape(header + "\n" + "\n".join(lines)) + "</pre>"
        MAX_TG = 4096
        if len(text) <= MAX_TG:
            await context.bot.send_message(chat_id=q.message.chat.id, text=text, parse_mode="HTML")
        else:
            content = text
            if text.startswith("<pre>") and text.endswith("</pre>"):
                content = text[5:-6]
            lines2 = content.split("\n")
            part1_lines = []
            for line in lines2:
                candidate = "\n".join(part1_lines + [line])
                if len(candidate) + 11 <= MAX_TG:
                    part1_lines.append(line)
                else:
                    break
            part2_lines = lines2[len(part1_lines):]
            tmp2 = []
            for line in part2_lines:
                candidate = "\n".join(tmp2 + [line])
                if len(candidate) + 11 <= MAX_TG:
                    tmp2.append(line)
                else:
                    break
            part2_lines = tmp2
            text1 = "<pre>" + "\n".join(part1_lines) + "</pre>"
            await context.bot.send_message(chat_id=q.message.chat.id, text=text1, parse_mode="HTML")
            if part2_lines:
                text2 = "<pre>" + "\n".join(part2_lines) + "</pre>"
                await context.bot.send_message(chat_id=q.message.chat.id, text=text2, parse_mode="HTML")

        # Jika mode level, cek apakah ada akun level 0 dengan token valid
        if mode == "level":
            level_0_accounts = []
            for d in data:
                if d.get("level") == "0" or d.get("level") == 0:
                    # Pastikan token valid (bukan yang invalid)
                    akun_match = None
                    for acc in akun:
                        if acc["name"] == d["name"]:
                            akun_match = acc
                            break

                    if akun_match:
                        # Cek apakah token valid
                        try:
                            token_valid, reason = validate_token_requests_fast(akun_match["token"])
                            if token_valid:
                                level_0_accounts.append(d["name"])
                        except:
                            pass

            if level_0_accounts:
                keyboard = [
                    [InlineKeyboardButton("🗑️ Hapus Semua Level 0", callback_data="hapus_level_0")],
                    [InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")],
                ]

                level_0_text = "\n".join([f"• {name}" for name in level_0_accounts])
                await context.bot.send_message(
                    chat_id=q.message.chat.id,
                    text=f"🔍 Ditemukan {len(level_0_accounts)} akun Level 0 dengan token valid:\n\n{level_0_text}\n\nIngin menghapus semua akun Level 0?",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

                # Simpan data level 0 accounts untuk digunakan nanti
                context.application.bot_data["level_0_accounts"] = level_0_accounts
                return ConversationHandler.END

        await context.bot.send_message(
            chat_id=q.message.chat.id,
            text="📍 Pilih menu:",
            reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
        )

    context.application.create_task(_runner())
    context.application.bot_data["cek_cek_task"] = True
    return ConversationHandler.END


async def stop_cek_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.application.bot_data["stop_cek"] = True
    await q.message.reply_text("🚫 Proses dihentikan.")
    await q.message.reply_text(
        "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
    )
    return ConversationHandler.END


async def fitur_utama_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    keyboard = [
        [InlineKeyboardButton("🚀 Upgrader", callback_data="upgrader")],
        [InlineKeyboardButton("🎡 WHEEL", callback_data="wheel")],
        [InlineKeyboardButton("🌧️ Auto Rain", callback_data="auto_rain")],
        [InlineKeyboardButton("🎁 Klaim Kupon", callback_data="klaimkupon")],
        [InlineKeyboardButton("🎁 Claim Monthly", callback_data="claim_monthly_menu")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="main_menu")],
    ]
    await q.edit_message_text("🧰 Fitur Utama:\n", reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END


claimmonthly_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(claim_monthly_start, pattern="^claim_monthly_menu$")],
    states={
        CLAIM_MONTHLY_CONFIRM: [
            CallbackQueryHandler(claim_monthly_execute, pattern="^(show|headless)$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
    },
    fallbacks=[CallbackQueryHandler(kembali_ke_menu, pattern="^back$")],
    per_chat=True,
)


cekakun_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(cekakun_start, pattern="^cekakun$")],
    states={
        CEKAKUN_SORT: [
            CallbackQueryHandler(cekakun_sort, pattern="^(name|saldo|wd|level|monthly_bonus)$")
        ],
        CEKAKUN_RUNNING: [CallbackQueryHandler(cekakun_execute, pattern="^start_cek$")],
    },
    fallbacks=[CallbackQueryHandler(kembali_ke_menu, pattern="^back$")],
    per_chat=True,
)


# ==== WHEEL HANDLERS ====
async def wheel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("x2", callback_data="2"),
            InlineKeyboardButton("x3", callback_data="3"),
        ],
        [
            InlineKeyboardButton("x5", callback_data="5"),
            InlineKeyboardButton("x50", callback_data="50"),
        ],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    text = "🌁 Masukkan multiplier:"
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        q = update.callback_query
        await q.answer()
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return WHEEL_MULT


async def wheel_mult(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["multiplier"] = int(q.data)
    keyboard = [
        [InlineKeyboardButton("All-in saldo", callback_data="allin")],
        [InlineKeyboardButton("Bet manual", callback_data="manual")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    await q.edit_message_text("💰 Pilih metode bet:", reply_markup=InlineKeyboardMarkup(keyboard))
    return WHEEL_BET_TYPE


async def wheel_bet_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["bet_type"] = q.data
    if q.data == "manual":
        await q.edit_message_text(
            "✍️ Masukkan jumlah bet (misal: 0.1):",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
            ),
        )
        return WHEEL_BET_VALUE
    return await wheel_browser_prompt(q, context)


async def wheel_bet_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["bet_manual"] = update.message.text.strip()
    return await wheel_browser_prompt(update, context)


async def wheel_browser_prompt(update_or_query, context):
    keyboard = [
        [
            InlineKeyboardButton("Tampilkan", callback_data="show"),
            InlineKeyboardButton("Tidak tampilkan", callback_data="headless"),
        ],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    text = "👁️ Pilih mode browser:"
    if hasattr(update_or_query, "edit_message_text"):
        await update_or_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update_or_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return WHEEL_BROWSER


async def wheel_browser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["browser"] = q.data

    keyboard = [
        [InlineKeyboardButton("🔍 Filter Saldo", callback_data="filter_saldo")],
        [InlineKeyboardButton("📋 Semua Akun", callback_data="all_accounts")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    await q.edit_message_text("💰 Pilih filter akun:", reply_markup=InlineKeyboardMarkup(keyboard))
    return WHEEL_FILTER


async def wheel_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "filter_saldo":
        await q.edit_message_text("🔍 Mengecek saldo akun...", reply_markup=None)

        # Cek saldo semua akun
        akun = load_accounts()
        if not akun:
            await q.edit_message_text("❌ Tidak ada akun.")
            return ConversationHandler.END

        akun_dengan_saldo = []
        akun_tanpa_saldo = []

        for acc in akun:
            try:
                saldo = get_balance(acc["token"])
                if saldo >= 0.1:
                    akun_dengan_saldo.append({"name": acc["name"], "saldo": saldo})
                else:
                    akun_tanpa_saldo.append({"name": acc["name"], "saldo": saldo})
            except:
                akun_tanpa_saldo.append({"name": acc["name"], "saldo": 0})

        # Tampilkan hasil ringkas (hanya jumlah, tanpa daftar nama)
        text = (
            "💰 Hasil Filter Saldo:\n\n"
            f"✅ Akun dengan saldo ≥ $0.1: <b>{len(akun_dengan_saldo)}</b>\n"
            f"❌ Akun dengan saldo < $0.1: <b>{len(akun_tanpa_saldo)}</b>\n"
        )

        # Simpan akun yang akan dijalankan
        context.user_data["filtered_accounts"] = [acc["name"] for acc in akun_dengan_saldo]

        keyboard = []
        if akun_dengan_saldo:
            keyboard.append(
                [InlineKeyboardButton("🚀 Jalankan Akun Bersaldo", callback_data="run_filtered")]
            )
        keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="back")])

        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        return WHEEL_CONFIRM

    elif q.data == "all_accounts":
        context.user_data["filtered_accounts"] = None  # Jalankan semua akun
        mult = context.user_data["multiplier"]
        btype = context.user_data["bet_type"]
        amount = context.user_data.get("bet_manual", "All-in")
        headless = context.user_data["browser"] == "headless"

        msg = (
            f"🚀 Siap menjalankan auto bet dengan pengaturan:\n"
            f"• Multiplier: x{mult}\n"
            f"• Metode Bet: {btype}\n"
            f"• Jumlah: {amount}\n"
            f"• Mode Browser: {'Headless' if headless else 'Visible'}\n"
            f"• Filter: Semua Akun\n\n"
            "Konfirmasi?"
        )
        keyboard = [
            [
                InlineKeyboardButton("✅ Ya", callback_data="confirm"),
                InlineKeyboardButton("❌ Batal", callback_data="cancel"),
            ],
            [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
        ]
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return WHEEL_CONFIRM


async def wheel_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🎰 Menjalankan auto bet…",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⛔ Stop", callback_data="stop_bet")]]
        ),
    )
    context.application.bot_data["stop_bet"] = False

    async def _runner():
        context._chat_id = q.message.chat.id

        # Tentukan akun yang akan dijalankan berdasarkan filter
        akun_list = None
        filtered_names = context.user_data.get("filtered_accounts")
        if filtered_names is not None:  # Jika ada filter saldo
            all_accounts = load_accounts()
            akun_list = [acc for acc in all_accounts if acc["name"] in filtered_names]
            if akun_list:
                await context.bot.send_message(
                    chat_id=q.message.chat.id,
                    text=f"🔍 Menjalankan {len(akun_list)} akun bersaldo dari {len(all_accounts)} total akun",
                )

        await jalankan_auto_bet(
            context.user_data["multiplier"],
            context.user_data.get("bet_manual", "0"),
            context.user_data["bet_type"] == "allin",
            context.user_data["browser"] == "headless",
            context,
            akun_list=akun_list,  # Pass filtered accounts
        )
        await context.bot.send_message(
            chat_id=q.message.chat.id,
            text="📍 Pilih menu:",
            reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
        )

    context.application.create_task(_runner())
    return ConversationHandler.END


async def wheel_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("❌ Auto bet dibatalkan.")
    await q.message.reply_text(
        "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
    )
    return ConversationHandler.END


async def stop_bet_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.application.bot_data["stop_bet"] = True
    await q.edit_message_text("🚫 Auto bet dihentikan via tombol.")
    await q.message.reply_text(
        "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
    )


wheel_conv = ConversationHandler(
    entry_points=[
        CommandHandler("wheel", wheel_start),
        CallbackQueryHandler(wheel_start, pattern="^wheel$"),
    ],
    states={
        WHEEL_MULT: [
            CallbackQueryHandler(wheel_mult, pattern="^(2|3|5|50)$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        WHEEL_BET_TYPE: [
            CallbackQueryHandler(wheel_bet_type, pattern="^(allin|manual)$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        WHEEL_BET_VALUE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, wheel_bet_value),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        WHEEL_BROWSER: [
            CallbackQueryHandler(wheel_browser, pattern="^(show|headless)$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        WHEEL_FILTER: [
            CallbackQueryHandler(wheel_filter, pattern="^(filter_saldo|all_accounts)$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        WHEEL_CONFIRM: [
            CallbackQueryHandler(wheel_confirm, pattern="^(confirm|run_filtered)$"),
            CallbackQueryHandler(wheel_cancel, pattern="^cancel$"),
            CallbackQueryHandler(stop_bet_handler, pattern="^stop_bet$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
    },
    fallbacks=[CallbackQueryHandler(kembali_ke_menu, pattern="^back$")],
    per_chat=True,
)


# ==== AUTO RAIN HANDLERS ====
async def auto_rain_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    # Guard fitur Rain dan Maintenance
    try:
        if not is_enabled('rain') or is_enabled('maintenance'):
            await q.message.reply_text(
                "🚧 Fitur Auto Rain: coming soon",
                reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
            )
            return ConversationHandler.END
    except Exception:
        pass
    akun = load_rain_accounts() or []
    kb = [
        [InlineKeyboardButton("▶️ Mulai Auto Rain", callback_data="rain_start")],
        [InlineKeyboardButton("🛠️ Kelola Akun Rain", callback_data="rain_manage")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    await q.edit_message_text(
        f"🌧️ Auto Rain\n\n📦 Akun rain: {len(akun)}\n\nPilih aksi:",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return RAIN_MENU


def _rain_manage_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Tambah", callback_data="rain_add")],
            [InlineKeyboardButton("✏️ Edit", callback_data="rain_edit")],
            [InlineKeyboardButton("🗑️ Hapus", callback_data="rain_delete")],
            [InlineKeyboardButton("📋 Lihat Daftar", callback_data="rain_list")],
            [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
        ]
    )


async def auto_rain_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "rain_start":
        kb = [
            [InlineKeyboardButton("👁️ Tampilkan Browser", callback_data="show")],
            [InlineKeyboardButton("🕶️ Headless", callback_data="headless")],
            [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
        ]
        await q.edit_message_text(
            "👁️ Pilih mode browser:", reply_markup=InlineKeyboardMarkup(kb)
        )
        return RAIN_BROWSER
    elif q.data == "rain_manage":
        akun = load_rain_accounts() or []
        await q.edit_message_text(
            f"🛠️ Kelola Akun Rain\n\n📦 Total: {len(akun)}\nPilih aksi:",
            reply_markup=_rain_manage_keyboard(),
        )
        return RAIN_MANAGE
    else:
        return await kembali_ke_menu(update, context)


async def auto_rain_browser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["rain_browser"] = q.data  # 'show' or 'headless'
    await q.edit_message_text(
        "⚙️ Masukkan jumlah Chromium paralel (1-5):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]),
    )
    return RAIN_CONCURRENCY


async def auto_rain_concurrency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    try:
        n = int(text)
        if n < 1 or n > 5:
            raise ValueError()
    except Exception:
        await update.message.reply_text(
            "❌ Input tidak valid. Masukkan angka 1-5:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]),
        )
        return RAIN_CONCURRENCY
    context.user_data["rain_conc"] = n
    await update.message.reply_text(
        "🔑 Masukkan CapSolver API key (atau ketik - untuk lewati):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]),
    )
    return RAIN_CAPSOLVER


async def auto_rain_capsolver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = (update.message.text or "").strip()
    context.user_data["rain_capsolver"] = None if key in ("", "-") else key
    visible = context.user_data.get("rain_browser") != "headless"
    conc = context.user_data.get("rain_conc", 2)
    msg = (
        "📋 Konfirmasi Auto Rain:\n"
        f"• Browser: {'Visible' if visible else 'Headless'}\n"
        f"• Chromium paralel: {conc}\n"
        f"• CapSolver: {'Lewati' if not context.user_data.get('rain_capsolver') else 'Digunakan'}\n\n"
        "Mulai sekarang?"
    )
    kb = [
        [InlineKeyboardButton("🚀 Mulai", callback_data="rain_go")],
        [InlineKeyboardButton("🔙 Batal", callback_data="back")],
    ]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb))
    return RAIN_CONFIRM


async def auto_rain_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    visible = context.user_data.get("rain_browser") != "headless"
    conc = int(context.user_data.get("rain_conc", 2))
    capkey = context.user_data.get("rain_capsolver")

    await q.edit_message_text(
        "🌧️ Menjalankan Auto Rain…",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Stop", callback_data="stop_rain")]]),
    )
    context.application.bot_data["stop_rain"] = False

    async def _runner():
        try:
            await jalankan_auto_rain(
                context=context,
                visible=visible,
                akun_list=None,
                stop_event=None,
                log_func=None,
                capsolver_api_key=capkey,
                max_concurrency=conc,
            )
        except Exception as e:
            await context.bot.send_message(chat_id=q.message.chat.id, text=f"❌ Error Auto Rain: {e}")
        await context.bot.send_message(
            chat_id=q.message.chat.id,
            text="📍 Pilih menu:",
            reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
        )

    context.application.create_task(_runner())
    return ConversationHandler.END


async def stop_rain_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.application.bot_data["stop_rain"] = True
    await q.edit_message_text("🚫 Auto Rain dihentikan via tombol.")
    await q.message.reply_text("📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()))
    return ConversationHandler.END


# Kelola Akun Rain
async def rain_manage_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    akun = load_rain_accounts() or []
    await q.edit_message_text(
        f"🛠️ Kelola Akun Rain\n\n📦 Total: {len(akun)}\nPilih aksi:",
        reply_markup=_rain_manage_keyboard(),
    )
    return RAIN_MANAGE


async def rain_list_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    akun = load_rain_accounts() or []
    if not akun:
        await q.edit_message_text("❌ Tidak ada akun rain.", reply_markup=_rain_manage_keyboard())
        return RAIN_MANAGE
    lines = [f"{i+1}. {a['name']}" for i, a in enumerate(akun)]
    await q.edit_message_text("📋 Daftar Akun Rain:\n" + "\n".join(lines), reply_markup=_rain_manage_keyboard())
    return RAIN_MANAGE


async def rain_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "📝 Masukkan nama akun rain:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]),
    )
    return RAIN_ADD_NAME


async def rain_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nama = (update.message.text or "").strip()
    if not nama:
        await update.message.reply_text(
            "❌ Nama kosong. Masukkan nama akun rain:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]),
        )
        return RAIN_ADD_NAME
    context.user_data["rain_new_name"] = nama
    await update.message.reply_text(
        "🔑 Masukkan token akun:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]),
    )
    return RAIN_ADD_TOKEN


async def rain_add_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = (update.message.text or "").strip()
    nama = context.user_data.get("rain_new_name", "")
    if not token:
        await update.message.reply_text(
            "❌ Token kosong. Masukkan token akun:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]),
        )
        return RAIN_ADD_TOKEN
    akun = load_rain_accounts() or []
    # Cek duplikasi nama
    for a in akun:
        if a.get("name", "").lower() == nama.lower():
            await update.message.reply_text(
                f"❌ Nama '{nama}' sudah ada. Masukkan nama lain:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]),
            )
            return RAIN_ADD_NAME
    akun.append({"name": nama, "token": token})
    save_rain_accounts(akun)
    await update.message.reply_text(
        f"✅ Akun rain '{nama}' ditambahkan.",
        reply_markup=_rain_manage_keyboard(),
    )
    return RAIN_MANAGE


async def rain_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    akun = load_rain_accounts() or []
    if not akun:
        await q.edit_message_text("❌ Tidak ada akun rain.", reply_markup=_rain_manage_keyboard())
        return RAIN_MANAGE
    kb = [[InlineKeyboardButton(f"{i+1}. {a['name']}", callback_data=f"rain_edit_sel_{i}")] for i, a in enumerate(akun)]
    kb.append([InlineKeyboardButton("🔙 Kembali", callback_data="back")])
    await q.edit_message_text("✏️ Pilih akun untuk diedit:", reply_markup=InlineKeyboardMarkup(kb))
    return RAIN_EDIT_SELECT


async def rain_edit_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    m = re.match(r"^rain_edit_sel_(\d+)$", q.data)
    if not m:
        return await rain_manage_start(update, context)
    idx = int(m.group(1))
    context.user_data["rain_edit_idx"] = idx
    await q.edit_message_text(
        "✏️ Masukkan nama baru (kosong = tidak diubah):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]),
    )
    return RAIN_EDIT_NAME


async def rain_edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["rain_edit_name_val"] = (update.message.text or "").strip()
    await update.message.reply_text(
        "🔑 Masukkan token baru (kosong = tidak diubah):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]),
    )
    return RAIN_EDIT_TOKEN


async def rain_edit_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_token = (update.message.text or "").strip()
    idx = context.user_data.get("rain_edit_idx", -1)
    akun = load_rain_accounts() or []
    if idx < 0 or idx >= len(akun):
        await update.message.reply_text("❌ Indeks tidak valid.", reply_markup=_rain_manage_keyboard())
        return RAIN_MANAGE
    new_name = (context.user_data.get("rain_edit_name_val") or "").strip()
    if new_name:
        akun[idx]["name"] = new_name
    if new_token:
        akun[idx]["token"] = new_token
    save_rain_accounts(akun)
    await update.message.reply_text("✅ Akun rain diperbarui!", reply_markup=_rain_manage_keyboard())
    return RAIN_MANAGE


async def rain_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    akun = load_rain_accounts() or []
    if not akun:
        await q.edit_message_text("❌ Tidak ada akun rain.", reply_markup=_rain_manage_keyboard())
        return RAIN_MANAGE
    kb = [[InlineKeyboardButton(f"Hapus {i+1}. {a['name']}", callback_data=f"rain_del_sel_{i}")] for i, a in enumerate(akun)]
    kb.append([InlineKeyboardButton("🔙 Kembali", callback_data="back")])
    await q.edit_message_text("🗑️ Pilih akun yang ingin dihapus:", reply_markup=InlineKeyboardMarkup(kb))
    return RAIN_DELETE_SELECT


async def rain_delete_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    m = re.match(r"^rain_del_sel_(\d+)$", q.data)
    if not m:
        return await rain_manage_start(update, context)
    idx = int(m.group(1))
    akun = load_rain_accounts() or []
    if 0 <= idx < len(akun):
        nama = akun[idx]["name"]
        del akun[idx]
        save_rain_accounts(akun)
        await q.edit_message_text(f"✅ Akun rain '{nama}' dihapus.", reply_markup=_rain_manage_keyboard())
        return RAIN_MANAGE
    else:
        await q.edit_message_text("❌ Indeks tidak valid.", reply_markup=_rain_manage_keyboard())
        return RAIN_MANAGE


auto_rain_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(auto_rain_start, pattern="^auto_rain$")],
    states={
        RAIN_MENU: [
            CallbackQueryHandler(auto_rain_menu, pattern="^(rain_start|rain_manage)$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        RAIN_BROWSER: [
            CallbackQueryHandler(auto_rain_browser, pattern="^(show|headless)$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        RAIN_CONCURRENCY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, auto_rain_concurrency),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        RAIN_CAPSOLVER: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, auto_rain_capsolver),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        RAIN_CONFIRM: [
            CallbackQueryHandler(auto_rain_execute, pattern="^rain_go$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        RAIN_MANAGE: [
            CallbackQueryHandler(rain_manage_start, pattern="^rain_manage$"),
            CallbackQueryHandler(rain_add_start, pattern="^rain_add$"),
            CallbackQueryHandler(rain_edit_start, pattern="^rain_edit$"),
            CallbackQueryHandler(rain_delete_start, pattern="^rain_delete$"),
            CallbackQueryHandler(rain_list_show, pattern="^rain_list$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        RAIN_ADD_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, rain_add_name),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        RAIN_ADD_TOKEN: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, rain_add_token),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        RAIN_EDIT_SELECT: [
            CallbackQueryHandler(rain_edit_select, pattern="^rain_edit_sel_\\d+$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        RAIN_EDIT_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, rain_edit_name),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        RAIN_EDIT_TOKEN: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, rain_edit_token),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        RAIN_DELETE_SELECT: [
            CallbackQueryHandler(rain_delete_select, pattern="^rain_del_sel_\\d+$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
    },
    fallbacks=[CallbackQueryHandler(kembali_ke_menu, pattern="^back$")],
    per_chat=True,
)

# ==== KLAIM KUPON HANDLERS ====
async def klaimkupon_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Semua", callback_data="all")],
        [
            InlineKeyboardButton("5 Akun", callback_data="5"),
            InlineKeyboardButton("10 Akun", callback_data="10"),
        ],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    if update.message:
        await update.message.reply_text(
            "👥 Pilih jumlah akun:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        q = update.callback_query
        await q.answer()
        await q.edit_message_text(
            "👥 Pilih jumlah akun:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    return KLAIM_JUMLAH


async def klaimkupon_jumlah(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["jumlah"] = q.data
    context.user_data["delay"] = 0  # Set default delay ke 0

    # Tampilkan pilihan kupon harian dan input manual
    keyboard = [
        [
            InlineKeyboardButton("📅 Monday", callback_data="monday"),
            InlineKeyboardButton("📅 Tuesday", callback_data="tuesday"),
        ],
        [
            InlineKeyboardButton("📅 Wednesday", callback_data="wednesday"),
            InlineKeyboardButton("📅 Thursday", callback_data="thursday"),
        ],
        [
            InlineKeyboardButton("📅 Friday", callback_data="friday"),
            InlineKeyboardButton("📅 Saturday", callback_data="saturday"),
        ],
        [InlineKeyboardButton("📅 Sunday", callback_data="sunday")],
        [InlineKeyboardButton("✍️ Input Manual", callback_data="manual_input")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]

    await q.edit_message_text(
        "🎁 Pilih kupon harian atau input manual:\n\n"
        "📅 <b>Kupon Harian:</b>\n"
        "• Monday - Sunday: Kode kupon otomatis\n\n"
        "✍️ <b>Input Manual:</b>\n"
        "• Masukkan kode kupon sendiri",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return KLAIM_PILIH_KUPON


async def klaimkupon_pilih_kupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "manual_input":
        # Jika pilih input manual, minta user masukkan kode
        await q.edit_message_text(
            "✍️ Masukkan kode kupon manual:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
            ),
        )
        return KLAIM_KODE
    else:
        # Jika pilih kupon harian, set kode otomatis
        daily_coupons = {
            "monday": "monday",
            "tuesday": "tuesday",
            "wednesday": "wednesday",
            "thursday": "thursday",
            "friday": "friday",
            "saturday": "saturday",
            "sunday": "sunday",
        }

        selected_day = q.data.lower()
        if selected_day in daily_coupons:
            context.user_data["kode"] = daily_coupons[selected_day]

            # Tampilkan konfirmasi
            jumlah = context.user_data["jumlah"]
            delay = context.user_data["delay"]
            kode = context.user_data["kode"]

            keyboard = [
                [InlineKeyboardButton("🚀 Mulai klaim", callback_data="start_claim")],
                [InlineKeyboardButton("🔙 Batal", callback_data="back")],
            ]

            await q.edit_message_text(
                f"📋 Konfirmasi klaim kupon harian:\n\n"
                f"📅 Hari: <b>{selected_day.title()}</b>\n"
                f"🏷️ Kode: <b>{kode}</b>\n"
                f"👥 Jumlah akun: <b>{jumlah}</b>\n"
                f"⏱️ Delay: <b>{delay}s</b>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML",
            )
            return KLAIM_CONFIRM
        else:
            await q.edit_message_text("❌ Pilihan tidak valid.")
            return ConversationHandler.END


async def klaimkupon_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        return await update.message.reply_text(
            "❌ Masukkan angka.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
            ),
        )
    context.user_data["delay"] = int(text)
    await update.message.reply_text(
        "🏷️ Masukkan kode kupon:",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
        ),
    )
    return KLAIM_KODE


async def klaimkupon_kode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["kode"] = update.message.text.strip()
    jumlah = context.user_data["jumlah"]
    delay = context.user_data["delay"]
    kode = context.user_data["kode"]
    keyboard = [
        [InlineKeyboardButton("🚀 Mulai klaim", callback_data="start_claim")],
        [InlineKeyboardButton("🔙 Batal", callback_data="back")],
    ]
    await update.message.reply_text(
        f"📋 Konfirmasi klaim kupon:\n"
        f"• Jumlah akun: {jumlah}\n"
        f"• Delay: {delay}s\n"
        f"• Kode: {kode}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return KLAIM_CONFIRM


async def klaimkupon_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    jumlah = context.user_data["jumlah"]
    delay = context.user_data["delay"]
    kode = context.user_data["kode"]
    await q.edit_message_text(
        "🚀 Menjalankan klaim…",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⛔ Stop", callback_data="stop_claim")]]
        ),
    )
    context.application.bot_data["stop_claim"] = False

    async def _runner():
        akun_list = load_accounts()
        target = akun_list if jumlah == "all" else akun_list[: int(jumlah)]

        for i, acc in enumerate(target, 1):
            if context.application.bot_data.get("stop_claim"):
                await context.bot.send_message(
                    chat_id=q.message.chat.id, text="🚫 Klaim dihentikan."
                )
                await context.bot.send_message(
                    chat_id=q.message.chat.id,
                    text="📍 Pilih menu:",
                    reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
                )
                return

            # Tampilkan loading message terlebih dahulu
            try:
                saldo = get_balance(acc["token"])
                loading_msg = f"[{i}/{len(target)}] {acc['name']} (💰 {saldo:.4f}) ⏳ Loading..."
                await context.bot.send_message(chat_id=q.message.chat.id, text=loading_msg)
            except:
                loading_msg = f"[{i}/{len(target)}] {acc['name']} (💰 0.0000) ⏳ Loading..."
                await context.bot.send_message(chat_id=q.message.chat.id, text=loading_msg)

            # Pre-check token dengan requests
            token_valid, reason = validate_token_requests_fast(acc["token"])
            if not token_valid:
                # Tampilkan error di bawah loading message (lebih informatif)
                error_msg = f"❌ Token tidak valid: {reason}"
                await context.bot.send_message(chat_id=q.message.chat.id, text=error_msg)
                await asyncio.sleep(0.2)
                continue

            # Cek level akun dengan cara yang sama seperti di fitur cek akun
            try:
                vip = get_vip(acc["token"])

                if vip and vip.get("currentLevel"):
                    level_name = vip.get("currentLevel", {}).get("name", "0")

                    # Level name sudah berupa angka langsung (seperti "2", "7", "6")
                    try:
                        level = int(level_name)
                    except Exception:
                        level = 0
                else:
                    level = 0
            except Exception:
                level = 0

            # Sistem loop untuk akun yang valid
            max_attempts = 5 if level >= 1 else 1  # Loop 5x jika level >= 1, sekali jika level 0
            final_status = ""

            for attempt in range(max_attempts):
                if context.application.bot_data.get("stop_claim"):
                    await context.bot.send_message(
                        chat_id=q.message.chat.id, text="🚫 Klaim dihentikan."
                    )
                    return

                try:
                    res = requests.post(
                        API_REDEEM_URL,
                        headers={
                            "x-auth-token": acc["token"],
                            "User-Agent": "Mozilla/5.0",
                            "Content-Type": "application/json",
                        },
                        json={"code": kode},
                        timeout=10,
                    )

                    # Format hasil di bawah loading message
                    if res.status_code == 200:
                        final_status = "✅ Sukses klaim kupon!"
                        break
                    elif res.status_code == 403:
                        final_status = "⚠️ Sudah klaim kupon hari ini"
                        break
                    elif res.status_code == 400:
                        # Cek response body untuk detail error
                        try:
                            error_detail = res.json()
                            error_msg = error_detail.get("message", "Kode kupon salah")
                        except:
                            error_msg = "Kode kupon salah"
                        final_status = f"❌ {error_msg}"
                        break
                    elif res.status_code == 401:
                        final_status = "❌ Token expired"
                        break
                    elif res.status_code == 429:
                        # Rate limit - tunggu dan retry
                        if attempt < max_attempts - 1:
                            await asyncio.sleep(5)
                            continue
                        else:
                            final_status = "❌ Rate limit exceeded"
                            break
                    elif res.status_code >= 500:
                        # Server error - retry
                        if attempt < max_attempts - 1:
                            await asyncio.sleep(2)
                            continue
                        else:
                            final_status = "❌ Server error"
                            break
                    else:
                        # Unknown error - retry jika level >= 1
                        if level >= 1 and attempt < max_attempts - 1:
                            await asyncio.sleep(2)
                            continue
                        else:
                            final_status = f"❌ HTTP {res.status_code}"
                            break

                except requests.exceptions.Timeout:
                    if level >= 1 and attempt < max_attempts - 1:
                        await asyncio.sleep(3)
                        continue
                    else:
                        final_status = "❌ Request timeout"
                        break
                except requests.exceptions.ConnectionError:
                    if level >= 1 and attempt < max_attempts - 1:
                        await asyncio.sleep(3)
                        continue
                    else:
                        final_status = "❌ Connection error"
                        break
                except Exception as e:
                    if level >= 1 and attempt < max_attempts - 1:
                        await asyncio.sleep(2)
                        continue
                    else:
                        final_status = f"❌ Error: {str(e)[:20]}"
                        break

                # Delay antar attempt jika akan retry
                if attempt < max_attempts - 1:
                    await asyncio.sleep(1)

            # Kirim hasil final untuk akun ini sebagai pesan biasa
            if final_status:
                await context.bot.send_message(chat_id=q.message.chat.id, text=final_status)

            # Delay antar akun (kecuali akun terakhir)
            if i < len(target):
                if delay > 0:
                    await asyncio.sleep(delay)

        await context.bot.send_message(
            chat_id=q.message.chat.id, text="🎉 Semua akun selesai diproses!"
        )
        await context.bot.send_message(
            chat_id=q.message.chat.id,
            text="📍 Pilih menu:",
            reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
        )

    context.application.create_task(_runner())
    return ConversationHandler.END


async def stop_claim_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.application.bot_data["stop_claim"] = True
    await q.edit_message_text("🚫 Klaim dihentikan via tombol.")
    await q.message.reply_text(
        "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
    )
    return ConversationHandler.END


klaim_conv = ConversationHandler(
    entry_points=[
        CommandHandler("klaimkupon", klaimkupon_start),
        CallbackQueryHandler(klaimkupon_start, pattern="^klaimkupon$"),
    ],
    states={
        KLAIM_JUMLAH: [
            CallbackQueryHandler(klaimkupon_jumlah, pattern="^(all|5|10)$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        KLAIM_PILIH_KUPON: [
            CallbackQueryHandler(
                klaimkupon_pilih_kupon,
                pattern="^(monday|tuesday|wednesday|thursday|friday|saturday|sunday|manual_input)$",
            ),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        KLAIM_DELAY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, klaimkupon_delay),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        KLAIM_KODE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, klaimkupon_kode),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        KLAIM_CONFIRM: [
            CallbackQueryHandler(klaimkupon_execute, pattern="^start_claim$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
    },
    fallbacks=[CallbackQueryHandler(kembali_ke_menu, pattern="^back$")],
    per_chat=True,
)


# ==== TAMBAH AKUN HANDLERS ====
async def tambahakun_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "📝 Masukkan nama akun:",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
        ),
    )
    return TAMBAH_NAMA


async def tambahakun_nama(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nama_baru"] = update.message.text.strip()
    await update.message.reply_text(
        "🔑 Masukkan token akun:",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
        ),
    )
    return TAMBAH_TOKEN


async def tambahakun_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = update.message.text.strip()
    nama = context.user_data.get("nama_baru", "Unknown")
    akun = load_accounts()
    akun.append({"name": nama, "token": token})
    save_accounts(akun)
    await update.message.reply_text(
        f"✅ Akun '{nama}' berhasil ditambahkan.\n\n📝 Masukkan nama akun selanjutnya:",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
        ),
    )
    return TAMBAH_NAMA


tambah_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(tambahakun_start, pattern="^tambahakun$")],
    states={
        TAMBAH_NAMA: [MessageHandler(filters.TEXT & ~filters.COMMAND, tambahakun_nama)],
        TAMBAH_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, tambahakun_token)],
    },
    fallbacks=[CallbackQueryHandler(kembali_ke_menu, pattern="^back$")],
    per_chat=True,
)


# ==== HAPUS AKUN HANDLERS ====
async def hapusakun_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    akun = load_accounts()
    if not akun:
        await q.edit_message_text("❌ Tidak ada akun.")
        return ConversationHandler.END
    keyboard = [
        [InlineKeyboardButton(acc["name"], callback_data=str(i))] for i, acc in enumerate(akun)
    ]
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="back")])
    await q.edit_message_text(
        "🗑️ Pilih akun yang ingin dihapus:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return HAPUS_PILIH


async def hapusakun_pilih(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "back":
        return await kembali_ke_menu(update, context)
    idx = int(q.data)
    akun = load_accounts()
    if 0 <= idx < len(akun):
        nama = akun[idx]["name"]
        del akun[idx]
        save_accounts(akun)
        await q.edit_message_text(f"✅ Akun '{nama}' dihapus.")
    else:
        await q.edit_message_text("❌ Akun tidak ditemukan.")
    akun = load_accounts()
    if akun:
        keyboard = [
            [InlineKeyboardButton(a["name"], callback_data=str(i))] for i, a in enumerate(akun)
        ]
        keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="back")])
        await q.message.reply_text(
            "🗑️ Pilih akun yang ingin dihapus:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return HAPUS_PILIH
    else:
        await q.message.reply_text("❌ Tidak ada akun.")
        await q.message.reply_text(
            "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
        )
        return ConversationHandler.END


hapus_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(hapusakun_start, pattern="^hapusakun$")],
    states={
        HAPUS_PILIH: [
            CallbackQueryHandler(hapusakun_pilih, pattern="^\\d+$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ]
    },
    fallbacks=[CallbackQueryHandler(kembali_ke_menu, pattern="^back$")],
    per_chat=True,
)


# ==== EDIT AKUN HANDLERS ====
async def editakun_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    akun = load_accounts()
    if not akun:
        await q.edit_message_text("❌ Tidak ada akun.")
        return ConversationHandler.END
    keyboard = [
        [InlineKeyboardButton(f"{i+1}. {acc['name']}", callback_data=str(i))]
        for i, acc in enumerate(akun)
    ]
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="back")])
    await q.edit_message_text(
        "✏️ Pilih akun untuk diedit:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EDIT_SELECT


async def editakun_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data)
    context.user_data["edit_idx"] = idx
    keyboard = [
        [
            InlineKeyboardButton("🚫 Tidak diubah", callback_data="skip_name"),
            InlineKeyboardButton("🔙 Kembali", callback_data="back"),
        ]
    ]
    await q.edit_message_text(
        "✏️ Masukkan nama baru (kosong = tidak diubah):", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EDIT_NAME


async def editakun_skip_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["edit_name"] = ""
    await q.edit_message_text(
        "🔑 Masukkan token baru (kosong = tidak diubah):",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
        ),
    )
    return EDIT_TOKEN


async def editakun_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["edit_name"] = update.message.text.strip()
    await update.message.reply_text(
        "🔑 Masukkan token baru (kosong = tidak diubah):",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
        ),
    )
    return EDIT_TOKEN


async def editakun_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_token = update.message.text.strip()
    idx = context.user_data.get("edit_idx")
    new_name = context.user_data.get("edit_name", "").strip()
    akun = load_accounts()
    if idx is not None and 0 <= idx < len(akun):
        if new_name:
            akun[idx]["name"] = new_name
        if new_token:
            akun[idx]["token"] = new_token
        save_accounts(akun)
        await update.message.reply_text("✅ Akun berhasil diperbarui!")
    else:
        await update.message.reply_text("❌ Indeks akun tidak valid.")
    await update.message.reply_text(
        "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
    )
    return ConversationHandler.END


editakun_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(editakun_start, pattern="^editakun$")],
    states={
        EDIT_SELECT: [
            CallbackQueryHandler(editakun_select, pattern="^\\d+$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        EDIT_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, editakun_name),
            CallbackQueryHandler(editakun_skip_name, pattern="^skip_name$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        EDIT_TOKEN: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, editakun_token),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
    },
    fallbacks=[CallbackQueryHandler(kembali_ke_menu, pattern="^back$")],
    per_chat=True,
)


# ==== UPGRADER (HEADLESS) HANDLERS ====
async def upgrader_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Semua", callback_data="all")],
        [
            InlineKeyboardButton("5 Akun", callback_data="5"),
            InlineKeyboardButton("10 Akun", callback_data="10"),
        ],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    if update.message:
        await update.message.reply_text(
            "👥 Pilih jumlah akun:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        q = update.callback_query
        await q.answer()
        await q.edit_message_text(
            "👥 Pilih jumlah akun:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
    return UPG_JUMLAH


async def upgrader_jumlah(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["upg_jumlah"] = q.data
    await q.edit_message_text(
        "🔎 Masukkan Search Query (contoh: Ryoma):",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
        ),
    )
    return UPG_SEARCH


async def upgrader_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    search_query = (update.message.text or "").strip()
    context.user_data["upg_search"] = search_query
    # Otomatis gunakan search_query sebagai nama item (tidak perlu input terpisah)
    context.user_data["upg_select_by"] = "name"
    context.user_data["upg_select_val"] = search_query

    keyboard = [
        [InlineKeyboardButton("Bet Manual", callback_data="manual")],
        [InlineKeyboardButton("Bet Max", callback_data="max")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    await update.message.reply_text(
        "💰 Pilih mode bet:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return UPG_BET_MODE


async def upgrader_bet_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["upg_bet_mode"] = q.data
    if q.data == "manual":
        await q.edit_message_text(
            "✍️ Masukkan jumlah bet (contoh: 0.10):",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
            ),
        )
        return UPG_BET_AMOUNT
    else:
        keyboard = [
            [InlineKeyboardButton("🔽 Roll Under", callback_data="roll_under")],
            [InlineKeyboardButton("🔼 Roll Over", callback_data="roll_over")],
            [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
        ]
        await q.edit_message_text(
            "🎯 Pilih arah roll:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return UPG_ROLL_DIRECTION


async def upgrader_bet_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amt = (update.message.text or "").strip()
    try:
        if float(amt) <= 0:
            raise ValueError()
    except Exception:
        return await update.message.reply_text(
            "❌ Jumlah bet harus angka > 0. Contoh: 0.10",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
            ),
        )
    context.user_data["upg_bet_amount"] = amt
    keyboard = [
        [InlineKeyboardButton("🔽 Roll Under", callback_data="roll_under")],
        [InlineKeyboardButton("🔼 Roll Over", callback_data="roll_over")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    await update.message.reply_text(
        "🎯 Pilih arah roll:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return UPG_ROLL_DIRECTION


async def upgrader_delay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    try:
        d = float(text)
        if d < 0:
            raise ValueError()
    except Exception:
        return await update.message.reply_text(
            "❌ Delay harus berupa angka >= 0. Contoh: 2 atau 2.5",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
            ),
        )
    context.user_data["upg_delay"] = d

    keyboard = [
        [InlineKeyboardButton("🔍 Filter Saldo", callback_data="upg_filter_saldo")],
        [InlineKeyboardButton("📋 Semua Akun", callback_data="upg_all_accounts")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    await update.message.reply_text(
        "💰 Pilih filter akun:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return UPG_FILTER


async def upgrader_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "upg_filter_saldo":
        await q.edit_message_text("🔍 Mengecek saldo akun...", reply_markup=None)

        # Cek saldo semua akun
        akun = load_accounts()
        if not akun:
            await q.edit_message_text("❌ Tidak ada akun.")
            return ConversationHandler.END

        akun_dengan_saldo = []
        akun_tanpa_saldo = []

        for acc in akun:
            try:
                saldo = get_balance(acc["token"])
                if saldo >= 0.1:
                    akun_dengan_saldo.append({"name": acc["name"], "saldo": saldo})
                else:
                    akun_tanpa_saldo.append({"name": acc["name"], "saldo": saldo})
            except:
                akun_tanpa_saldo.append({"name": acc["name"], "saldo": 0})

        # Tampilkan hasil ringkas (hanya jumlah, tanpa daftar nama)
        text = (
            "💰 Hasil Filter Saldo:\n\n"
            f"✅ Akun dengan saldo ≥ $0.1: <b>{len(akun_dengan_saldo)}</b>\n"
            f"❌ Akun dengan saldo < $0.1: <b>{len(akun_tanpa_saldo)}</b>\n"
        )

        # Simpan akun yang akan dijalankan
        context.user_data["upg_filtered_accounts"] = [acc["name"] for acc in akun_dengan_saldo]

        # Set default paralel jika belum ada
        if "upg_conc" not in context.user_data:
            context.user_data["upg_conc"] = 1

        current_parallel = context.user_data.get("upg_conc", 1)
        text += f"\n\n⚙️ Paralel: {current_parallel}"

        keyboard = []
        if akun_dengan_saldo:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        "🚀 Jalankan Akun Bersaldo", callback_data="upg_run_filtered"
                    )
                ]
            )
        # Tambah baris tombol paralel 1-5
        keyboard.append([
            InlineKeyboardButton("1", callback_data="upg_conc_1"),
            InlineKeyboardButton("2", callback_data="upg_conc_2"),
            InlineKeyboardButton("3", callback_data="upg_conc_3"),
            InlineKeyboardButton("4", callback_data="upg_conc_4"),
            InlineKeyboardButton("5", callback_data="upg_conc_5"),
        ])
        keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="back")])

        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return UPG_CONFIRM

    elif q.data == "upg_all_accounts":
        context.user_data["upg_filtered_accounts"] = None  # Jalankan semua akun
        jumlah = context.user_data.get("upg_jumlah", "all")
        search = context.user_data.get("upg_search", "-")
        bmode = context.user_data.get("upg_bet_mode", "manual")
        bamt = context.user_data.get("upg_bet_amount", "0.10")
        delay = context.user_data.get("upg_delay", 0)

        # Set default paralel jika belum ada
        if "upg_conc" not in context.user_data:
            context.user_data["upg_conc"] = 1

        current_parallel = context.user_data.get("upg_conc", 1)

        msg = (
            "📋 Konfirmasi Upgrader (Headless):\n"
            f"• Jumlah akun: {jumlah}\n"
            f"• Search & Item: {search}\n"
            f"• Bet mode: {bmode}{(' → '+bamt) if bmode=='manual' else ''}\n"
            f"• Delay: {delay}s\n"
            f"• Browser: Headless\n"
            f"• Filter: Semua Akun\n"
            f"• Paralel: {current_parallel}\n"
        )
        keyboard = [
            [InlineKeyboardButton("🚀 Mulai", callback_data="upg_start")],
            # Tambah baris tombol paralel 1-5
            [
                InlineKeyboardButton("1", callback_data="upg_conc_1"),
                InlineKeyboardButton("2", callback_data="upg_conc_2"),
                InlineKeyboardButton("3", callback_data="upg_conc_3"),
                InlineKeyboardButton("4", callback_data="upg_conc_4"),
                InlineKeyboardButton("5", callback_data="upg_conc_5"),
            ],
            [InlineKeyboardButton("🔙 Batal", callback_data="back")],
        ]
        await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        return UPG_CONFIRM


# Handler untuk tombol paralel
async def upgrader_parallel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    # Penting: jawab callback segera agar spinner Telegram berhenti
    try:
        await q.answer()
    except Exception:
        pass

    # Ambil nilai paralel dari callback data, contoh: upg_conc_3 -> 3
    try:
        parallel_value = int((q.data or "").split("_")[-1])
    except Exception:
        parallel_value = 1
    if parallel_value < 1:
        parallel_value = 1
    if parallel_value > 5:
        parallel_value = 5

    # Simpan pilihan ke context
    context.user_data["upg_conc"] = parallel_value

    # Refresh tampilan sesuai mode saat ini (filter saldo vs semua akun)
    try:
        if context.user_data.get("upg_filtered_accounts") is not None:
            # Mode filter saldo
            return await upgrader_filter_saldo_refresh(update, context)
        else:
            # Mode semua akun
            return await upgrader_all_accounts_refresh(update, context)
    except Exception:
        # Jika terjadi error saat refresh, tetap kembalikan state konfirmasi
        return UPG_CONFIRM
    await q.answer()

    # Ambil nilai paralel dari callback data
    parallel_value = int(q.data.split("_")[-1])  # upg_conc_1 -> 1
    context.user_data["upg_conc"] = parallel_value

    # Refresh tampilan dengan nilai paralel yang baru
    if context.user_data.get("upg_filtered_accounts") is not None:
        # Jika sedang di mode filter saldo, buat update baru dengan data yang benar
        # Simulasi callback untuk filter saldo
        await upgrader_filter_saldo_refresh(update, context)
        return UPG_CONFIRM
    else:
        # Jika sedang di mode semua akun, buat update baru dengan data yang benar
        await upgrader_all_accounts_refresh(update, context)
        return UPG_CONFIRM


# Handler untuk roll direction
async def upgrader_roll_direction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["upg_roll_direction"] = q.data  # 'roll_under' atau 'roll_over'
    context.user_data["upg_delay"] = 0  # Set default delay ke 0
    keyboard = [
        [InlineKeyboardButton("🔍 Filter Saldo", callback_data="upg_filter_saldo")],
        [InlineKeyboardButton("📋 Semua Akun", callback_data="upg_all_accounts")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    await q.edit_message_text(
        "💰 Pilih filter akun:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return UPG_FILTER


# Helper function untuk refresh filter saldo
async def upgrader_filter_saldo_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass

    # Ambil data dari context tanpa melakukan cek saldo ulang (agar cepat)
    filtered_names = context.user_data.get("upg_filtered_accounts") or []
    total_accounts = 0
    try:
        total_accounts = len(load_accounts())
    except Exception:
        total_accounts = len(filtered_names)

    dengan_saldo = len(filtered_names)
    tanpa_saldo = max(total_accounts - dengan_saldo, 0)

    current_parallel = int(context.user_data.get("upg_conc", 1))

    text = (
        "💰 Hasil Filter Saldo:\n\n"
        f"✅ Akun dengan saldo ≥ $0.1: <b>{dengan_saldo}</b>\n"
        f"❌ Akun dengan saldo < $0.1: <b>{tanpa_saldo}</b>\n\n"
        f"⚙️ Paralel: {current_parallel}"
    )

    keyboard = []
    if dengan_saldo > 0:
        keyboard.append([
            InlineKeyboardButton("🚀 Jalankan Akun Bersaldo", callback_data="upg_run_filtered"),
        ])
    keyboard.append([
        InlineKeyboardButton("1", callback_data="upg_conc_1"),
        InlineKeyboardButton("2", callback_data="upg_conc_2"),
        InlineKeyboardButton("3", callback_data="upg_conc_3"),
        InlineKeyboardButton("4", callback_data="upg_conc_4"),
        InlineKeyboardButton("5", callback_data="upg_conc_5"),
    ])
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="back")])

    try:
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    except Exception:
        # Fallback tanpa parse_mode jika diperlukan
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    return UPG_CONFIRM

    # Cek saldo semua akun
    akun = load_accounts()
    if not akun:
        await q.edit_message_text("❌ Tidak ada akun.")
        return

    akun_dengan_saldo = []
    akun_tanpa_saldo = []

    for acc in akun:
        try:
            saldo = get_balance(acc["token"])
            if saldo >= 0.1:
                akun_dengan_saldo.append({"name": acc["name"], "saldo": saldo})
            else:
                akun_tanpa_saldo.append({"name": acc["name"], "saldo": saldo})
        except:
            akun_tanpa_saldo.append({"name": acc["name"], "saldo": 0})

    # Tampilkan hasil ringkas (hanya jumlah, tanpa daftar nama)
    text = (
    "💰 Hasil Filter Saldo:\n\n"
    f"✅ Akun dengan saldo ≥ $0.1: <b>{len(akun_dengan_saldo)}</b>\n"
    f"❌ Akun dengan saldo < $0.1: <b>{len(akun_tanpa_saldo)}</b>\n"
    )

    # Simpan akun yang akan dijalankan
    context.user_data["upg_filtered_accounts"] = [acc["name"] for acc in akun_dengan_saldo]

    current_parallel = context.user_data.get("upg_conc", 1)
    text += f"\n\n⚙️ Paralel: {current_parallel}"

    keyboard = []
    if akun_dengan_saldo:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "🚀 Jalankan Akun Bersaldo", callback_data="upg_run_filtered"
                )
            ]
        )
    # Tambah baris tombol paralel 1-5
    keyboard.append([
        InlineKeyboardButton("1", callback_data="upg_conc_1"),
        InlineKeyboardButton("2", callback_data="upg_conc_2"),
        InlineKeyboardButton("3", callback_data="upg_conc_3"),
        InlineKeyboardButton("4", callback_data="upg_conc_4"),
        InlineKeyboardButton("5", callback_data="upg_conc_5"),
    ])
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="back")])

    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


# Helper function untuk refresh semua akun
async def upgrader_all_accounts_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass

    # Ambil parameter yang sudah tersimpan untuk menampilkan ringkasan sederhana
    jumlah = context.user_data.get("upg_jumlah", "all")
    bet_mode = context.user_data.get("upg_bet_mode", "manual")
    bet_amt = context.user_data.get("upg_bet_amount", "-")
    roll_dir = context.user_data.get("upg_roll_direction", "roll_under")
    delay = context.user_data.get("upg_delay", 0)
    search = context.user_data.get("upg_search", "-")

    current_parallel = int(context.user_data.get("upg_conc", 1))

    # Bangun teks konfirmasi (tanpa proses berat)
    msg = (
        "📋 Konfirmasi Upgrader (Semua Akun):\n\n"
        f"• Jumlah akun: {jumlah}\n"
        f"• Search: {search}\n"
        f"• Mode bet: {bet_mode}{(f' ({bet_amt})' if bet_mode == 'manual' else '')}\n"
        f"• Arah roll: {'Roll Under' if roll_dir == 'roll_under' else 'Roll Over'}\n"
        f"• Delay: {delay}s\n\n"
        f"⚙️ Paralel: {current_parallel}\n\n"
        "Konfirmasi?"
    )

    keyboard = [
        [InlineKeyboardButton("🚀 Mulai", callback_data="upg_start")],
        [
            InlineKeyboardButton("1", callback_data="upg_conc_1"),
            InlineKeyboardButton("2", callback_data="upg_conc_2"),
            InlineKeyboardButton("3", callback_data="upg_conc_3"),
            InlineKeyboardButton("4", callback_data="upg_conc_4"),
            InlineKeyboardButton("5", callback_data="upg_conc_5"),
        ],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]

    await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    return UPG_CONFIRM

    context.user_data["upg_filtered_accounts"] = None  # Jalankan semua akun
    jumlah = context.user_data.get("upg_jumlah", "all")
    search = context.user_data.get("upg_search", "-")
    bmode = context.user_data.get("upg_bet_mode", "manual")
    bamt = context.user_data.get("upg_bet_amount", "0.10")
    delay = context.user_data.get("upg_delay", 0)

    current_parallel = context.user_data.get("upg_conc", 1)

    msg = (
        "📋 Konfirmasi Upgrader (Headless):\n"
        f"• Jumlah akun: {jumlah}\n"
        f"• Search & Item: {search}\n"
        f"• Bet mode: {bmode}{(' → '+bamt) if bmode=='manual' else ''}\n"
        f"• Delay: {delay}s\n"
        f"• Browser: Headless\n"
        f"• Filter: Semua Akun\n"
        f"• Paralel: {current_parallel}\n"
    )
    keyboard = [
        [InlineKeyboardButton("🚀 Mulai", callback_data="upg_start")],
        # Tambah baris tombol paralel 1-5
        [
            InlineKeyboardButton("1", callback_data="upg_conc_1"),
            InlineKeyboardButton("2", callback_data="upg_conc_2"),
            InlineKeyboardButton("3", callback_data="upg_conc_3"),
            InlineKeyboardButton("4", callback_data="upg_conc_4"),
            InlineKeyboardButton("5", callback_data="upg_conc_5"),
        ],
        [InlineKeyboardButton("🔙 Batal", callback_data="back")],
    ]
    await q.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))


async def upgrader_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🚀 Menjalankan Upgrader (Headless)…",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⛔ Stop", callback_data="stop_upgrade")]]
        ),
    )
    context.application.bot_data["stop_upgrade"] = False

    async def _runner():
        jumlah = context.user_data.get("upg_jumlah", "all")
        search = context.user_data.get("upg_search", "")
        sby = context.user_data.get("upg_select_by", "name")
        sval = context.user_data.get("upg_select_val", "")
        bmode = context.user_data.get("upg_bet_mode", "manual")
        bamt = context.user_data.get("upg_bet_amount", "0.10")
        delay = float(context.user_data.get("upg_delay", 0))

        akun_list = load_accounts()
        target = akun_list if jumlah == "all" else akun_list[: int(jumlah)]

        # Tentukan akun yang akan dijalankan berdasarkan filter
        filtered_names = context.user_data.get("upg_filtered_accounts")
        if filtered_names is not None:  # Jika ada filter saldo
            target = [acc for acc in target if acc["name"] in filtered_names]
            if target:
                await context.bot.send_message(
                    chat_id=q.message.chat.id,
                    text=f"🔍 Menjalankan {len(target)} akun bersaldo dari {len(akun_list)} total akun",
                )

        # Jalankan semua akun sekaligus dengan delay internal (tidak duplikat)
        try:
            # Set stop mechanism untuk upgrader
            def stop_check():
                return context.application.bot_data.get("stop_upgrade", False)

            # Custom stop event untuk upgrader
            class StopEvent:
                def __init__(self):
                    self._is_set = False

                def is_set(self):
                    return context.application.bot_data.get("stop_upgrade", False)

                def set(self):
                    context.application.bot_data["stop_upgrade"] = True

            stop_event = StopEvent()

            await jalankan_upgrader(
                search_query=search,
                select_by=sby,
                select_value=sval,
                bet_mode=bmode,
                bet_amount=bamt,
                roll_direction=context.user_data.get("upg_roll_direction", "roll_under"),  # Parameter roll direction
                headless=True,  # paksa HEADLESS untuk VPS/CLI
                context=context,
                akun_list=target,  # Akun yang sudah difilter
                stop_event=stop_event,
                log_func=None,
                delay_between_accounts=delay,  # Delay akan dihandle internal
                max_concurrency=context.user_data.get("upg_conc", 1),  # Paralel dari tombol
            )
        except Exception as e:
            await context.bot.send_message(
                chat_id=q.message.chat.id, text=f"❌ Error upgrader: {e}"
            )
        await context.bot.send_message(chat_id=q.message.chat.id, text="✅ Selesai Upgrader.")
        await context.bot.send_message(
            chat_id=q.message.chat.id,
            text="📍 Pilih menu:",
            reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
        )

    context.application.create_task(_runner())
    return ConversationHandler.END


async def stop_upgrade_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # Set stop flag untuk menghentikan semua proses upgrader
    context.application.bot_data["stop_upgrade"] = True

    # Langsung edit pesan dan kembali ke menu tanpa melanjutkan proses apapun
    await q.edit_message_text("🚫 Upgrader dihentikan via tombol.")

    # Kirim menu utama langsung
    await q.message.reply_text(
        "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
    )
    return ConversationHandler.END

    # Langsung end conversation tanpa menunggu proses lain
    return ConversationHandler.END


upgrader_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(upgrader_start, pattern="^upgrader$")],
    states={
        UPG_JUMLAH: [
            CallbackQueryHandler(upgrader_jumlah, pattern="^(all|5|10)$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        UPG_SEARCH: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, upgrader_search),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        UPG_BET_MODE: [
            CallbackQueryHandler(upgrader_bet_mode, pattern="^(manual|max)$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        UPG_BET_AMOUNT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, upgrader_bet_amount),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        UPG_ROLL_DIRECTION: [
            CallbackQueryHandler(upgrader_roll_direction, pattern="^(roll_under|roll_over)$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        UPG_DELAY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, upgrader_delay),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        UPG_FILTER: [
            CallbackQueryHandler(upgrader_filter, pattern="^(upg_filter_saldo|upg_all_accounts)$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        UPG_PARALEL: [
            CallbackQueryHandler(upgrader_parallel_handler, pattern="^upg_conc_[1-5]$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
        UPG_CONFIRM: [
            CallbackQueryHandler(upgrader_execute, pattern="^(upg_start|upg_run_filtered)$"),
            CallbackQueryHandler(upgrader_parallel_handler, pattern="^upg_conc_[1-5]$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ],
    },
    fallbacks=[CallbackQueryHandler(kembali_ke_menu, pattern="^back$")],
    per_chat=True,
)


# ==== SIMPLE COMMANDS & MENU ====
async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    akun = load_accounts()
    if not akun:
        await update.message.reply_text("Tidak ada akun.")
        return

    # Tampilkan dengan urutan konsisten berdasarkan posisi array (seperti fitur lain)
    lines = []
    for i, acc in enumerate(akun):
        lines.append(f"{i+1}. {acc['name']}")

    await update.message.reply_text("📋 Daftar Akun:\n" + "\n".join(lines))


async def akun_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    akun = load_accounts()
    if not akun:
        await update.message.reply_text("❌ Tidak ada akun.")
        return
    akun_saldo = [(acc["name"], get_balance(acc["token"])) for acc in akun]
    akun_saldo.sort(key=lambda x: x[1], reverse=True)
    max_n = max(len(n) for n, _ in akun_saldo)
    max_s = max(len(f"{s:.2f}") for _, s in akun_saldo)
    msg = "💰 <b>Urutan Saldo Terbesar:</b>\n\n"
    for i, (n, s) in enumerate(akun_saldo, 1):
        msg += f"{i}. {n.ljust(max_n)} | Saldo: {str(f'{s:.2f}'.rjust(max_s))}\n"
    await update.message.reply_text(f"<pre>{msg}</pre>", parse_mode="HTML")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📟 <b>Daftar Perintah</b>\n\n"
        "/start - Tampilkan menu\n"
        "/info - Daftar akun\n"
        "/akun - Cek saldo terurut\n"
        "/klaimkupon - Klaim kupon massal\n"
        "/wheel - Jalankan auto bet\n"
        "/stop - Hentikan auto bet\n"
        "/help - Tampilkan bantuan\n\n"
        "ℹ️ Upgrader tersedia via menu inline (🚀 Upgrader).",
        parse_mode="HTML",
    )


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.application.bot_data["stop_bet"] = True
    await update.message.reply_text("🚫 Auto bet dihentikan.")
    await update.message.reply_text(
        "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    import traceback

    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text("⚠️ Terjadi kesalahan saat menjalankan perintah.")


async def menu_utama(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup(_main_menu_keyboard())
    text = "📍 Pilih menu:"
    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    else:
        q = update.callback_query
        await q.answer()
        await q.edit_message_text(text, reply_markup=kb)
    return ConversationHandler.END


# ==== REFRESH TOKEN HANDLER ====
async def refreshtoken_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    keyboard = [
        [InlineKeyboardButton("♻️ Refresh via invalid.txt", callback_data="start_refresh")],
        [InlineKeyboardButton("➕ Tambah akun via seed", callback_data="seed_add_start")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    await q.edit_message_text(
        "🔄 <b>Refresh Token Otomatis</b>\n\n"
        "Fitur ini akan:\n"
        "• Cek semua token yang tidak valid\n"
        "• Ambil seed phrase yang sesuai\n"
        "• Login ulang ke flip.gg dengan Solflare\n"
        "• Update token baru ke akun.enc\n\n"
        "⚠️ <b>Pastikan:</b>\n"
        "• File seed.enc sudah ada\n"
        "• Nama akun sama dengan nama di seed.enc\n"
        "• Koneksi internet stabil\n\n"
        "Lanjutkan?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return REFRESH_CONFIRM


async def refreshtoken_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.application.bot_data["stop_refresh"] = False

    async def _runner():
        # Custom stop event untuk refresh
        class StopEvent:
            def __init__(self):
                self._is_set = False

            def is_set(self):
                return context.application.bot_data.get("stop_refresh", False)

            def set(self):
                context.application.bot_data["stop_refresh"] = True

        stop_event = StopEvent()

        # Baca daftar invalid dari invalid.txt (satu nama per baris)
        invalid_names = []
        try:
            import os

            invalid_path = os.path.join(os.path.dirname(__file__), "invalid.txt")
            if os.path.exists(invalid_path):
                with open(invalid_path, "r", encoding="utf-8", errors="ignore") as f:
                    for raw in f:
                        line = (raw or "").strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            name = line.split("=", 1)[0].strip()
                        else:
                            name = line
                        if name:
                            invalid_names.append(name)
            else:
                await context.bot.send_message(
                    chat_id=q.message.chat.id,
                    text="⚠️ invalid.txt tidak ditemukan. Tidak ada akun untuk direfresh.",
                )
        except Exception as e:
            await context.bot.send_message(
                chat_id=q.message.chat.id, text=f"❌ Gagal membaca invalid.txt: {e}"
            )
            invalid_names = []

        # Ringkas akun invalid: nama + potongan token + status seed
        akun_list = load_accounts() or []
        seeds = load_seed_phrases() or {}
        seeds_ci = {(k or "").strip().lower(): (v or "") for k, v in seeds.items()}
        invalid_set = {(n or "").strip().lower() for n in invalid_names}

        matched = []
        for acc in akun_list:
            nm = acc.get("name", "") or ""
            nm_lower = nm.lower()
            if nm_lower in invalid_set:
                tok = acc.get("token", "") or ""
                if len(tok) > 20:
                    masked = f"{tok[:8]}…{tok[-8:]}"
                else:
                    masked = f"{tok[:4]}…{tok[-4:]}" if len(tok) >= 8 else tok
                has_seed = "✅" if nm_lower in seeds_ci else "❌"
                matched.append((nm, masked, has_seed))

        if matched:
            lines = [f"{i+1}. {n} | token: {m} | seed: {s}" for i, (n, m, s) in enumerate(matched)]
            seed_avail = sum(1 for _, _, s in matched if s == "✅")
            text = (
                f"📋 Ringkasan Akun Invalid ({len(matched)}):\n\n"
                + "\n".join(lines)
                + f"\n\n🔑 Seed tersedia: {seed_avail}/{len(matched)}"
            )
        else:
            text = "🎉 Tidak ada akun invalid pada invalid.txt atau tidak cocok dengan akun.enc"

        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 Jalankan Refresh", callback_data="run_refresh_now")],
                [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
            ]
        )
        await context.bot.send_message(chat_id=q.message.chat.id, text=text, reply_markup=kb)

    context.application.create_task(_runner())


async def stop_refresh_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.application.bot_data["stop_refresh"] = True
    await q.edit_message_text("🚫 Refresh token dihentikan via tombol.")
    await q.message.reply_text(
        "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
    )


# Tambahan: eksekusi refresh setelah konfirmasi ringkasan
async def refreshtoken_run_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    # Tidak lagi mengirim "Memulai proses refresh" dari modul bot.
    # Pesan ini kini dikirim oleh refresher tepat di bawah ringkasan.
    context.application.bot_data["stop_refresh"] = False

    async def _runner():
        # Custom stop event untuk refresh
        class StopEvent:
            def __init__(self):
                self._is_set = False

            def is_set(self):
                return context.application.bot_data.get("stop_refresh", False)

            def set(self):
                context.application.bot_data["stop_refresh"] = True

        stop_event = StopEvent()

        # Baca ulang invalid.txt
        invalid_names = []
        try:
            import os

            invalid_path = os.path.join(os.path.dirname(__file__), "invalid.txt")
            if os.path.exists(invalid_path):
                with open(invalid_path, "r", encoding="utf-8", errors="ignore") as f:
                    for raw in f:
                        line = (raw or "").strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            name = line.split("=", 1)[0].strip()
                        else:
                            name = line
                        if name:
                            invalid_names.append(name)
        except Exception as e:
            await context.bot.send_message(
                chat_id=q.message.chat.id, text=f"❌ Gagal membaca invalid.txt: {e}"
            )

        try:
            await refresh_invalid_tokens(
                headless=False,
                context=context,
                log_func=None,
                stop_event=stop_event,
                invalid_names=invalid_names if invalid_names else None,
            )
        except Exception as e:
            await context.bot.send_message(
                chat_id=q.message.chat.id, text=f"❌ Error refresh token: {e}"
            )

        await context.bot.send_message(
            chat_id=q.message.chat.id,
            text="📍 Pilih menu:",
            reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
        )

    context.application.create_task(_runner())


async def seed_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    # Hitung ringkasan kandidat dari seed (nama belum ada di akun.enc & seed 12 kata)
    akun_list = load_accounts() or []
    seeds = load_seed_phrases() or {}
    existing = {(a.get("name") or "").strip().lower() for a in akun_list}

    def _valid_seed_12(s: str) -> bool:
        try:
            return len([w for w in (s or "").split() if w]) == 12
        except Exception:
            return False

    candidates = []
    if isinstance(seeds, dict):
        for name, seed in seeds.items():
            n = (name or "").strip()
            if not n:
                continue
            if n.lower() in existing:
                continue
            if not _valid_seed_12(seed or ""):
                continue
            candidates.append(n)

    text = (
        "➕ Tambah akun via seed\n\n"
        f"📦 Total seed: {len(seeds)}\n"
        f"📋 Akun eksisting: {len(akun_list)}\n"
        f"🧮 Kandidat baru: {len(candidates)}\n\n"
        "👁️ Pilih mode browser untuk proses login via seed:"
    )
    keyboard = [
        [InlineKeyboardButton("👁️ Tampilkan Browser", callback_data="show")],
        [InlineKeyboardButton("🕶️ Headless (disarankan di VPS)", callback_data="headless")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    # Tetap di state REFRESH_CONFIRM agar tombol dihandle oleh refresh_conv
    return REFRESH_CONFIRM
    await q.answer()
    akun_list = load_accounts() or []
    existing = {(a.get("name") or "").strip().lower() for a in akun_list}
    seeds = load_seed_phrases() or {}

    def _valid_seed_12(s: str) -> bool:
        try:
            words = [w for w in (s or "").strip().split() if w]
            return len(words) == 12
        except Exception:
            return False

    candidates = []
    for name, seed in seeds.items() if isinstance(seeds, dict) else []:
        n = (name or "").strip()
        if not n:
            continue
        if n.lower() in existing:
            continue
        if not _valid_seed_12(seed):
            continue
        candidates.append(n)

    # Ringkasan
    text = (
        "➕ Tambah akun via seed\n\n"
        f"📦 Total seed: {len(seeds)}\n"
        f"📋 Akun eksisting: {len(akun_list)}\n"
        f"🆕 Kandidat akun baru: {len(candidates)}\n\n"
        "Aksi ini akan login ke flip.gg untuk setiap kandidat, mengambil token, dan menambahkan ke akun.enc."
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👁️ Tampilkan Browser", callback_data="show")],
            [InlineKeyboardButton("🕶️ Headless (disarankan di VPS)", callback_data="headless")],
            [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
        ]
    )
    await q.edit_message_text(text, reply_markup=kb)


async def seed_add_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    # Simpan pilihan browser jika dipicu dari tombol show/headless
    try:
        if q and q.data in ("show", "headless"):
            context.user_data["seed_add_browser"] = q.data
    except Exception:
        pass

    # Tentukan headless berdasarkan pilihan; default headless=True (aman untuk VPS)
    try:
        browser_choice = context.user_data.get("seed_add_browser")
        headless = (browser_choice != "show")
    except Exception:
        headless = True

    # Reset flag stop untuk proses seed-add: gunakan stop_refresh agar dikenali add_accounts_via_seed
    try:
        context.application.bot_data["stop_refresh"] = False
    except Exception:
        pass

    # Tampilkan pesan running + tombol Stop
    await q.edit_message_text(
        "🚀 Menjalankan tambah akun via seed…",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⛔ Stop", callback_data="STOP_REFRESH")]]),
    )

    async def _runner():
        try:
            summary = await add_accounts_via_seed(
                headless=headless,
                max_concurrency=int(context.user_data.get("seed_add_conc", 1)),
                context=context,
            )
            await context.bot.send_message(
                chat_id=q.message.chat.id,
                text=(
                    "📋 Ringkasan Tambah via Seed\n"
                    f"• Kandidat: {summary.get('candidates',0)}\n"
                    f"• Ditambahkan: {summary.get('added',0)}\n"
                    f"• Gagal: {summary.get('failed',0)}\n"
                    f"• Mode: {'Headless' if headless else 'Visible'}\n"
                ),
            )
        except Exception as e:
            await context.bot.send_message(
                chat_id=q.message.chat.id,
                text=f"❌ Error tambah via seed: {e}",
            )
        await context.bot.send_message(
            chat_id=q.message.chat.id,
            text="📍 Pilih menu:",
            reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
        )

    context.application.create_task(_runner())
    return ConversationHandler.END
    # Simpan pilihan browser jika dipilih dari tombol
    try:
        if q and q.data in ("show", "headless"):
            context.user_data["seed_add_browser"] = q.data
    except Exception:
        pass
    await q.answer()

    async def _runner():
        try:
            summary = await add_accounts_via_seed(
                headless=False,
                max_concurrency=int(context.user_data.get("seed_add_conc", 1)),
                context=context,
                log_func=None,
                stop_event=None,
            )
        except Exception as e:
            await context.bot.send_message(
                chat_id=q.message.chat.id, text=f"❌ Error tambah via seed: {e}"
            )
        # Selalu kembali ke menu utama setelah selesai
        await context.bot.send_message(
            chat_id=q.message.chat.id,
            text="📍 Pilih menu:",
            reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
        )

    context.application.create_task(_runner())


# Refresh Token ConversationHandler
REFRESH_CONFIRM = 800

refresh_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(refreshtoken_start, pattern="^refreshtoken$")],
    states={
        REFRESH_CONFIRM: [
            CallbackQueryHandler(refreshtoken_execute, pattern="^start_refresh$"),
            CallbackQueryHandler(seed_add_start, pattern="^seed_add_start$"),
            CallbackQueryHandler(seed_add_prompt_conc, pattern="^(show|headless)$"),
            CallbackQueryHandler(seed_add_run, pattern="^seed_add_run$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, seed_add_concurrency),
            CallbackQueryHandler(refreshtoken_run_execute, pattern="^run_refresh_now$"),
            CallbackQueryHandler(stop_refresh_handler, pattern="^STOP_REFRESH$"),
            CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
        ]
    },
    fallbacks=[CallbackQueryHandler(kembali_ke_menu, pattern="^back$")],
    per_chat=True,
)


# ==== KELOLA AKUN HANDLERS ====
async def kelola_akun_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    akun = load_accounts()

    keyboard = [
        [InlineKeyboardButton("➕ Tambah Akun", callback_data="kelola_tambah")],
        [InlineKeyboardButton("✏️ Edit Akun", callback_data="kelola_edit")],
        [InlineKeyboardButton("🗑️ Hapus Akun", callback_data="kelola_hapus")],
        [InlineKeyboardButton("📋 Lihat Daftar Akun", callback_data="kelola_lihat")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]

    text = f"📊 Total akun: {len(akun)}\n\n⚙️ Pilih aksi kelola akun:"
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return KELOLA_AKUN_MENU


async def kelola_tambah_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "📝 Masukkan nama akun:",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
        ),
    )
    return TAMBAH_NAMA


async def kelola_tambah_nama(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nama = update.message.text.strip()
    if not nama:
        await update.message.reply_text(
            "❌ Nama akun tidak boleh kosong. Masukkan nama akun:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return TAMBAH_NAMA

    # Cek duplikasi nama
    akun = load_accounts()
    for acc in akun:
        if acc["name"].lower() == nama.lower():
            await update.message.reply_text(
                f"❌ Nama akun '{nama}' sudah ada. Masukkan nama lain:",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
                ),
            )
            return TAMBAH_NAMA

    context.user_data["nama_baru"] = nama
    await update.message.reply_text(
        "🔑 Masukkan token akun:",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
        ),
    )
    return TAMBAH_TOKEN


async def kelola_tambah_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = update.message.text.strip()
    if not token:
        await update.message.reply_text(
            "❌ Token tidak boleh kosong. Masukkan token akun:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return TAMBAH_TOKEN

    if len(token) < 10:
        await update.message.reply_text(
            "❌ Token terlalu pendek. Masukkan token yang valid:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return TAMBAH_TOKEN

    nama = context.user_data.get("nama_baru", "Unknown")
    akun = load_accounts()

    # Cek duplikasi token
    for acc in akun:
        if acc["token"] == token:
            await update.message.reply_text(
                f"❌ Token sudah digunakan untuk akun '{acc['name']}'. Masukkan token lain:",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
                ),
            )
            return TAMBAH_TOKEN

    try:
        akun.append({"name": nama, "token": token})
        save_accounts(akun)

        keyboard = [
            [InlineKeyboardButton("➕ Tambah Lagi", callback_data="kelola_tambah")],
            [InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")],
        ]

        await update.message.reply_text(
            f"✅ Akun '{nama}' berhasil ditambahkan!\n\n📊 Total akun sekarang: {len(akun)}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return KELOLA_AKUN_MENU

    except Exception as e:
        await update.message.reply_text(
            f"❌ Gagal menyimpan akun: {str(e)}\n\nSilakan coba lagi:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return TAMBAH_TOKEN


async def kelola_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    akun = load_accounts()

    if not akun:
        await q.edit_message_text(
            "❌ Tidak ada akun untuk diedit.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return KELOLA_AKUN_MENU

    keyboard = [
        [InlineKeyboardButton("🔍 Cari Berdasarkan Nama", callback_data="edit_search")],
        [InlineKeyboardButton("📋 Pilih dari Daftar", callback_data="edit_list")],
    ]
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")])

    await q.edit_message_text(
        "✏️ Pilih cara untuk mencari akun:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EDIT_SELECT


async def kelola_edit_search_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "edit_search":
        await q.edit_message_text(
            "🔍 Masukkan nama akun yang ingin diedit:\n\n💡 Contoh: anjay",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return EDIT_SEARCH  # Gunakan state EDIT_SEARCH untuk input pencarian
    elif q.data == "edit_list":
        return await kelola_edit_show_list(update, context)


async def kelola_edit_show_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    akun = load_accounts()

    keyboard = []
    for i, acc in enumerate(akun):
        keyboard.append([InlineKeyboardButton(f"{i+1}. {acc['name']}", callback_data=f"edit_{i}")])
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")])

    await q.edit_message_text(
        "✏️ Pilih akun untuk diedit:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EDIT_SELECT


async def kelola_edit_search_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    search_name = update.message.text.strip().lower()
    if not search_name:
        await update.message.reply_text(
            "❌ Nama akun tidak boleh kosong. Masukkan nama akun:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return EDIT_SEARCH

    akun = load_accounts()
    found_accounts = []

    # Cari akun yang namanya mengandung kata kunci pencarian
    for i, acc in enumerate(akun):
        if search_name in acc["name"].lower():
            found_accounts.append((i, acc))

    if not found_accounts:
        await update.message.reply_text(
            f"❌ Tidak ditemukan akun dengan nama '{search_name}'.\n\n🔍 Masukkan nama akun lain:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return EDIT_SEARCH
    elif len(found_accounts) == 1:
        # Jika hanya 1 akun ditemukan, langsung pilih
        idx, acc = found_accounts[0]
        context.user_data["edit_idx"] = idx

        keyboard = [
            [InlineKeyboardButton("✏️ Edit Nama", callback_data="edit_name")],
            [InlineKeyboardButton("🔑 Edit Token", callback_data="edit_token")],
            [InlineKeyboardButton("🔄 Edit Keduanya", callback_data="edit_both")],
            [InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")],
        ]

        text = f"✅ Akun ditemukan: {acc['name']}\n\n✏️ Pilih yang ingin diedit:"
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return EDIT_SELECT
    else:
        # Jika lebih dari 1 akun ditemukan, tampilkan pilihan
        keyboard = []
        for idx, acc in found_accounts:
            keyboard.append([InlineKeyboardButton(f"✏️ {acc['name']}", callback_data=f"edit_{idx}")])
        keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")])

        text = f"🔍 Ditemukan {len(found_accounts)} akun dengan nama '{search_name}':\n\nPilih akun yang ingin diedit:"
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return EDIT_SELECT


async def kelola_edit_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split("_")[1])

    akun = load_accounts()
    if idx >= len(akun):
        await q.edit_message_text(
            "❌ Akun tidak ditemukan.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return KELOLA_AKUN_MENU

    context.user_data["edit_idx"] = idx
    current_acc = akun[idx]

    keyboard = [
        [InlineKeyboardButton("✏️ Edit Nama", callback_data="edit_name")],
        [InlineKeyboardButton("🔑 Edit Token", callback_data="edit_token")],
        [InlineKeyboardButton("🔄 Edit Keduanya", callback_data="edit_both")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")],
    ]

    text = f"✏️ Edit Akun: {current_acc['name']}\n\nPilih yang ingin diedit:"
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return EDIT_SELECT


async def kelola_edit_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    choice = q.data

    context.user_data["edit_choice"] = choice

    if choice == "edit_name":
        await q.edit_message_text(
            "✏️ Masukkan nama baru:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return EDIT_NAME
    elif choice == "edit_token":
        await q.edit_message_text(
            "🔑 Masukkan token baru:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return EDIT_TOKEN
    elif choice == "edit_both":
        await q.edit_message_text(
            "✏️ Masukkan nama baru:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return EDIT_NAME


async def kelola_edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    if not new_name:
        await update.message.reply_text(
            "❌ Nama tidak boleh kosong. Masukkan nama baru:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return EDIT_NAME

    # Cek duplikasi nama
    akun = load_accounts()
    idx = context.user_data.get("edit_idx")
    for i, acc in enumerate(akun):
        if i != idx and acc["name"].lower() == new_name.lower():
            await update.message.reply_text(
                f"❌ Nama '{new_name}' sudah digunakan. Masukkan nama lain:",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
                ),
            )
            return EDIT_NAME

    context.user_data["new_name"] = new_name
    choice = context.user_data.get("edit_choice")

    if choice == "edit_both":
        await update.message.reply_text(
            "🔑 Masukkan token baru:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return EDIT_TOKEN
    else:
        return await kelola_save_changes(update, context)


async def kelola_edit_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_token = update.message.text.strip()
    if not new_token:
        await update.message.reply_text(
            "❌ Token tidak boleh kosong. Masukkan token baru:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return EDIT_TOKEN

    if len(new_token) < 10:
        await update.message.reply_text(
            "❌ Token terlalu pendek. Masukkan token yang valid:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return EDIT_TOKEN

    # Cek duplikasi token
    akun = load_accounts()
    idx = context.user_data.get("edit_idx")
    for i, acc in enumerate(akun):
        if i != idx and acc["token"] == new_token:
            await update.message.reply_text(
                f"❌ Token sudah digunakan untuk akun '{acc['name']}'. Masukkan token lain:",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
                ),
            )
            return EDIT_TOKEN

    context.user_data["new_token"] = new_token
    return await kelola_save_changes(update, context)


async def kelola_save_changes(update, context):
    try:
        akun = load_accounts()
        idx = context.user_data.get("edit_idx")

        if idx is None or idx >= len(akun):
            await update.message.reply_text(
                "❌ Akun tidak ditemukan.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")]]
                ),
            )
            return ConversationHandler.END

        old_name = akun[idx]["name"]
        new_name = context.user_data.get("new_name")
        new_token = context.user_data.get("new_token")

        changes = []
        if new_name:
            akun[idx]["name"] = new_name
            changes.append(f"Nama: {old_name} → {new_name}")
        if new_token:
            akun[idx]["token"] = new_token
            changes.append("Token: Diperbarui")

        save_accounts(akun)

        keyboard = [
            [InlineKeyboardButton("✏️ Edit Lagi", callback_data="kelola_edit")],
            [InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")],
        ]

        change_text = "\n".join(changes)
        await update.message.reply_text(
            f"✅ Akun berhasil diperbarui!\n\n📝 Perubahan:\n{change_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        # Clear user data
        context.user_data.clear()
        return KELOLA_AKUN_MENU

    except Exception as e:
        await update.message.reply_text(
            f"❌ Gagal menyimpan perubahan: {str(e)}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")]]
            ),
        )
        return ConversationHandler.END


async def kelola_hapus_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    akun = load_accounts()

    if not akun:
        await q.edit_message_text(
            "❌ Tidak ada akun untuk dihapus.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return KELOLA_AKUN_MENU

    keyboard = []
    for i, acc in enumerate(akun):
        keyboard.append([InlineKeyboardButton(f"🗑️ {acc['name']}", callback_data=f"hapus_{i}")])
    keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")])

    await q.edit_message_text(
        "🗑️ Pilih akun yang ingin dihapus:", reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return HAPUS_PILIH


async def kelola_hapus_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split("_")[1])

    akun = load_accounts()
    if idx >= len(akun):
        await q.edit_message_text(
            "❌ Akun tidak ditemukan.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return KELOLA_AKUN_MENU

    acc_name = akun[idx]["name"]
    keyboard = [
        [InlineKeyboardButton("✅ Ya, Hapus", callback_data=f"confirm_hapus_{idx}")],
        [InlineKeyboardButton("❌ Batal", callback_data="back_kelola")],
    ]

    await q.edit_message_text(
        f"⚠️ Konfirmasi Hapus Akun\n\n🗑️ Akun: {acc_name}\n\nApakah Anda yakin ingin menghapus akun ini?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return HAPUS_PILIH


async def kelola_hapus_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.split("_")[2])

    try:
        akun = load_accounts()
        if idx >= len(akun):
            await q.edit_message_text(
                "❌ Akun tidak ditemukan.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")]]
                ),
            )
            return ConversationHandler.END

        deleted_name = akun[idx]["name"]
        del akun[idx]
        save_accounts(akun)

        keyboard = [
            [InlineKeyboardButton("🗑️ Hapus Lagi", callback_data="kelola_hapus")],
            [InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")],
        ]

        await q.edit_message_text(
            f"✅ Akun '{deleted_name}' berhasil dihapus!\n\n📊 Sisa akun: {len(akun)}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return KELOLA_AKUN_MENU

    except Exception as e:
        await q.edit_message_text(
            f"❌ Gagal menghapus akun: {str(e)}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")]]
            ),
        )
        return ConversationHandler.END


async def kelola_lihat_akun(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    akun = load_accounts()

    if not akun:
        await q.edit_message_text(
            "❌ Tidak ada akun.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")]]
            ),
        )
        return KELOLA_AKUN_MENU

    lines = []
    for i, acc in enumerate(akun):
        acc_id = acc.get("id", str(i + 1))
        token_preview = acc["token"][:15] + "..." if len(acc["token"]) > 15 else acc["token"]
        lines.append(f"{acc_id}. {acc['name']}\n   Token: {token_preview}")

    text = f"📋 Daftar Akun ({len(akun)} total):\n\n" + "\n\n".join(lines)

    keyboard = [
        [InlineKeyboardButton("🔄 Refresh", callback_data="kelola_lihat")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back_kelola")],
    ]

    if len(text) > 4000:
        chunks = [text[i : i + 3500] for i in range(0, len(text), 3500)]
        for i, chunk in enumerate(chunks):
            if i == len(chunks) - 1:  # Last chunk
                await context.bot.send_message(
                    chat_id=q.message.chat.id,
                    text=f"<pre>{html.escape(chunk)}</pre>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            else:
                await context.bot.send_message(
                    chat_id=q.message.chat.id,
                    text=f"<pre>{html.escape(chunk)}</pre>",
                    parse_mode="HTML",
                )
    else:
        await q.edit_message_text(
            f"<pre>{html.escape(text)}</pre>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    return KELOLA_AKUN_MENU


async def back_to_kelola_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.clear()  # Clear any stored data
    return await kelola_akun_start(update, context)
    context.user_data.clear()  # Clear any stored data
    return await kelola_akun_start(update, context)


async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data.clear()  # Clear any stored data
    await q.edit_message_text(
        "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
    )
    return ConversationHandler.END
    context.user_data.clear()  # Clear any stored data
    await q.edit_message_text(
        "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
    )
    return ConversationHandler.END


kelola_akun_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(kelola_akun_start, pattern="^kelola_akun$")],
    states={
        KELOLA_AKUN_MENU: [
            CallbackQueryHandler(kelola_tambah_start, pattern="^kelola_tambah$"),
            CallbackQueryHandler(kelola_edit_start, pattern="^kelola_edit$"),
            CallbackQueryHandler(kelola_hapus_start, pattern="^kelola_hapus$"),
            CallbackQueryHandler(kelola_lihat_akun, pattern="^kelola_lihat$"),
            CallbackQueryHandler(
                kelola_tambah_start, pattern="^kelola_tambah$"
            ),  # For "Tambah Lagi" button
            CallbackQueryHandler(
                kelola_edit_start, pattern="^kelola_edit$"
            ),  # For "Edit Lagi" button
            CallbackQueryHandler(
                kelola_hapus_start, pattern="^kelola_hapus$"
            ),  # For "Hapus Lagi" button
        ],
        TAMBAH_NAMA: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, kelola_tambah_nama),
            CallbackQueryHandler(back_to_kelola_menu, pattern="^back_kelola$"),
        ],
        TAMBAH_TOKEN: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, kelola_tambah_token),
            CallbackQueryHandler(back_to_kelola_menu, pattern="^back_kelola$"),
        ],
        EDIT_SELECT: [
            CallbackQueryHandler(kelola_edit_select, pattern="^edit_\\d+$"),
            CallbackQueryHandler(kelola_edit_choice, pattern="^edit_(name|token|both)$"),
            CallbackQueryHandler(kelola_edit_search_choice, pattern="^edit_(search|list)$"),
            CallbackQueryHandler(back_to_kelola_menu, pattern="^back_kelola$"),
        ],
        EDIT_SEARCH: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, kelola_edit_search_name),
            CallbackQueryHandler(back_to_kelola_menu, pattern="^back_kelola$"),
        ],
        EDIT_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, kelola_edit_name),
            CallbackQueryHandler(back_to_kelola_menu, pattern="^back_kelola$"),
        ],
        EDIT_TOKEN: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, kelola_edit_token),
            CallbackQueryHandler(back_to_kelola_menu, pattern="^back_kelola$"),
        ],
        HAPUS_PILIH: [
            CallbackQueryHandler(kelola_hapus_confirm, pattern="^hapus_\\d+$"),
            CallbackQueryHandler(kelola_hapus_execute, pattern="^confirm_hapus_\\d+$"),
            CallbackQueryHandler(back_to_kelola_menu, pattern="^back_kelola$"),
        ],
    },
    fallbacks=[
        CallbackQueryHandler(back_to_kelola_menu, pattern="^back_kelola$"),
        CallbackQueryHandler(back_to_main_menu, pattern="^main_menu$"),
        CallbackQueryHandler(kembali_ke_menu, pattern="^back$"),
    ],
    per_chat=True,
)


# ==== KELOLA SEED HANDLERS ====
# ==== KELOLA SEED HANDLERS ====
async def kelola_seed_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("[KelolaSeed] kelola_seed_start")
    q = update.callback_query
    await q.answer()

    keyboard = [
        [InlineKeyboardButton("➕ Tambah Seed", callback_data="tambah_seed")],
        [InlineKeyboardButton("✏️ Edit Seed", callback_data="edit_seed")],
        [InlineKeyboardButton("🗑️ Hapus Seed", callback_data="hapus_seed")],
        [InlineKeyboardButton("📋 Lihat Seed", callback_data="lihat_seed")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]

    await q.edit_message_text(
        "🔒 <b>Kelola Seed Phrase</b>\n\n" "⚙️ Pilih aksi:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return SEED_MENU

    await q.edit_message_text(
        "📝 Masukkan nama akun untuk seed phrase:",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
        ),
    )
    return SEED_NAMA

    if not nama:
        await update.message.reply_text(
            "❌ Nama tidak boleh kosong. Masukkan nama akun:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
            ),
        )
        return SEED_NAMA

    context.user_data["seed_nama"] = nama
    await update.message.reply_text(
        "🔑 Masukkan seed phrase (12 kata dipisah spasi):",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
        ),
    )
    return SEED_PHRASE

    if not phrase:
        await update.message.reply_text(
            "❌ Seed phrase tidak boleh kosong. Masukkan seed phrase:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
            ),
        )
        return SEED_PHRASE

    # Validasi seed phrase (harus 12 kata)
    words = phrase.split()
    if len(words) != 12:
        await update.message.reply_text(
            f"❌ Seed phrase harus 12 kata, Anda memasukkan {len(words)} kata. Masukkan seed phrase yang benar:",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
            ),
        )
        return SEED_PHRASE

    nama = context.user_data.get("seed_nama", "Unknown")

    try:
        # Load existing seeds
        seeds = load_seed_phrases()

        # Cek duplikasi nama
        for seed in seeds:
            if seed["name"].lower() == nama.lower():
                await update.message.reply_text(
                    f"❌ Seed untuk akun '{nama}' sudah ada. Masukkan nama lain:",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
                    ),
                )
                return SEED_NAMA

        # Tambah seed baru
        seeds.append({"name": nama, "seed": phrase})
        save_seed_phrases(seeds)

        keyboard = [
            [InlineKeyboardButton("➕ Tambah Lagi", callback_data="tambah_seed")],
            [InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")],
        ]

        await update.message.reply_text(
            f"✅ Seed phrase untuk akun '{nama}' berhasil disimpan!",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return SEED_MENU

    except Exception as e:
        await update.message.reply_text(
            f"❌ Gagal menyimpan seed phrase: {str(e)}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")]]
            ),
        )
        return ConversationHandler.END

    try:
        seeds = load_seed_phrases()
        if not seeds:
            await q.edit_message_text(
                "❌ Tidak ada seed phrase yang tersimpan.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]
                ),
            )
            return SEED_MENU

        lines = []
        for i, seed in enumerate(seeds, 1):
            lines.append(f"{i}. {seed['name']}")

        text = f"🔒 <b>Daftar Seed Phrase ({len(seeds)}):</b>\n\n" + "\n".join(lines)

        keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data="back")]]

        await q.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )
        return SEED_MENU

    except Exception as e:
        await q.edit_message_text(
            f"❌ Gagal memuat seed phrase: {str(e)}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")]]
            ),
        )
        return ConversationHandler.END


# ==== VALIDASI TOKEN HANDLERS ====
async def validasi_token_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    keyboard = [
        [InlineKeyboardButton("🚀 Mulai Validasi", callback_data="start_validasi")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]

    await q.edit_message_text(
        "🔐 <b>Validasi Token & Seed</b>\n\n"
        "Fitur ini akan:\n"
        "• Cek semua token akun\n"
        "• Tampilkan status valid/invalid\n"
        "• Cek kecocokan dengan seed phrase\n"
        "• Berikan rekomendasi aksi\n\n"
        "Lanjutkan?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return VALIDASI_CONFIRM


async def validasi_token_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    await q.edit_message_text("🔐 Memvalidasi token dan seed...")

    try:
        akun = load_accounts()
        seeds = load_seed_phrases()

        if not akun:
            await context.bot.send_message(
                chat_id=q.message.chat.id, text="❌ Tidak ada akun untuk divalidasi."
            )
            await context.bot.send_message(
                chat_id=q.message.chat.id,
                text="📍 Pilih menu:",
                reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
            )
            return ConversationHandler.END

        valid_tokens = []
        invalid_tokens = []
        seed_matches = []
        seed_missing = []

        for acc in akun:
            # Validasi token
            token_valid, reason = validate_token_requests_fast(acc["token"])

            if token_valid:
                valid_tokens.append(acc["name"])
            else:
                invalid_tokens.append({"name": acc["name"], "reason": reason})

            # Cek kecocokan seed
            seed_found = False
            for seed in seeds:
                if seed["name"].lower() == acc["name"].lower():
                    seed_matches.append(acc["name"])
                    seed_found = True
                    break

            if not seed_found:
                seed_missing.append(acc["name"])

        # Tampilkan hasil
        result_text = "🔐 <b>Hasil Validasi Token & Seed</b>\n\n"

        # Token valid
        result_text += f"✅ <b>Token Valid ({len(valid_tokens)}):</b>\n"
        if valid_tokens:
            for name in valid_tokens:
                result_text += f"• {name}\n"
        else:
            result_text += "Tidak ada\n"

        # Token invalid
        result_text += f"\n❌ <b>Token Invalid ({len(invalid_tokens)}):</b>\n"
        if invalid_tokens:
            for item in invalid_tokens:
                result_text += f"• {item['name']} - {item['reason']}\n"
        else:
            result_text += "Tidak ada\n"

        # Seed matches
        result_text += f"\n🔑 <b>Seed Tersedia ({len(seed_matches)}):</b>\n"
        if seed_matches:
            for name in seed_matches:
                result_text += f"• {name}\n"
        else:
            result_text += "Tidak ada\n"

        # Seed missing
        result_text += f"\n⚠️ <b>Seed Tidak Ada ({len(seed_missing)}):</b>\n"
        if seed_missing:
            for name in seed_missing:
                result_text += f"• {name}\n"
        else:
            result_text += "Tidak ada\n"

        # Rekomendasi
        result_text += "\n💡 <b>Rekomendasi:</b>\n"
        if invalid_tokens and seed_matches:
            result_text += "• Gunakan fitur 'Refresh Token' untuk token invalid\n"
        if seed_missing:
            result_text += "• Tambahkan seed phrase untuk akun yang belum ada\n"
        if not invalid_tokens:
            result_text += "• Semua token valid, tidak perlu refresh\n"

        keyboard = [
            [InlineKeyboardButton("🔄 Refresh Token", callback_data="refreshtoken")],
            [InlineKeyboardButton("🔒 Kelola Seed", callback_data="kelola_seed")],
            [InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")],
        ]

        await context.bot.send_message(
            chat_id=q.message.chat.id,
            text=result_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )

        return ConversationHandler.END

    except Exception as e:
        await context.bot.send_message(
            chat_id=q.message.chat.id,
            text=f"❌ Gagal memvalidasi: {str(e)}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")]]
            ),
        )
        return ConversationHandler.END
    seeds = load_seed_phrases() or {}

    keyboard = [
        [InlineKeyboardButton("➕ Tambah Seed", callback_data="tambah_seed")],
        [InlineKeyboardButton("📋 Lihat Daftar Seed", callback_data="lihat_seed")],
        [InlineKeyboardButton("🔙 Kembali", callback_data="back")],
    ]

    text = f"🔑 Total seed phrase: {len(seeds)}\n\n🔒 Pilih aksi kelola seed:"
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SEED_MENU


async def tambah_seed_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("[KelolaSeed] tambah_seed_start")
    q = update.callback_query
    # Pastikan callback dijawab agar tidak stuck, lalu paksa alur ke input manual
    try:
        await q.answer()
    except Exception:
        pass
    try:
        context.user_data.pop("seed_nama", None)
    except Exception:
        pass
    await q.edit_message_text("📝 Masukkan nama akun:")
    return SEED_NAMA
    await q.answer()

    akun = load_accounts()
    if akun:
        keyboard = [
            [InlineKeyboardButton(f"📋 {acc['name']}", callback_data=f"pilih_{i}")]
            for i, acc in enumerate(akun)
        ]
        keyboard.append([InlineKeyboardButton("✍️ Input Manual", callback_data="manual")])
        keyboard.append([InlineKeyboardButton("🔙 Kembali", callback_data="back")])
        await q.edit_message_text(
            "👥 Pilih nama akun atau input manual:", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return SEED_PILIH_AKUN
    else:
        await q.edit_message_text("📝 Masukkan nama akun:")
        return SEED_NAMA


async def seed_pilih_akun(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        print(f"[KelolaSeed] seed_pilih_akun data={q.data}")
    except Exception:
        pass

    if q.data == "manual":
        await q.edit_message_text("📝 Masukkan nama akun:")
        return SEED_NAMA
    elif q.data.startswith("pilih_"):
        idx = int(q.data.split("_")[1])
        akun = load_accounts()
        if 0 <= idx < len(akun):
            context.user_data["seed_nama"] = akun[idx]["name"]
            await q.edit_message_text(
                f"✅ Nama akun dipilih: {akun[idx]['name']}\n\n🔑 Masukkan seed phrase:"
            )
            return SEED_PHRASE

    return SEED_PILIH_AKUN


async def seed_nama(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("[KelolaSeed] seed_nama menerima input nama")
    # Normalisasi input nama: trim dan satukan spasi berlebih
    nama = (update.message.text or "").strip()
    nama = " ".join(nama.split())
    if not nama:
        await update.message.reply_text("❌ Nama akun tidak boleh kosong")
        return SEED_NAMA

    context.user_data["seed_nama"] = nama
    await update.message.reply_text("🔑 Masukkan seed phrase:")
    return SEED_PHRASE


async def seed_phrase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("[KelolaSeed] seed_phrase dipanggil")
    # Normalisasi input seed: trim dan satukan spasi berlebih
    seed = (update.message.text or "").strip()
    seed = " ".join(seed.split())
    if not seed:
        await update.message.reply_text("❌ Seed phrase tidak boleh kosong")
        return SEED_PHRASE

    # Ambil dan normalisasi nama akun dari context
    nama = " ".join(((context.user_data.get("seed_nama", "Unknown") or "").strip()).split())

    # Muat seed map dan satukan key secara case-insensitive agar tidak duplikat
    seeds = load_seed_phrases() or {}
    target_key = next((k for k in seeds.keys() if k.lower() == nama.lower()), nama)
    seeds[target_key] = seed

    try:
        save_seed_phrases(seeds)
        await update.message.reply_text(
            f"✅ Seed untuk '{target_key}' berhasil ditambahkan/diperbarui!",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("➕ Tambah Lagi", callback_data="tambah_seed")],
                    [InlineKeyboardButton("🔒 Kembali ke Menu Seed", callback_data="kelola_seed")],
                ]
            ),
        )
        return SEED_NAMA
    except Exception as e:
        print(f"[KelolaSeed][ERROR] Gagal menyimpan seed: {e}")
        await update.message.reply_text(f"❌ Gagal menyimpan seed: {e}")
        return ConversationHandler.END


async def lihat_seed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    seeds = load_seed_phrases() or {}

    if not seeds:
        text = "❌ Tidak ada seed phrase."
    else:
        lines = []
        for i, (name, seed) in enumerate(sorted(seeds.items()), 1):
            words = seed.split()
            if len(words) > 6:
                masked = " ".join(words[:4]) + " … " + " ".join(words[-2:])
            else:
                masked = seed
            lines.append(f"{i}. {name} | Seed: {masked}")

        text = f"📋 Daftar Seed Phrase ({len(seeds)} total):\n\n" + "\n".join(lines)

        if len(text) > 4000:
            chunks = [text[i : i + 4000] for i in range(0, len(text), 4000)]
            for chunk in chunks:
                await context.bot.send_message(
                    chat_id=q.message.chat.id,
                    text=f"<pre>{html.escape(chunk)}</pre>",
                    parse_mode="HTML",
                )
            text = ""

    if text:
        await context.bot.send_message(
            chat_id=q.message.chat.id, text=f"<pre>{html.escape(text)}</pre>", parse_mode="HTML"
        )

    await context.bot.send_message(
        chat_id=q.message.chat.id,
        text="📍 Pilih menu:",
        reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
    )
    return ConversationHandler.END


async def seed_edit_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    seeds_map = load_seed_phrases() or {}
    if not seeds_map:
        await q.edit_message_text(
            "❌ Tidak ada seed.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📍 Menu Seed", callback_data="kelola_seed")]]
            ),
        )
        return SEED_MENU
    names = sorted(list(seeds_map.keys()), key=lambda x: (x or "").lower())
    context.user_data["seed_names"] = names
    kb = [
        [InlineKeyboardButton(f"✏️ {n}", callback_data=f"seed_edit_{i}")]
        for i, n in enumerate(names)
    ]
    kb.append([InlineKeyboardButton("🔙 Kembali", callback_data="kelola_seed")])
    await q.edit_message_text("✏️ Pilih seed untuk diedit:", reply_markup=InlineKeyboardMarkup(kb))
    return SEED_PILIH_AKUN


async def seed_delete_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    seeds_map = load_seed_phrases() or {}
    if not seeds_map:
        await q.edit_message_text(
            "❌ Tidak ada seed.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📍 Menu Seed", callback_data="kelola_seed")]]
            ),
        )
        return SEED_MENU
    names = sorted(list(seeds_map.keys()), key=lambda x: (x or "").lower())
    context.user_data["seed_names"] = names
    kb = [
        [InlineKeyboardButton(f"🗑️ {n}", callback_data=f"seed_del_{i}")] for i, n in enumerate(names)
    ]
    kb.append([InlineKeyboardButton("🔙 Kembali", callback_data="kelola_seed")])
    await q.edit_message_text("🗑️ Pilih seed untuk dihapus:", reply_markup=InlineKeyboardMarkup(kb))
    return SEED_PILIH_AKUN


async def seed_edit_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    names = context.user_data.get("seed_names") or []
    try:
        idx = int(q.data.split("_")[-1])
    except Exception:
        idx = -1
    if idx < 0 or idx >= len(names):
        await q.edit_message_text(
            "❌ Pilihan tidak valid.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📍 Menu Seed", callback_data="kelola_seed")]]
            ),
        )
        return SEED_MENU
    target = names[idx]
    context.user_data["seed_nama"] = target  # reuse alur input seed_phrase
    await q.edit_message_text(
        f"✍️ Masukkan seed baru untuk '{target}' (12 kata):",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Batal", callback_data="kelola_seed")]]
        ),
    )
    return SEED_PHRASE


async def seed_delete_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    names = context.user_data.get("seed_names") or []
    try:
        idx = int(q.data.split("_")[-1])
    except Exception:
        idx = -1
    if idx < 0 or idx >= len(names):
        await q.edit_message_text(
            "❌ Pilihan tidak valid.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📍 Menu Seed", callback_data="kelola_seed")]]
            ),
        )
        return SEED_MENU
    target = names[idx]
    seeds_map = load_seed_phrases() or {}
    if target in seeds_map:
        seeds_map.pop(target, None)
        save_seed_phrases(seeds_map)
        await q.edit_message_text(
            f"✅ Seed untuk '{target}' telah dihapus.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📍 Menu Seed", callback_data="kelola_seed")]]
            ),
        )
    else:
        await q.edit_message_text(
            "❌ Seed tidak ditemukan.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📍 Menu Seed", callback_data="kelola_seed")]]
            ),
        )
    return SEED_MENU


kelola_seed_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(kelola_seed_start, pattern="^kelola_seed$")],
    states={
        SEED_MENU: [
            CallbackQueryHandler(tambah_seed_start, pattern="^(tambah_seed|seed_tambah)$"),
            CallbackQueryHandler(lihat_seed, pattern="^lihat_seed$"),
            CallbackQueryHandler(seed_edit_list, pattern="^edit_seed$"),
            CallbackQueryHandler(seed_delete_list, pattern="^hapus_seed$"),
        ],
        SEED_PILIH_AKUN: [
            CallbackQueryHandler(seed_pilih_akun, pattern="^(manual|pilih_\\d+)$"),
            CallbackQueryHandler(seed_edit_select, pattern="^seed_edit_\\d+$"),
            CallbackQueryHandler(seed_delete_select, pattern="^seed_del_\\d+$"),
        ],
        SEED_NAMA: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, seed_nama),
            CallbackQueryHandler(tambah_seed_start, pattern="^(tambah_seed|seed_tambah)$"),
        ],
        SEED_PHRASE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, seed_phrase),
            CallbackQueryHandler(tambah_seed_start, pattern="^(tambah_seed|seed_tambah)$"),
        ],
    },
    fallbacks=[CallbackQueryHandler(kembali_ke_menu, pattern="^back$")],
    per_chat=True,
)

# ==== VALIDASI TOKEN HANDLERS ====


async def validasi_token_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    akun = load_accounts() or []

    if not akun:
        await q.edit_message_text("❌ Tidak ada akun untuk divalidasi.")
        await q.message.reply_text(
            "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
        )
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("🚀 Mulai Validasi", callback_data="start_validasi")],
        [InlineKeyboardButton("🔙 Batal", callback_data="back")],
    ]

    text = (
        "🔐 <b>Validasi Token</b>\n\n"
        f"📊 Total akun: <b>{len(akun)}</b>\n\n"
        "Fitur ini akan:\n"
        "• Mengecek status semua token\n"
        "• Mendeteksi duplikasi nama/token\n"
        "• Menampilkan seed phrase (disamarkan) untuk akun invalid\n\n"
        "Lanjutkan?"
    )
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return VALIDASI_CONFIRM


# Helper: kirim <pre> panjang aman (pecah <4096 char)
async def _send_pre(context, chat_id: int, text: str):
    import html

    CHUNK = 3500  # aman di bawah limit Telegram
    for i in range(0, len(text), CHUNK):
        chunk = text[i : i + CHUNK]
        await context.bot.send_message(
            chat_id=chat_id, text=f"<pre>{html.escape(chunk)}</pre>", parse_mode="HTML"
        )


async def validasi_token_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import os
    from datetime import datetime

    q = update.callback_query
    await q.answer()
    await q.edit_message_text("🔐 Sedang memvalidasi token... Mohon tunggu.")

    try:
        akun = load_accounts() or []
        invalid_accounts = []
        valid_accounts = []

        # ===== Deteksi duplikasi =====
        token_duplicates = {}  # token -> [(idx, name)]
        name_duplicates = {}  # name_lower -> [(idx, name)]

        for i, acc in enumerate(akun):
            token = (acc.get("token") or "").strip()
            name = (acc.get("name") or "").strip()

            if token:
                token_duplicates.setdefault(token, []).append((i, name))

            if name:
                name_lower = name.lower()
                name_duplicates.setdefault(name_lower, []).append((i, name))

        duplicate_tokens = {k: v for k, v in token_duplicates.items() if len(v) > 1}
        duplicate_names = {k: v for k, v in name_duplicates.items() if len(v) > 1}

        # ===== Cek validitas token cepat =====
        for acc in akun:
            try:
                valid, reason = validate_token_requests_fast(acc.get("token", ""))
            except Exception as e:
                valid, reason = False, f"Error: {e}"

            if valid:
                valid_accounts.append(acc)
            else:
                invalid_accounts.append({"account": acc, "reason": reason})

            # beri kesempatan event loop agar UI bot responsif
            await asyncio.sleep(0)

        seed_map = load_seed_phrases() or {}
        seed_lower_map = {str(k).lower(): v for k, v in seed_map.items()}

        # ===== Simpan token invalid ke file invalid.txt =====
        if invalid_accounts:
            invalid_file_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "invalid.txt"
            )
            with open(invalid_file_path, "w", encoding="utf-8") as f:
                f.write(
                    f"# DAFTAR TOKEN INVALID - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                )
                f.write(f"# Total akun invalid: {len(invalid_accounts)}\n")
                f.write("# Format: nama_akun=token_invalid\n\n")
                for item in invalid_accounts:
                    acc = item["account"]
                    name = acc.get("name", "Unknown")
                    token = acc.get("token", "")
                    reason = item.get("reason", "Unknown error")
                    f.write(f"# {name} - Alasan: {reason}\n")
                    f.write(f"{name}={token}\n\n")

            await context.bot.send_message(
                chat_id=q.message.chat.id,
                text=f"💾 Token invalid disimpan ke file: <b>invalid.txt</b>\n📁 Lokasi: <code>{invalid_file_path}</code>",
                parse_mode="HTML",
            )

        # ===== Ringkasan singkat =====
        summary = (
            "📋 <b>Hasil Validasi Token</b>\n\n"
            f"• Total akun     : <b>{len(akun)}</b>\n"
            f"• Token valid    : <b>{len(valid_accounts)}</b>\n"
            f"• Token invalid  : <b>{len(invalid_accounts)}</b>\n"
        )

        if duplicate_tokens or duplicate_names:
            summary += "• Duplikasi      : ditemukan\n"
        else:
            summary += "• Duplikasi      : tidak ada\n"

        await context.bot.send_message(chat_id=q.message.chat.id, text=summary, parse_mode="HTML")

        # ===== Laporan duplikasi detail =====
        if duplicate_tokens or duplicate_names:
            report = "🔍 LAPORAN DUPLIKASI DETAIL:\n\n"

            if duplicate_tokens:
                report += f"🔑 TOKEN KEMBAR ({len(duplicate_tokens)} grup):\n"
                for i, (token, accounts) in enumerate(duplicate_tokens.items(), 1):
                    if i > 10:
                        report += f"... dan {len(duplicate_tokens) - 10} grup lainnya\n"
                        break
                    token_preview = f"{token[:8]}...{token[-8:]}" if len(token) > 16 else token
                    report += f"{i}. Token {token_preview}:\n"
                    for idx, name in accounts:
                        report += f"   • {name} (#{idx + 1})\n"
                    report += "\n"

            if duplicate_names:
                report += f"👤 NAMA KEMBAR ({len(duplicate_names)} grup):\n"
                for i, (name_lower, accounts) in enumerate(duplicate_names.items(), 1):
                    if i > 10:
                        report += f"... dan {len(duplicate_names) - 10} grup lainnya\n"
                        break
                    original = accounts[0][1] if accounts else name_lower
                    report += f"{i}. Nama '{original}':\n"
                    for idx, name in accounts:
                        report += f"   • {name} (#{idx + 1})\n"
                    report += "\n"

            report += "💡 Disarankan hapus/ganti item yang duplikat."
            await _send_pre(context, q.message.chat.id, report)
        else:
            await context.bot.send_message(
                chat_id=q.message.chat.id, text="✅ Tidak ada duplikasi nama/token."
            )

        # ===== List invalid token (ringkas) =====
        if invalid_accounts:
            lines = ["⚠️ Invalid token (maks 30 baris):"]
            for i, item in enumerate(invalid_accounts[:30], 1):
                acc = item["account"]
                nm = (acc.get("name") or "-").strip()
                rsn = str(item.get("reason", ""))[:80]
                has_seed = "✅" if nm.lower() in seed_lower_map else "❌"
                masked = ""
                if has_seed == "✅":
                    words = (seed_lower_map.get(nm.lower()) or "").split()
                    masked = (
                        " ".join(words[:4]) + " … " + " ".join(words[-2:])
                        if len(words) > 6
                        else " ".join(words)
                    )
                line = f"{i}. {nm} | seed:{has_seed}"
                if masked:
                    line += f" | {masked}"
                line += f" | {rsn}"
                lines.append(line)
            await _send_pre(context, q.message.chat.id, "\n".join(lines))

    except Exception as e:
        await context.bot.send_message(chat_id=q.message.chat.id, text=f"❌ Error: {e}")

    # Kembali ke menu
    await context.bot.send_message(
        chat_id=q.message.chat.id,
        text="📍 Pilih menu:",
        reply_markup=InlineKeyboardMarkup(_main_menu_keyboard()),
    )
    return ConversationHandler.END


validasi_token_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(validasi_token_start, pattern="^validasi_token$")],
    states={
        VALIDASI_CONFIRM: [CallbackQueryHandler(validasi_token_execute, pattern="^start_validasi$")]
    },
    fallbacks=[CallbackQueryHandler(kembali_ke_menu, pattern="^back$")],
    per_chat=True,
)


# ==== SAFE CALLBACK HANDLER ====
async def safe_answer_callback(query):
    """Safely answer callback query"""
    try:
        await query.answer()
    except Exception:
        pass


# ==== TOMBOL MENU HANDLER ====
async def tombol_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)

    routes = {
        "klaimkupon": klaimkupon_start,
        "cekakun": cekakun_start,
        "tambahakun": tambahakun_start,
        "hapusakun": hapusakun_start,
        "wheel": wheel_start,
        "editakun": editakun_start,
        "upgrader": upgrader_start,
        "refreshtoken": refreshtoken_start,
        "kelola_akun": kelola_akun_start,
        "kelola_seed": kelola_seed_start,
        "validasi_token": validasi_token_start,
        "hapus_level_0": hapus_level_0_handler,
        "main_menu": back_to_main_menu,  # pastikan fungsi ini ada
    }

    handler = routes.get(q.data)
    if handler:
        return await handler(update, context)
    else:
        await q.edit_message_text("❌ Perintah tidak dikenali.")
        return ConversationHandler.END


# ==== HAPUS LEVEL 0 HANDLER ====
async def hapus_level_0_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # Ambil data level 0 accounts yang disimpan sebelumnya
    level_0_accounts = context.application.bot_data.get("level_0_accounts", []) or []

    if not level_0_accounts:
        await q.edit_message_text(
            "❌ Tidak ada data akun Level 0 yang tersimpan.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")]]
            ),
        )
        return ConversationHandler.END

    try:
        # Load akun saat ini
        akun = load_accounts() or []
        if not akun:
            await q.edit_message_text(
                "❌ Tidak ada akun untuk dihapus.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")]]
                ),
            )
            return ConversationHandler.END

        # Cari dan hapus akun level 0
        deleted_accounts = []
        remaining_accounts = []

        names_to_delete = set(level_0_accounts)
        for acc in akun:
            nm = acc.get("name")
            if nm in names_to_delete:
                deleted_accounts.append(nm)
            else:
                remaining_accounts.append(acc)

        if not deleted_accounts:
            await q.edit_message_text(
                "❌ Tidak ditemukan akun Level 0 untuk dihapus.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")]]
                ),
            )
            return ConversationHandler.END

        # Simpan akun yang tersisa
        save_accounts(remaining_accounts)

        # Clear data level 0 accounts
        context.application.bot_data["level_0_accounts"] = []

        # Tampilkan hasil
        deleted_text = "\n".join([f"• {name}" for name in deleted_accounts])

        keyboard = [
            [InlineKeyboardButton("🏅 Cek Level Lagi", callback_data="cekakun")],
            [InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")],
        ]

        await q.edit_message_text(
            f"✅ Berhasil menghapus {len(deleted_accounts)} akun Level 0:\n\n{deleted_text}\n\n"
            f"📊 Total akun tersisa: {len(remaining_accounts)}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        return ConversationHandler.END

    except Exception as e:
        await q.edit_message_text(
            f"❌ Gagal menghapus akun Level 0: {str(e)}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("📍 Menu Utama", callback_data="main_menu")]]
            ),
        )
        return ConversationHandler.END

    await q.answer()

    # Ambil data level 0 accounts yang disimpan sebelumnya
    level_0_accounts = context.application.bot_data.get("level_0_accounts", [])

    if not level_0_accounts:
        await q.edit_message_text("❌ Tidak ada data akun Level 0 untuk dihapus.")
        await q.message.reply_text(
            "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
        )
        return

    try:
        # Load akun saat ini
        akun = load_accounts()
        deleted_accounts = []
        deleted_accounts_data = []  # Untuk menyimpan data lengkap akun yang dihapus
        remaining_accounts = []

        # Filter akun yang akan dihapus (hanya level 0 dengan token valid)
        for acc in akun:
            if acc["name"] in level_0_accounts:
                # Double check: pastikan token masih valid dan level masih 0
                try:
                    token_valid, reason = validate_token_requests_fast(acc["token"])
                    if token_valid:
                        vip = get_vip(acc["token"])
                        if vip and vip.get("currentLevel"):
                            level_name = vip.get("currentLevel", {}).get("name", "0")
                            try:
                                level = int(level_name)
                            except:
                                level = 0
                        else:
                            level = 0

                        # Hanya hapus jika benar-benar level 0
                        if level == 0:
                            deleted_accounts.append(acc["name"])
                            deleted_accounts_data.append(acc)  # Simpan data lengkap
                        else:
                            remaining_accounts.append(acc)
                    else:
                        # Token tidak valid, jangan hapus (biarkan untuk refresh token)
                        remaining_accounts.append(acc)
                except:
                    # Error saat cek, jangan hapus untuk keamanan
                    remaining_accounts.append(acc)
            else:
                remaining_accounts.append(acc)

        if deleted_accounts:
            # Simpan akun yang dihapus ke file txt
            import os
            from datetime import datetime

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"akun_level0_dihapus_{timestamp}.txt"
            filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)

            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(
                        f"# Akun Level 0 yang dihapus pada {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                    )
                    f.write(f"# Total akun dihapus: {len(deleted_accounts_data)}\n")
                    f.write("# Format: Nama=Token\n\n")

                    for acc in deleted_accounts_data:
                        f.write(f"{acc['name']}={acc['token']}\n")

                backup_success = True
                backup_message = f"💾 Akun yang dihapus telah disimpan ke: {filename}"
            except Exception as e:
                backup_success = False
                backup_message = f"⚠️ Gagal menyimpan backup: {str(e)}"

            # Simpan akun yang tersisa (tidak termasuk yang level 0)
            save_accounts(remaining_accounts)

            deleted_text = "\n".join([f"• {name}" for name in deleted_accounts])
            result_message = (
                f"✅ Berhasil menghapus {len(deleted_accounts)} akun Level 0:\n\n"
                f"{deleted_text}\n\n"
                f"📊 Sisa akun: {len(remaining_accounts)}\n\n"
                f"{backup_message}"
            )

            await q.edit_message_text(result_message)
        else:
            await q.edit_message_text(
                "⚠️ Tidak ada akun Level 0 yang dapat dihapus (mungkin sudah naik level atau token invalid)."
            )

        # Clear data level 0 accounts
        context.application.bot_data.pop("level_0_accounts", None)

        await q.message.reply_text(
            "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
        )

    except Exception as e:
        await q.edit_message_text(f"❌ Error saat menghapus akun Level 0: {str(e)}")
        await q.message.reply_text(
            "📍 Pilih menu:", reply_markup=InlineKeyboardMarkup(_main_menu_keyboard())
        )


# ==== SAFE CALLBACK HANDLER ====
async def safe_answer_callback(query):
    """Safely answer callback query"""
    try:
        await query.answer()
    except Exception:
        pass  # Ignore expired queries


async def tombol_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await safe_answer_callback(q)
    routes = {
        "klaimkupon": klaimkupon_start,
        "cekakun": cekakun_start,
        "tambahakun": tambahakun_start,
        "hapusakun": hapusakun_start,
        "wheel": wheel_start,
        "editakun": editakun_start,
        "upgrader": upgrader_start,
        "refreshtoken": refreshtoken_start,
        "kelola_akun": kelola_akun_start,
        "kelola_seed": kelola_seed_start,
        "validasi_token": validasi_token_start,
        "hapus_level_0": hapus_level_0_handler,
        "main_menu": back_to_main_menu,
    }
    handler = routes.get(q.data)
    if handler:
        return await handler(update, context)
    await q.edit_message_text("🚧 Menu belum tersedia.")
    return ConversationHandler.END


def start_bot():
    print("🤖 Memulai Bot Telegram FlipBot Enhanced Version...")
    print("✨ Fitur yang tersedia:")
    print("   • 🎁 Klaim Kupon (dengan smart retry system)")
    print("   • 📊 Cek Akun (saldo, wager, level)")
    print("   • 🎡 WHEEL (auto bet)")
    print("   • 🚀 Upgrader (headless mode)")
    print("   • ⚙️ Kelola Akun (tambah, edit, hapus, lihat)")
    print("   • 🔒 Kelola Seed (tambah, lihat seed phrase)")
    print("   • 🔐 Validasi Token (cek status + seed)")
    print("   • 🔄 Refresh Token (otomatis login ulang)")
    print("   • 🛡️ Safe callback handling")
    print("   • 📱 User-friendly interface")

    app = ApplicationBuilder().token(TG_TOKEN).build()

    # Register conversation handlers (order matters!)
    app.add_handler(cekakun_conv)
    app.add_handler(CallbackQueryHandler(fitur_utama_start, pattern="^fitur_utama$"))
    app.add_handler(CallbackQueryHandler(menu_utama, pattern="^main_menu$"))
    app.add_handler(claimmonthly_conv)
    app.add_handler(wheel_conv)
    app.add_handler(auto_rain_conv)
    app.add_handler(klaim_conv)
    app.add_handler(tambah_conv)
    app.add_handler(hapus_conv)
    app.add_handler(editakun_conv)
    app.add_handler(upgrader_conv)
    app.add_handler(refresh_conv)
    app.add_handler(kelola_akun_conv)
    app.add_handler(kelola_seed_conv)
    app.add_handler(validasi_token_conv)

    # Simple commands
    app.add_handler(CommandHandler("info", info))
    app.add_handler(CommandHandler("akun", akun_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))

    # Stop buttons
    app.add_handler(CallbackQueryHandler(stop_bet_handler, pattern="^stop_bet$"))
    app.add_handler(CallbackQueryHandler(stop_rain_handler, pattern="^stop_rain$"))
    app.add_handler(CallbackQueryHandler(stop_claim_handler, pattern="^stop_claim$"))
    app.add_handler(CallbackQueryHandler(stop_cek_handler, pattern="^stop_cek$"))
    app.add_handler(CallbackQueryHandler(stop_upgrade_handler, pattern="^stop_upgrade$"))
    app.add_handler(CallbackQueryHandler(stop_refresh_handler, pattern="^stop_refresh$"))
    app.add_handler(CallbackQueryHandler(hapus_level_0_handler, pattern="^hapus_level_0$"))

    # Refresh token handlers

    # Main menu command
    app.add_handler(CommandHandler("start", menu_utama))

    # Menu navigation (updated pattern)
    app.add_handler(
        CallbackQueryHandler(
            tombol_menu_handler,
            pattern="^(klaimkupon|cekakun|tambahakun|hapusakun|wheel|editakun|upgrader|refreshtoken|kelola_akun|kelola_seed|validasi_token|hapus_level_0|main_menu)$",
        )
    )

    # Error handler
    app.add_error_handler(error_handler)

    print("✅ Bot siap digunakan!")
    print("💡 Tekan Ctrl+C untuk menghentikan bot")

    try:
        app.run_polling()
    except KeyboardInterrupt:
        print("\n🛑 Bot dihentikan oleh user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        print("🔄 Membersihkan resources...")
        print("✅ Bot telah dihentikan dengan aman!")


if __name__ == "__main__":
    start_bot()
