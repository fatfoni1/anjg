# ========== GUI MODULE: gui.py ==========
import asyncio
import threading
import random
import time
import tkinter as tk

import customtkinter as ctk
import requests
from playwright.async_api import async_playwright

from asf_core import (
    load_accounts,
    save_accounts,
    API_REDEEM_URL,
    inject_and_validate_token_fast,
    get_profile,
    get_balance,
    get_vip,
    validate_token_requests_fast,
    load_seed_phrases,
    save_seed_phrases,
)
from asf_wheel import jalankan_auto_bet
from asf_upgrader import jalankan_upgrader

# Tampilan
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Flip.gg Tools")
        self.geometry("820x780")
        self.resizable(False, False)

        # data & state
        self.akun = load_accounts()
        self.seeds = load_seed_phrases() or {}

        # wheel
        self._wheel_running = False
        self._wheel_stop_event = threading.Event()
        self._wheel_thread = None
        self._wheel_delay_sec = 0.0

        # kupon
        self._kupon_running = False
        self._kupon_stop_event = threading.Event()
        self._kupon_thread = None
        self._kupon_delay_sec = 0.0

        # upgrader
        self._upg_running = False
        self._upg_stop_event = threading.Event()
        self._upg_thread = None
        self._upg_delay_sec = 0.0

        # cek akun
        self._cek_running = False
        self._cek_stop_event = threading.Event()
        self._cek_thread = None

        self._build_ui()

    # ===================== UI ROOT =====================
    def _build_ui(self):
        container = ctk.CTkFrame(self)
        container.pack(padx=16, pady=16, fill="both", expand=True)

        self.tabs = ctk.CTkTabview(container, width=780, height=720)
        self.tabs.pack(fill="both", expand=True)

        self.tab_wheel = self.tabs.add("🎡 Wheel")
        self.tab_kupon = self.tabs.add("🎁 Klaim Kupon")
        self.tab_upgrader = self.tabs.add("🚀 Upgrader")
        self.tab_cek_akun = self.tabs.add("📊 Cek Akun")
        self.tab_kelola_akun = self.tabs.add("⚙️ Kelola Akun")
        self.tab_kelola_seed = self.tabs.add("🔒 Kelola Seed")

        self._build_wheel_tab()
        self._build_kupon_tab()
        self._build_upgrader_tab()
        self._build_cek_akun_tab()
        self._build_kelola_akun_tab()
        self._build_kelola_seed_tab()

    # ===================== COMMON HELPERS =====================
    def _log_textbox_append(self, textbox: ctk.CTkTextbox, text: str):
        try:
            textbox.configure(state="normal")
            textbox.insert(tk.END, text + "\n")
            textbox.see(tk.END)
        finally:
            textbox.configure(state="disabled")

    def _human_delay(self, sec: float, stop_event: threading.Event):
        if sec <= 0:
            return
        start = time.monotonic()
        while not stop_event.is_set():
            remaining = sec - (time.monotonic() - start)
            if remaining <= 0:
                break
            time.sleep(min(0.25, max(0.05, remaining)))

    async def _await_delay(self, delay: float, stop_event: threading.Event):
        if delay <= 0:
            return
        start = time.monotonic()
        while not stop_event.is_set():
            remaining = delay - (time.monotonic() - start)
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.25, max(0.05, remaining)))

    def _show_message(self, message: str):
        """Tampilkan pesan ke user"""
        print(message)  # Untuk sementara print ke console

    def _update_info_label(self):
        """Update label info jumlah akun"""
        self.info_label.configure(text=f"📊 Total akun: {len(self.akun)}")

    def _update_seed_info_label(self):
        """Update label info jumlah seed"""
        self.seed_info_label.configure(text=f"🔑 Total seed phrase: {len(self.seeds)}")

    # ===================== Wheel Tab =====================
    def _build_wheel_tab(self):
        frame = ctk.CTkFrame(self.tab_wheel)
        frame.pack(padx=12, pady=12, fill="both", expand=True)

        ctk.CTkLabel(frame, text=f"🔢 Jumlah akun tersedia: {len(self.akun)}").pack(pady=(0, 10))

        jumlah_values = ["Semua"] + [str(i) for i in range(1, len(self.akun) + 1)]
        self.w_jumlah_menu = ctk.CTkOptionMenu(frame, values=jumlah_values)
        self.w_jumlah_menu.set("Semua")
        self.w_jumlah_menu.pack(pady=(0, 10))

        ctk.CTkLabel(frame, text="Pilih multiplier:").pack()
        self.w_mult_mode = ctk.CTkOptionMenu(frame, values=["Perkalian tetap", "Perkalian acak"])
        self.w_mult_mode.set("Perkalian tetap")
        self.w_mult_mode.pack(pady=(0, 10))
        self.w_mult_fix = ctk.CTkOptionMenu(frame, values=["2", "3", "5", "50"])
        self.w_mult_fix.set("2")
        self.w_mult_fix.pack(pady=(0, 10))

        ctk.CTkLabel(frame, text="Metode Bet:").pack()
        self.w_bet_mode = ctk.CTkOptionMenu(frame, values=["All-in saldo", "Bet manual"])
        self.w_bet_mode.set("All-in saldo")
        self.w_bet_mode.pack(pady=(0, 10))
        self.w_bet_manual = ctk.CTkEntry(frame, placeholder_text="Masukkan jumlah bet manual (min 0.1)")
        self.w_bet_manual.pack(pady=(0, 10))

        ctk.CTkLabel(frame, text="Mode browser:").pack()
        self.w_browser = ctk.CTkOptionMenu(frame, values=["Tampilkan", "Tidak tampilkan"])
        self.w_browser.set("Tampilkan")
        self.w_browser.pack(pady=(0, 10))

        ctk.CTkLabel(frame, text="Delay antar akun (detik):").pack()
        self.w_delay = ctk.CTkEntry(frame, placeholder_text="Misal: 2 atau 2.5")
        self.w_delay.pack(pady=(0, 10))

        btn_frame = ctk.CTkFrame(frame)
        btn_frame.pack(pady=(5, 10))
        self.w_start_btn = ctk.CTkButton(btn_frame, text="Jalankan", width=140, command=self._wheel_start_thread)
        self.w_start_btn.pack(side="left", padx=6)
        self.w_stop_btn = ctk.CTkButton(btn_frame, text="Stop", width=120, command=self._wheel_stop)
        self.w_stop_btn.pack(side="left", padx=6)

        self.w_log = ctk.CTkTextbox(frame, width=760, height=320)
        self.w_log.pack(pady=10)
        self.w_log.configure(state="disabled")
        self._wheel_update_buttons()

    def _wheel_update_buttons(self):
        self.w_start_btn.configure(state=("disabled" if self._wheel_running else "normal"))
        self.w_stop_btn.configure(state=("normal" if self._wheel_running else "disabled"))

    def _wheel_log_async(self, text: str):
        self.w_log.after(0, self._log_textbox_append, self.w_log, text)

    def _wheel_start_thread(self):
        if self._wheel_running:
            self._wheel_log_async("⚠️ Proses masih berjalan.")
            return
        if not self.akun:
            self._wheel_log_async("❌ Tidak ada akun. Tambahkan akun terlebih dahulu.")
            return
        delay_txt = (self.w_delay.get() or "0").strip()
        try:
            self._wheel_delay_sec = float(delay_txt)
            if self._wheel_delay_sec < 0:
                raise ValueError()
        except Exception:
            self._wheel_log_async("❌ Delay harus berupa angka (>= 0), contoh: 2 atau 2.5")
            return
        self._wheel_stop_event.clear()
        self._wheel_running = True
        self._wheel_update_buttons()
        self._wheel_log_async("🚀 Menjalankan auto bet…")
        self._wheel_thread = threading.Thread(target=self._wheel_run, daemon=True)
        self._wheel_thread.start()

    def _wheel_stop(self):
        if not self._wheel_running:
            self._wheel_log_async("ℹ️ Tidak ada proses berjalan.")
            return
        self._wheel_stop_event.set()
        self._wheel_log_async("⏹️ Menghentikan… tunggu hingga proses aman berhenti.")

    def _wheel_run(self):
        try:
            jumlah = self.w_jumlah_menu.get()
            if jumlah == "Semua":
                selected_akun = self.akun[:]
            else:
                try:
                    jumlah_int = int(jumlah)
                except ValueError:
                    self._wheel_log_async("❌ Jumlah akun tidak valid.")
                    return
                selected_akun = self.akun[:jumlah_int]

            use_random_mult = self.w_mult_mode.get() == "Perkalian acak"
            if use_random_mult:
                multiplier_value = random.choice([2, 3, 5, 50])
                self._wheel_log_async(f"🎲 Menggunakan multiplier acak: x{multiplier_value}")
            else:
                multiplier_value = int(self.w_mult_fix.get())

            allin = self.w_bet_mode.get() == "All-in saldo"
            if allin:
                bet_amount = "0"
            else:
                bet_amount = (self.w_bet_manual.get() or "0").strip()
                try:
                    if float(bet_amount) < 0.1:
                        self._wheel_log_async("❌ Bet manual minimal 0.1")
                        return
                except ValueError:
                    self._wheel_log_async("❌ Bet manual harus angka.")
                    return

            headless = self.w_browser.get() == "Tidak tampilkan"

            # Jalankan per akun agar bisa disisipkan delay antar akun
            for i, acc in enumerate(selected_akun, 1):
                if self._wheel_stop_event.is_set():
                    break
                self._wheel_log_async(f"▶️ Akun {i}/{len(selected_akun)}: {acc['name']}")
                asyncio.run(
                    jalankan_auto_bet(
                        multiplier_value,
                        bet_amount,
                        allin,
                        headless,
                        None,
                        akun_list=[acc],
                        stop_event=self._wheel_stop_event,
                    )
                )
                if self._wheel_stop_event.is_set():
                    break
                if i < len(selected_akun) and self._wheel_delay_sec > 0:
                    self._wheel_log_async(f"⏳ Delay {self._wheel_delay_sec} detik sebelum akun berikutnya…")
                    self._human_delay(self._wheel_delay_sec, self._wheel_stop_event)

            if self._wheel_stop_event.is_set():
                self._wheel_log_async("🛑 Dihentikan oleh pengguna.")
            else:
                self._wheel_log_async("✅ Selesai.")
        except Exception as e:
            self._wheel_log_async(f"❌ Error: {e}")
        finally:
            self._wheel_running = False
            self._wheel_update_buttons()

    # ===================== Kupon Tab =====================
    def _build_kupon_tab(self):
        frame = ctk.CTkFrame(self.tab_kupon)
        frame.pack(padx=12, pady=12, fill="both", expand=True)

        ctk.CTkLabel(frame, text=f"🔢 Jumlah akun tersedia: {len(self.akun)}").pack(pady=(0, 10))

        jumlah_values = ["Semua"] + [str(i) for i in range(1, len(self.akun) + 1)]
        self.k_jumlah_menu = ctk.CTkOptionMenu(frame, values=jumlah_values)
        self.k_jumlah_menu.set("Semua")
        self.k_jumlah_menu.pack(pady=(0, 10))

        ctk.CTkLabel(frame, text="Delay antar akun (detik):").pack()
        self.k_delay = ctk.CTkEntry(frame, placeholder_text="Misal: 2 atau 2.5")
        self.k_delay.pack(pady=(0, 10))

        ctk.CTkLabel(frame, text="Kode kupon:").pack()
        self.k_kode = ctk.CTkEntry(frame, placeholder_text="Masukkan kode kupon")
        self.k_kode.pack(pady=(0, 10))

        ctk.CTkLabel(frame, text="Mode browser:").pack()
        self.k_browser = ctk.CTkOptionMenu(frame, values=["Tampilkan", "Tidak tampilkan"])
        self.k_browser.set("Tampilkan")
        self.k_browser.pack(pady=(0, 10))

        btn_frame = ctk.CTkFrame(frame)
        btn_frame.pack(pady=(5, 10))
        self.k_start_btn = ctk.CTkButton(btn_frame, text="Mulai Klaim", width=140, command=self._kupon_start_thread)
        self.k_start_btn.pack(side="left", padx=6)
        self.k_stop_btn = ctk.CTkButton(btn_frame, text="Stop", width=120, command=self._kupon_stop)
        self.k_stop_btn.pack(side="left", padx=6)

        self.k_log = ctk.CTkTextbox(frame, width=760, height=320)
        self.k_log.pack(pady=10)
        self.k_log.configure(state="disabled")
        self._kupon_update_buttons()

    def _kupon_update_buttons(self):
        self.k_start_btn.configure(state=("disabled" if self._kupon_running else "normal"))
        self.k_stop_btn.configure(state=("normal" if self._kupon_running else "disabled"))

    def _kupon_log_async(self, text: str):
        self.k_log.after(0, self._log_textbox_append, self.k_log, text)

    def _kupon_start_thread(self):
        if self._kupon_running:
            self._kupon_log_async("⚠️ Proses masih berjalan.")
            return
        if not self.akun:
            self._kupon_log_async("❌ Tidak ada akun. Tambahkan akun terlebih dahulu.")
            return
        kode = (self.k_kode.get() or "").strip()
        if not kode:
            self._kupon_log_async("❌ Kode kupon tidak boleh kosong.")
            return
        delay_txt = (self.k_delay.get() or "0").strip()
        try:
            self._kupon_delay_sec = float(delay_txt)
            if self._kupon_delay_sec < 0:
                raise ValueError()
        except Exception:
            self._kupon_log_async("❌ Delay harus berupa angka (>= 0), contoh: 2 atau 2.5")
            return
        self._kupon_stop_event.clear()
        self._kupon_running = True
        self._kupon_update_buttons()
        self._kupon_log_async(f"🚀 Menjalankan klaim kupon '{kode}'…")
        self._kupon_thread = threading.Thread(target=self._kupon_run, daemon=True)
        self._kupon_thread.start()

    def _kupon_stop(self):
        if not self._kupon_running:
            self._kupon_log_async("ℹ️ Tidak ada proses berjalan.")
            return
        self._kupon_stop_event.set()
        self._kupon_log_async("⏹️ Menghentikan… tunggu hingga proses aman berhenti.")

    def _kupon_run(self):
        try:
            jumlah = self.k_jumlah_menu.get()
            if jumlah == "Semua":
                target = self.akun[:]
            else:
                try:
                    target = self.akun[: int(jumlah)]
                except Exception:
                    self._kupon_log_async("❌ Jumlah akun tidak valid.")
                    return
            delay = getattr(self, "_kupon_delay_sec", 0.0)
            kode = (self.k_kode.get() or "").strip()
            show_browser = self.k_browser.get() == "Tampilkan"

            if show_browser:
                async def run_with_browser():
                    async with async_playwright() as pw:
                        browser = await pw.chromium.launch(headless=False)
                        context = await browser.new_context()
                        page = await context.new_page()
                        ok, already, fail = [], [], []
                        try:
                            for i, acc in enumerate(target, 1):
                                if self._kupon_stop_event.is_set():
                                    self._kupon_log_async("🛑 Dihentikan oleh pengguna.")
                                    break
                                self._kupon_log_async(f"🔐 Akun {i}/{len(target)}: {acc['name']}")

                                # Validasi token cepat via Playwright
                                token_valid, reason = await inject_and_validate_token_fast(page, acc['token'], acc['name'])
                                if not token_valid:
                                    self._kupon_log_async(f"⛔ [{acc['name']}] {reason}")
                                    if i < len(target) and not self._kupon_stop_event.is_set():
                                        await asyncio.sleep(0.2)
                                    continue

                                headers = {
                                    "x-auth-token": acc["token"],
                                    "User-Agent": "Mozilla/5.0",
                                    "Content-Type": "application/json",
                                }
                                loop = asyncio.get_running_loop()
                                require_full_delay = True
                                try:
                                    r = await loop.run_in_executor(
                                        None,
                                        lambda: requests.post(
                                            API_REDEEM_URL,
                                            headers=headers,
                                            json={"code": kode},
                                            timeout=15,
                                        ),
                                    )
                                    if r.status_code == 200:
                                        ok.append(acc["name"]) ; self._kupon_log_async("✅ Sukses klaim")
                                    elif r.status_code == 403:
                                        already.append(acc["name"]) ; self._kupon_log_async("⚠️ Sudah klaim")
                                    elif r.status_code == 400:
                                        fail.append(acc["name"]) ; self._kupon_log_async("❌ Kode salah/tidak aktif")
                                    elif r.status_code == 401:
                                        fail.append(acc["name"]) ; self._kupon_log_async("⛔ Token tidak aktif")
                                        require_full_delay = False
                                    else:
                                        fail.append(acc["name"]) ; self._kupon_log_async(f"❌ Gagal ({r.status_code})")
                                except Exception as e:
                                    fail.append(acc["name"]) ; self._kupon_log_async(f"⛔ Error: {e}")

                                if i < len(target) and not self._kupon_stop_event.is_set():
                                    if require_full_delay and delay > 0:
                                        await self._await_delay(delay, self._kupon_stop_event)
                                    else:
                                        await asyncio.sleep(0.2)
                        finally:
                            await browser.close()
                        self._kupon_log_async(
                            f"\n✅ Selesai. Sukses: {len(ok)}, Sudah: {len(already)}, Gagal: {len(fail)}"
                        )

                asyncio.run(run_with_browser())
            else:
                ok, already, fail = [], [], []
                for i, acc in enumerate(target, 1):
                    if self._kupon_stop_event.is_set():
                        self._kupon_log_async("🛑 Dihentikan oleh pengguna.")
                        break
                    self._kupon_log_async(f"🔐 Akun {i}/{len(target)}: {acc['name']}")

                    headers = {
                        "x-auth-token": acc["token"],
                        "User-Agent": "Mozilla/5.0",
                        "Content-Type": "application/json",
                    }
                    require_full_delay = True
                    try:
                        r = requests.post(
                            API_REDEEM_URL, headers=headers, json={"code": kode}, timeout=15
                        )
                        if r.status_code == 200:
                            ok.append(acc["name"]) ; self._kupon_log_async("✅ Sukses klaim")
                        elif r.status_code == 403:
                            already.append(acc["name"]) ; self._kupon_log_async("⚠️ Sudah klaim")
                        elif r.status_code == 400:
                            fail.append(acc["name"]) ; self._kupon_log_async("❌ Kode salah/tidak aktif")
                        elif r.status_code == 401:
                            fail.append(acc["name"]) ; self._kupon_log_async("⛔ Token tidak aktif")
                            require_full_delay = False
                        else:
                            fail.append(acc["name"]) ; self._kupon_log_async(f"❌ Gagal ({r.status_code})")
                    except Exception as e:
                        fail.append(acc["name"]) ; self._kupon_log_async(f"⛔ Error: {e}")

                    if i < len(target) and not self._kupon_stop_event.is_set():
                        if require_full_delay and delay > 0:
                            self._human_delay(delay, self._kupon_stop_event)
                        else:
                            time.sleep(0.2)

                self._kupon_log_async(
                    f"\n✅ Selesai. Sukses: {len(ok)}, Sudah: {len(already)}, Gagal: {len(fail)}"
                )
        except Exception as e:
            self._kupon_log_async(f"❌ Error: {e}")
        finally:
            self._kupon_running = False
            self._kupon_update_buttons()

    # ===================== Upgrader Tab =====================
    def _build_upgrader_tab(self):
        frame = ctk.CTkFrame(self.tab_upgrader)
        frame.pack(padx=12, pady=12, fill="both", expand=True)

        ctk.CTkLabel(frame, text=f"🔢 Jumlah akun tersedia: {len(self.akun)}").pack(pady=(0, 10))

        jumlah_values = ["Semua"] + [str(i) for i in range(1, len(self.akun) + 1)]
        self.u_jumlah_menu = ctk.CTkOptionMenu(frame, values=jumlah_values)
        self.u_jumlah_menu.set("Semua")
        self.u_jumlah_menu.pack(pady=(0, 10))

        ctk.CTkLabel(frame, text="Search & Item (nama item sama dengan search):").pack()
        self.u_search = ctk.CTkEntry(frame, placeholder_text="Nama item yang dicari (contoh: Ryoma)")
        self.u_search.pack(pady=(0, 10))

        ctk.CTkLabel(frame, text="Bet mode:").pack()
        self.u_bet_mode = ctk.CTkOptionMenu(frame, values=["Manual", "Max"])
        self.u_bet_mode.set("Manual")
        self.u_bet_mode.pack(pady=(0, 10))
        self.u_bet_amount = ctk.CTkEntry(frame, placeholder_text="Jumlah bet (contoh: 0.10)")
        self.u_bet_amount.pack(pady=(0, 10))

        ctk.CTkLabel(frame, text="Mode browser:").pack()
        self.u_browser = ctk.CTkOptionMenu(frame, values=["Tampilkan", "Tidak tampilkan"])
        self.u_browser.set("Tampilkan")
        self.u_browser.pack(pady=(0, 10))

        ctk.CTkLabel(frame, text="Delay antar akun (detik):").pack()
        self.u_delay = ctk.CTkEntry(frame, placeholder_text="Misal: 2 atau 2.5")
        self.u_delay.pack(pady=(0, 10))

        btn_frame = ctk.CTkFrame(frame)
        btn_frame.pack(pady=(5, 10))
        self.u_start_btn = ctk.CTkButton(
            btn_frame, text="Jalankan Upgrader", width=160, command=self._upg_start_thread
        )
        self.u_start_btn.pack(side="left", padx=6)
        self.u_stop_btn = ctk.CTkButton(btn_frame, text="Stop", width=120, command=self._upg_stop)
        self.u_stop_btn.pack(side="left", padx=6)

        self.u_log = ctk.CTkTextbox(frame, width=760, height=320)
        self.u_log.pack(pady=10)
        self.u_log.configure(state="disabled")
        self._upg_update_buttons()

    def _upg_update_buttons(self):
        self.u_start_btn.configure(state=("disabled" if self._upg_running else "normal"))
        self.u_stop_btn.configure(state=("normal" if self._upg_running else "disabled"))

    def _upg_log_async(self, text: str):
        self.u_log.after(0, self._log_textbox_append, self.u_log, text)

    def _upg_start_thread(self):
        if self._upg_running:
            self._upg_log_async("⚠️ Proses masih berjalan.")
            return
        if not self.akun:
            self._upg_log_async("❌ Tidak ada akun. Tambahkan akun terlebih dahulu.")
            return
        search_query = (self.u_search.get() or "").strip()
        if not search_query:
            self._upg_log_async("❌ Search & Item tidak boleh kosong.")
            return

        bet_mode_ui = self.u_bet_mode.get().lower()
        bet_mode = "manual" if bet_mode_ui == "manual" else "max"
        bet_amount = (self.u_bet_amount.get() or "0.10").strip()
        if bet_mode == "manual":
            try:
                if float(bet_amount) <= 0:
                    self._upg_log_async("❌ Jumlah bet harus lebih dari 0")
                    return
            except Exception:
                self._upg_log_async("❌ Jumlah bet harus angka")
                return
        headless = self.u_browser.get() == "Tidak tampilkan"

        delay_txt = (self.u_delay.get() or "0").strip()
        try:
            self._upg_delay_sec = float(delay_txt)
            if self._upg_delay_sec < 0:
                raise ValueError()
        except Exception:
            self._upg_log_async("❌ Delay harus berupa angka (>= 0), contoh: 2 atau 2.5")
            return

        self._upg_stop_event.clear()
        self._upg_running = True
        self._upg_update_buttons()
        self._upg_log_async(f"🚀 Menjalankan Upgrader untuk item: {search_query}")
        self._upg_thread = threading.Thread(
            target=self._upg_run,
            args=(search_query, bet_mode, bet_amount, headless),
            daemon=True,
        )
        self._upg_thread.start()

    def _upg_stop(self):
        if not self._upg_running:
            self._upg_log_async("ℹ️ Tidak ada proses berjalan.")
            return
        self._upg_stop_event.set()
        self._upg_log_async("⏹️ Menghentikan… tunggu hingga proses aman berhenti.")

    def _upg_run(self, search_query: str, bet_mode: str, bet_amount: str, headless: bool):
        try:
            jumlah = self.u_jumlah_menu.get()
            if jumlah == "Semua":
                selected_akun = self.akun[:]
            else:
                try:
                    jumlah_int = int(jumlah)
                except ValueError:
                    self._upg_log_async("❌ Jumlah akun tidak valid.")
                    return
                selected_akun = self.akun[:jumlah_int]

            # Jalankan per akun agar bisa disisipkan delay antar akun
            for i, acc in enumerate(selected_akun, 1):
                if self._upg_stop_event.is_set():
                    break
                self._upg_log_async(f"▶️ Akun {i}/{len(selected_akun)}: {acc['name']}")
                asyncio.run(
                    jalankan_upgrader(
                        search_query=search_query,
                        bet_mode=bet_mode,
                        bet_amount=bet_amount,
                        headless=headless,
                        context=None,
                        akun_list=[acc],
                        stop_event=self._upg_stop_event,
                        log_func=self._upg_log_async,
                    )
                )
                if self._upg_stop_event.is_set():
                    break
                if i < len(selected_akun) and self._upg_delay_sec > 0:
                    self._upg_log_async(
                        f"⏳ Delay {self._upg_delay_sec} detik sebelum akun berikutnya…"
                    )
                    self._human_delay(self._upg_delay_sec, self._upg_stop_event)

            if self._upg_stop_event.is_set():
                self._upg_log_async("🛑 Dihentikan oleh pengguna.")
            else:
                self._upg_log_async("✅ Selesai.")
        except Exception as e:
            self._upg_log_async(f"❌ Error: {e}")
        finally:
            self._upg_running = False
            self._upg_update_buttons()

    # ===================== Cek Akun Tab =====================
    def _build_cek_akun_tab(self):
        frame = ctk.CTkFrame(self.tab_cek_akun)
        frame.pack(padx=12, pady=12, fill="both", expand=True)

        ctk.CTkLabel(frame, text=f"🔢 Jumlah akun tersedia: {len(self.akun)}").pack(pady=(0, 10))

        btn_frame = ctk.CTkFrame(frame)
        btn_frame.pack(pady=(5, 10))

        self.cek_nama_btn = ctk.CTkButton(
            btn_frame, text="📋 Cek Nama", width=120, command=self._cek_nama_akun
        )
        self.cek_nama_btn.pack(side="left", padx=6)

        self.cek_saldo_btn = ctk.CTkButton(
            btn_frame, text="💰 Cek Saldo", width=120, command=self._cek_saldo_akun
        )
        self.cek_saldo_btn.pack(side="left", padx=6)

        self.cek_wager_btn = ctk.CTkButton(
            btn_frame, text="🎲 Cek Wager", width=120, command=self._cek_wager_akun
        )
        self.cek_wager_btn.pack(side="left", padx=6)

        self.cek_level_btn = ctk.CTkButton(
            btn_frame, text="🏅 Cek Level", width=120, command=self._cek_level_akun
        )
        self.cek_level_btn.pack(side="left", padx=6)

        self.cek_token_btn = ctk.CTkButton(
            btn_frame, text="🔐 Validasi Token & Auto Update", width=240, command=self._validasi_token_start
        )
        self.cek_token_btn.pack(side="left", padx=6)

        self.cek_stop_btn = ctk.CTkButton(
            btn_frame, text="⏹️ Stop", width=100, command=self._cek_stop
        )
        self.cek_stop_btn.pack(side="left", padx=6)

        self.cek_log = ctk.CTkTextbox(frame, width=760, height=420)
        self.cek_log.pack(pady=10)
        self.cek_log.configure(state="disabled")
        self._cek_update_buttons()

    def _cek_update_buttons(self):
        state = "disabled" if self._cek_running else "normal"
        self.cek_nama_btn.configure(state=state)
        self.cek_saldo_btn.configure(state=state)
        self.cek_wager_btn.configure(state=state)
        self.cek_level_btn.configure(state=state)
        self.cek_token_btn.configure(state=state)
        self.cek_stop_btn.configure(state=("normal" if self._cek_running else "disabled"))

    def _cek_log_async(self, text: str):
        self.cek_log.after(0, self._log_textbox_append, self.cek_log, text)

    def _cek_nama_akun(self):
        if not self.akun:
            self._cek_log_async("❌ Tidak ada akun.")
            return
        self._cek_log_async("📋 Daftar Nama Akun:")
        for i, acc in enumerate(self.akun, 1):
            self._cek_log_async(f"{i}. {acc['name']}")
        self._cek_log_async(f"\n✅ Total: {len(self.akun)} akun")

    def _cek_saldo_akun(self):
        if self._cek_running:
            self._cek_log_async("⚠️ Proses masih berjalan.")
            return
        if not self.akun:
            self._cek_log_async("❌ Tidak ada akun.")
            return
        self._cek_stop_event.clear()
        self._cek_running = True
        self._cek_update_buttons()
        self._cek_log_async("💰 Memulai pengecekan saldo…")
        self._cek_thread = threading.Thread(target=self._cek_saldo_run, daemon=True)
        self._cek_thread.start()

    def _cek_saldo_run(self):
        try:
            data = []
            for i, acc in enumerate(self.akun, 1):
                if self._cek_stop_event.is_set():
                    self._cek_log_async("🛑 Pengecekan dihentikan.")
                    return
                self._cek_log_async(f"📊 Mengecek {i}/{len(self.akun)}: {acc['name']}")
                saldo = get_balance(acc["token"]) or 0.0
                data.append({"name": acc["name"], "saldo": float(saldo)})
                time.sleep(0.1)
            data.sort(key=lambda x: x["saldo"], reverse=True)
            self._cek_log_async("\n💰 Hasil Cek Saldo (Urutan Terbesar):")
            for i, item in enumerate(data, 1):
                self._cek_log_async(f"{i}. {item['name']} | Saldo: {item['saldo']:.4f}")
            self._cek_log_async(f"\n✅ Selesai mengecek {len(data)} akun")
        except Exception as e:
            self._cek_log_async(f"❌ Error: {e}")
        finally:
            self._cek_running = False
            self._cek_update_buttons()

    def _cek_wager_akun(self):
        if self._cek_running:
            self._cek_log_async("⚠️ Proses masih berjalan.")
            return
        if not self.akun:
            self._cek_log_async("❌ Tidak ada akun.")
            return
        self._cek_stop_event.clear()
        self._cek_running = True
        self._cek_update_buttons()
        self._cek_log_async("🎲 Memulai pengecekan wager…")
        self._cek_thread = threading.Thread(target=self._cek_wager_run, daemon=True)
        self._cek_thread.start()

    def _cek_wager_run(self):
        try:
            data = []
            for i, acc in enumerate(self.akun, 1):
                if self._cek_stop_event.is_set():
                    self._cek_log_async("🛑 Pengecekan dihentikan.")
                    return
                self._cek_log_async(f"📊 Mengecek {i}/{len(self.akun)}: {acc['name']}")
                profile = get_profile(acc["token"]) or {}
                wager = profile.get("wagerNeededForWithdraw", 0) or 0
                data.append({"name": acc["name"], "wager": float(wager)})
                time.sleep(0.1)
            data.sort(key=lambda x: x["wager"], reverse=True)
            self._cek_log_async("\n🎲 Hasil Cek Wager (Urutan Terbesar):")
            for i, item in enumerate(data, 1):
                self._cek_log_async(f"{i}. {item['name']} | Wager: {item['wager']:.2f}")
            self._cek_log_async(f"\n✅ Selesai mengecek {len(data)} akun")
        except Exception as e:
            self._cek_log_async(f"❌ Error: {e}")
        finally:
            self._cek_running = False
            self._cek_update_buttons()

    def _cek_level_akun(self):
        if self._cek_running:
            self._cek_log_async("⚠️ Proses masih berjalan.")
            return
        if not self.akun:
            self._cek_log_async("❌ Tidak ada akun.")
            return
        self._cek_stop_event.clear()
        self._cek_running = True
        self._cek_update_buttons()
        self._cek_log_async("🏅 Memulai pengecekan level…")
        self._cek_thread = threading.Thread(target=self._cek_level_run, daemon=True)
        self._cek_thread.start()

    def _cek_level_run(self):
        try:
            data = []
            for i, acc in enumerate(self.akun, 1):
                if self._cek_stop_event.is_set():
                    self._cek_log_async("🛑 Pengecekan dihentikan.")
                    return
                self._cek_log_async(f"📊 Mengecek {i}/{len(self.akun)}: {acc['name']}")
                profile = get_profile(acc["token"]) or {}
                vip = get_vip(acc["token"]) or {}
                level_name = (vip.get("currentLevel", {}) or {}).get("name", "-")
                exp_current = int(profile.get("wager", 0) or 0)
                exp_needed = (vip.get("nextLevel", {}) or {}).get("wagerNeeded", None)
                try:
                    exp_str = f"{exp_current:,} / {int(exp_needed):,}" if exp_needed is not None else f"{exp_current:,} / N/A"
                except Exception:
                    exp_str = f"{exp_current:,} / N/A"
                data.append({
                    "name": acc["name"],
                    "level": level_name,
                    "exp": exp_str,
                    "exp_num": exp_current,
                })
                time.sleep(0.1)
            data.sort(key=lambda x: x["exp_num"], reverse=True)
            self._cek_log_async("\n🏅 Hasil Cek Level & EXP (Urutan EXP Terbesar):")
            for i, item in enumerate(data, 1):
                self._cek_log_async(
                    f"{i}. {item['name']} | Level: {item['level']} | EXP: {item['exp']}"
                )
            self._cek_log_async(f"\n✅ Selesai mengecek {len(data)} akun")
        except Exception as e:
            self._cek_log_async(f"❌ Error: {e}")
        finally:
            self._cek_running = False
            self._cek_update_buttons()

    def _validasi_token_start(self):
        if self._cek_running:
            self._cek_log_async("⚠️ Proses masih berjalan.")
            return
        if not self.akun:
            self._cek_log_async("❌ Tidak ada akun.")
            return
        self._cek_stop_event.clear()
        self._cek_running = True
        self._cek_update_buttons()
        self._cek_log_async("🔐 Memulai validasi token & auto update…")
        self._cek_thread = threading.Thread(target=self._validasi_token_run, daemon=True)
        self._cek_thread.start()

    def _validasi_token_run(self):
        try:
            # 1) Pre-check status token
            invalid_accounts = []
            for i, acc in enumerate(self.akun, 1):
                if self._cek_stop_event.is_set():
                    self._cek_log_async("🛑 Dihentikan oleh pengguna.")
                    return
                name = acc.get("name", f"Akun-{i}")
                self._cek_log_async(f"📊 Mengecek {i}/{len(self.akun)}: {name}")
                try:
                    valid, reason = validate_token_requests_fast(acc.get("token", ""))
                except Exception as e:
                    valid, reason = False, f"Error: {e}"
                if valid:
                    self._cek_log_async("✅ Token aktif")
                else:
                    self._cek_log_async(f"⛔ Token tidak aktif ({reason})")
                    invalid_accounts.append(acc)
                time.sleep(0.05)

            if not invalid_accounts:
                self._cek_log_async("\n✅ Semua token aktif. Tidak ada yang perlu diupdate.")
                return

            # 2) Siapkan seed map (opsional untuk bantu user saat login manual)
            seed_map = load_seed_phrases() or {}
            seed_map_ci = {k.lower(): v for k, v in seed_map.items()}

            # 3) Buka browser untuk proses login manual Solflare per akun invalid, lalu ambil token dari localStorage
            async def run_browser_flow():
                async with async_playwright() as pw:
                    browser = await pw.chromium.launch(headless=False)
                    context = await browser.new_context()
                    page = await context.new_page()
                    try:
                        for idx, acc in enumerate(invalid_accounts, 1):
                            if self._cek_stop_event.is_set():
                                self._cek_log_async("🛑 Dihentikan oleh pengguna.")
                                break
                            name = acc.get("name", f"Akun-{idx}")
                            # Tampilkan seed jika ada (untuk memudahkan user login manual)
                            seed = seed_map.get(name) or seed_map_ci.get(name.lower())
                            if seed:
                                words = seed.split()
                                if len(words) > 6:
                                    masked = " ".join(words[:4]) + " … " + " ".join(words[-2:])
                                else:
                                    masked = seed
                                self._cek_log_async(f"🔑 [{name}] Seed tersedia: {masked}")
                            else:
                                self._cek_log_async(f"❓ [{name}] Seed tidak ditemukan di seed.enc")

                            # Buka Flip.gg dan pastikan token lama dibersihkan
                            try:
                                await page.goto("https://flip.gg", timeout=30000)
                                await page.wait_for_load_state("domcontentloaded", timeout=10000)
                                try:
                                    await page.evaluate("localStorage.removeItem('token')")
                                except:
                                    pass
                                await page.reload()
                                await page.wait_for_load_state("domcontentloaded", timeout=10000)
                            except Exception as e:
                                self._cek_log_async(f"❌ [{name}] Gagal membuka Flip.gg: {e}")
                                continue

                            # Instruksikan login manual Solflare oleh user
                            self._cek_log_async(f"🧭 [{name}] Silakan login Solflare dan masuk ke Flip.gg. Aplikasi akan menunggu token…")

                            # Poll token di localStorage
                            new_token = None
                            for _ in range(900):  # ~180 detik @0.2s
                                if self._cek_stop_event.is_set():
                                    break
                                try:
                                    new_token = await page.evaluate("""() => window.localStorage.getItem('token')""")
                                except Exception:
                                    new_token = None
                                if new_token and isinstance(new_token, str) and len(new_token) > 10:
                                    break
                                await asyncio.sleep(0.2)

                            if self._cek_stop_event.is_set():
                                break

                            if new_token and isinstance(new_token, str) and len(new_token) > 10:
                                acc["token"] = new_token
                                try:
                                    save_accounts(self.akun)
                                    self._cek_log_async(f"✅ [{name}] Token diperbarui & tersimpan.")
                                except Exception as e:
                                    self._cek_log_async(f"⚠️ [{name}] Gagal menyimpan akun.enc: {e}")
                            else:
                                self._cek_log_async(f"⛔ [{name}] Token tidak terdeteksi. Pastikan sudah login.")
                    finally:
                        try:
                            await browser.close()
                        except:
                            pass

            asyncio.run(run_browser_flow())
            self._cek_log_async("\n✅ Proses validasi & update token selesai.")
        except Exception as e:
            self._cek_log_async(f"❌ Error: {e}")
        finally:
            self._cek_running = False
            self._cek_update_buttons()

    def _cek_stop(self):
        if not self._cek_running:
            self._cek_log_async("ℹ️ Tidak ada proses berjalan.")
            return
        self._cek_stop_event.set()
        self._cek_log_async("⏹️ Menghentikan pengecekan…")

    # ===================== Kelola Akun Tab (HANYA AKUN) =====================
    def _build_kelola_akun_tab(self):
        frame = ctk.CTkFrame(self.tab_kelola_akun)
        frame.pack(padx=12, pady=12, fill="both", expand=True)

        # Info jumlah akun
        self.info_label = ctk.CTkLabel(frame, text=f"📊 Total akun: {len(self.akun)}")
        self.info_label.pack(pady=(0, 15))

        # Frame untuk input akun
        input_frame = ctk.CTkFrame(frame)
        input_frame.pack(pady=(0, 15), fill="x")

        ctk.CTkLabel(input_frame, text="Nama Akun:").pack(pady=(10, 5))
        self.nama_entry = ctk.CTkEntry(input_frame, placeholder_text="Masukkan nama akun")
        self.nama_entry.pack(pady=(0, 10), padx=20, fill="x")

        ctk.CTkLabel(input_frame, text="Token Akun:").pack(pady=(5, 5))
        self.token_entry = ctk.CTkEntry(input_frame, placeholder_text="Masukkan token akun")
        self.token_entry.pack(pady=(0, 15), padx=20, fill="x")

        # Tombol aksi akun
        btn_frame = ctk.CTkFrame(input_frame)
        btn_frame.pack(pady=(0, 15))

        tambah_btn = ctk.CTkButton(btn_frame, text="➕ Tambah Akun", command=self._tambah_akun)
        tambah_btn.pack(side="left", padx=5)

        clear_btn = ctk.CTkButton(btn_frame, text="🗑️ Clear Input", command=self._clear_input)
        clear_btn.pack(side="left", padx=5)

        # Daftar akun
        ctk.CTkLabel(frame, text="📋 Daftar Akun:").pack(pady=(10, 5))

        # Frame untuk scrollable list akun dengan tinggi yang lebih besar
        self.akun_frame = ctk.CTkScrollableFrame(frame, height=500)
        self.akun_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self._refresh_akun_list()

    def _tambah_akun(self):
        nama = (self.nama_entry.get() or "").strip()
        token = (self.token_entry.get() or "").strip()
        if not nama or not token:
            self._show_message("❌ Nama dan token tidak boleh kosong!")
            return
        for acc in self.akun:
            if acc["name"].lower() == nama.lower():
                self._show_message(f"❌ Akun dengan nama '{nama}' sudah ada!")
                return
        self.akun.append({"name": nama, "token": token})
        save_accounts(self.akun)
        self._show_message(f"✅ Akun '{nama}' berhasil ditambahkan!")
        self._clear_input()
        self._refresh_akun_list()
        self._update_info_label()

    def _clear_input(self):
        self.nama_entry.delete(0, tk.END)
        self.token_entry.delete(0, tk.END)

    def _hapus_akun(self, index: int):
        if 0 <= index < len(self.akun):
            nama = self.akun[index]["name"]
            del self.akun[index]
            save_accounts(self.akun)
            self._show_message(f"✅ Akun '{nama}' berhasil dihapus!")
            self._refresh_akun_list()
            self._update_info_label()

    def _refresh_akun_list(self):
        for widget in self.akun_frame.winfo_children():
            widget.destroy()
        if not self.akun:
            no_akun_label = ctk.CTkLabel(
                self.akun_frame, text="Belum ada akun. Tambahkan akun terlebih dahulu."
            )
            no_akun_label.pack(pady=20)
            return
        for i, acc in enumerate(self.akun):
            akun_item = ctk.CTkFrame(self.akun_frame)
            akun_item.pack(fill="x", pady=2, padx=5)

            info_text = f"{i+1}. {acc['name']} | Token: {acc['token'][:20]}…"
            info_label = ctk.CTkLabel(akun_item, text=info_text, anchor="w")
            info_label.pack(side="left", padx=10, pady=5, fill="x", expand=True)

            hapus_btn = ctk.CTkButton(akun_item, text="🗑️", width=40, command=lambda idx=i: self._hapus_akun(idx))
            hapus_btn.pack(side="right", padx=5, pady=5)

    # ===================== Kelola Seed Tab (TERPISAH) =====================
    def _build_kelola_seed_tab(self):
        frame = ctk.CTkFrame(self.tab_kelola_seed)
        frame.pack(padx=12, pady=12, fill="both", expand=True)

        # Info jumlah seed
        self.seed_info_label = ctk.CTkLabel(frame, text=f"🔑 Total seed phrase: {len(self.seeds)}")
        self.seed_info_label.pack(pady=(0, 15))

        # Frame untuk input seed
        seed_input_frame = ctk.CTkFrame(frame)
        seed_input_frame.pack(pady=(0, 15), fill="x")

        ctk.CTkLabel(seed_input_frame, text="Nama Akun:").pack(pady=(10, 5))
        self.seed_nama_entry = ctk.CTkEntry(
            seed_input_frame,
            placeholder_text="Masukkan nama akun (harus sama dengan nama di akun.enc)",
        )
        self.seed_nama_entry.pack(pady=(0, 10), padx=20, fill="x")

        # Tombol untuk load nama dari akun yang ada
        load_btn = ctk.CTkButton(
            seed_input_frame, 
            text="📋 Pilih dari Akun", 
            command=self._pilih_akun_untuk_seed,
            width=150
        )
        load_btn.pack(pady=(0, 10))

        ctk.CTkLabel(seed_input_frame, text="Seed Phrase:").pack(pady=(5, 5))
        self.seed_text = ctk.CTkTextbox(seed_input_frame, height=100)
        self.seed_text.pack(pady=(0, 10), padx=20, fill="x")

        # Tombol aksi seed
        seed_btn_frame = ctk.CTkFrame(seed_input_frame)
        seed_btn_frame.pack(pady=(0, 15))

        ctk.CTkButton(
            seed_btn_frame, 
            text="➕ Tambah/Update Seed", 
            command=self._tambah_seed
        ).pack(side="left", padx=5)
        
        ctk.CTkButton(
            seed_btn_frame, 
            text="🧹 Clear Input", 
            command=self._clear_seed_input
        ).pack(side="left", padx=5)

        ctk.CTkButton(
            seed_btn_frame, 
            text="📥 Load dari Akun", 
            command=self._load_seed_dari_akun
        ).pack(side="left", padx=5)

        # Daftar seed
        ctk.CTkLabel(frame, text="📋 Daftar Seed Phrase:").pack(pady=(10, 5))

        # Frame untuk scrollable list seed
        self.seed_frame = ctk.CTkScrollableFrame(frame, height=400)
        self.seed_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self._refresh_seed_list()

    def _pilih_akun_untuk_seed(self):
        """Buka dialog untuk memilih nama akun dari daftar akun yang ada"""
        if not self.akun:
            self._show_message("❌ Tidak ada akun tersedia!")
            return
        
        # Buat window popup sederhana
        popup = ctk.CTkToplevel(self)
        popup.title("Pilih Akun")
        popup.geometry("400x300")
        popup.transient(self)
        popup.grab_set()

        ctk.CTkLabel(popup, text="Pilih nama akun:").pack(pady=10)

        # Frame untuk daftar akun
        akun_list_frame = ctk.CTkScrollableFrame(popup, height=200)
        akun_list_frame.pack(fill="both", expand=True, padx=20, pady=10)

        for acc in self.akun:
            btn = ctk.CTkButton(
                akun_list_frame,
                text=acc['name'],
                command=lambda name=acc['name']: self._set_seed_nama_dan_tutup(name, popup)
            )
            btn.pack(pady=2, fill="x")

        ctk.CTkButton(popup, text="Batal", command=popup.destroy).pack(pady=10)

    def _set_seed_nama_dan_tutup(self, nama: str, popup):
        """Set nama akun ke field dan tutup popup"""
        self.seed_nama_entry.delete(0, tk.END)
        self.seed_nama_entry.insert(0, nama)
        popup.destroy()

    def _load_seed_dari_akun(self):
        """Load seed phrase dari akun yang dipilih jika sudah ada"""
        nama = (self.seed_nama_entry.get() or "").strip()
        if not nama:
            self._show_message("❌ Pilih nama akun terlebih dahulu!")
            return
        
        if nama in self.seeds:
            self.seed_text.delete("1.0", tk.END)
            self.seed_text.insert("1.0", self.seeds[nama])
            self._show_message(f"✅ Seed untuk '{nama}' berhasil dimuat!")
        else:
            self._show_message(f"❌ Seed untuk '{nama}' tidak ditemukan!")

    def _tambah_seed(self):
        """Tambah atau update seed phrase"""
        name = (self.seed_nama_entry.get() or "").strip()
        seed = (self.seed_text.get("1.0", tk.END) or "").strip()
        
        if not name or not seed:
            self._show_message("❌ Nama akun dan seed phrase tidak boleh kosong!")
            return
        
        self.seeds[name] = seed
        try:
            save_seed_phrases(self.seeds)
            self._show_message(f"✅ Seed untuk '{name}' berhasil ditambahkan/diperbarui!")
            self._clear_seed_input()
            self._refresh_seed_list()
            self._update_seed_info_label()
        except Exception as e:
            self._show_message(f"❌ Gagal menyimpan seed: {e}")

    def _clear_seed_input(self):
        """Bersihkan input seed"""
        try:
            self.seed_nama_entry.delete(0, tk.END)
        except Exception:
            pass
        try:
            self.seed_text.delete("1.0", tk.END)
        except Exception:
            pass

    def _hapus_seed(self, name: str):
        """Hapus seed phrase"""
        if name in self.seeds:
            try:
                del self.seeds[name]
                save_seed_phrases(self.seeds)
                self._show_message(f"✅ Seed untuk '{name}' berhasil dihapus!")
                self._refresh_seed_list()
                self._update_seed_info_label()
            except Exception as e:
                self._show_message(f"❌ Gagal menghapus seed: {e}")

    def _refresh_seed_list(self):
        """Refresh daftar seed phrase"""
        for widget in self.seed_frame.winfo_children():
            widget.destroy()
        
        if not self.seeds:
            no_seed_label = ctk.CTkLabel(
                self.seed_frame,
                text="Belum ada seed phrase. Tambahkan seed terlebih dahulu.",
            )
            no_seed_label.pack(pady=20)
            return

        for i, name in enumerate(sorted(self.seeds.keys()), 1):
            seed_item = ctk.CTkFrame(self.seed_frame)
            seed_item.pack(fill="x", pady=2, padx=5)

            seed_val = self.seeds.get(name, "")
            words = seed_val.split()
            if len(words) > 6:
                masked = " ".join(words[:4]) + " … " + " ".join(words[-2:])
            else:
                masked = seed_val

            info_text = f"{i}. {name} | Seed: {masked}"
            info_label = ctk.CTkLabel(seed_item, text=info_text, anchor="w")
            info_label.pack(side="left", padx=10, pady=5, fill="x", expand=True)

            # Tombol edit
            edit_btn = ctk.CTkButton(
                seed_item, 
                text="✏️", 
                width=40, 
                command=lambda nm=name: self._edit_seed(nm)
            )
            edit_btn.pack(side="right", padx=2, pady=5)

            # Tombol hapus
            hapus_btn = ctk.CTkButton(
                seed_item, 
                text="🗑️", 
                width=40, 
                command=lambda nm=name: self._hapus_seed(nm)
            )
            hapus_btn.pack(side="right", padx=2, pady=5)

    def _edit_seed(self, name: str):
        """Edit seed phrase yang dipilih"""
        if name in self.seeds:
            self.seed_nama_entry.delete(0, tk.END)
            self.seed_nama_entry.insert(0, name)
            self.seed_text.delete("1.0", tk.END)
            self.seed_text.insert("1.0", self.seeds[name])
            self._show_message(f"📝 Seed '{name}' dimuat untuk diedit")


# Fungsi untuk menjalankan GUI
def start_gui():
    """Entry point untuk menjalankan aplikasi GUI."""
    app = App()
    app.mainloop()


if __name__ == "__main__":
    start_gui()