import os
import sys
import logging

# ================== SETUP LOGGING (PALING ATAS) ==================
# Konfigurasi ini harus dijalankan sebelum import lainnya untuk memastikan
# semua logger dari pustaka pihak ketiga dapat di-override.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

# Bungkam semua logger yang "cerewet" dari pustaka telegram
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.request").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

import requests
import subprocess
import json
import psutil
import re
import time
from typing import Optional

# ================== SETUP LOGGING ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.StreamHandler() # Tampilkan log ke konsol
    ]
)

try:
    from gologin import GoLogin
except ImportError as e:
    print("[GoLogin] ERROR: Pustaka 'gologin' tidak ditemukan.")
    print("[GoLogin] Silakan install dengan menjalankan: pip install gologin requests")
    sys.exit(1)

# ================== KONFIGURASI DARI FILE ==================
# Menggunakan path dinamis agar bisa dijalankan dari mana saja
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_CONFIG_PATH = os.path.join(BASE_DIR, "bot_config.json")
FAST_RESULT_PATH = os.path.join(BASE_DIR, "fast_exec_result.json")

def load_bot_config():
    """Memuat konfigurasi dari file JSON."""
    try:
        with open(BOT_CONFIG_PATH, 'r') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"[Config] ERROR: Gagal memuat {BOT_CONFIG_PATH}: {e}")
        sys.exit(1)

config = load_bot_config()
headless_mode = bool(config.get("headless", False))

# 3. Path ke skrip bot utama Anda
BOT_SCRIPT_PATH = os.path.join(BASE_DIR, "bot_cdp.py")
# =================================================

def get_profile_id(token, profile_name):
    """Mendapatkan ID profil berdasarkan namanya."""
    logging.info(f"[GoLogin] Mencari ID untuk profil: {profile_name}")
    url = "https://api.gologin.com/browser/v2"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        profiles = data.get("profiles", []) if isinstance(data, dict) else []
        # Prioritaskan exact match, lalu contains
        for profile in profiles:
            try:
                if str(profile.get("name", "")).lower() == str(profile_name).lower():
                    profile_id = profile.get("id")
                    logging.info(f"[GoLogin] Profil ditemukan (exact). ID: {profile_id}")
                    return profile_id
            except Exception:
                continue
        for profile in profiles:
            try:
                if str(profile_name).lower() in str(profile.get("name", "")).lower():
                    profile_id = profile.get("id")
                    logging.info(f"[GoLogin] Profil ditemukan (contains). ID: {profile_id}")
                    return profile_id
            except Exception:
                continue
        logging.error(f"[GoLogin] ERROR: Profil dengan nama '{profile_name}' tidak ditemukan.")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"[GoLogin] ERROR: Gagal menghubungi API GoLogin: {e}")
        return None

def start_gologin_profile(token, profile_id):
    """Menjalankan profil GoLogin dan mendapatkan port CDP."""
    logging.info("[GoLogin] Meminta GoLogin untuk menjalankan profil...")
    try:
        # Inisialisasi GoLogin dengan token Anda
        # Siapkan opsi headless jika tersedia (bergantung dukungan SDK/versi)
        opts = {
            "token": token,
            "profile_id": profile_id,
        }
        try:
            if headless_mode:
                # Beberapa versi SDK menerima 'headless': True; jika tidak, SDK akan mengabaikan field ini
                opts["headless"] = True
        except Exception:
            pass
        gl = GoLogin(opts)

        # Pustaka akan mencari port secara otomatis
        logging.info("[GoLogin] Menjalankan profil menggunakan pustaka resmi%s..." % (" (HEADLESS)" if headless_mode else ""))
        debugger_address = gl.start()

        if not debugger_address or ":" not in debugger_address:
            logging.error(f"[GoLogin] ERROR: Debugger address tidak valid: {debugger_address}")
            return None

        # Ekstrak port dari alamat debugger
        # Contoh: 127.0.0.1:54321
        cdp_port = debugger_address.split(":")[-1]
        logging.info(f"[GoLogin] Profil berhasil dijalankan. Alamat CDP: {debugger_address}")
        logging.info(f"[GoLogin] Port CDP yang diekstrak: {cdp_port}")
        return cdp_port

    except Exception as e:
        # Otomatis tangani file lock 'Account Web Data' di Windows jika ada di pesan error
        msg = str(e)
        import re, os
        m = re.search(r"([A-Za-z]:\\\\[^\"']*Default\\\\Account Web Data)", msg)
        if m:
            base = m.group(1)
            for path in [base, base + "-journal"]:
                try:
                    if os.path.exists(path):
                        os.remove(path)
                        logging.warning(f"[GoLogin] Menghapus file lock: {path}")
                except Exception:
                    pass
            # Retry sekali
            try:
                gl = GoLogin(opts)
                debugger_address = gl.start()
                if not debugger_address or ":" not in debugger_address:
                    logging.error(f"[GoLogin] ERROR: Debugger address tidak valid (retry): {debugger_address}")
                    return None
                cdp_port = debugger_address.split(":")[-1]
                logging.info(f"[GoLogin] Profil berhasil dijalankan (retry). Alamat CDP: {debugger_address}")
                return cdp_port
            except Exception as e2:
                logging.error(f"[GoLogin] ERROR: Gagal menjalankan profil (retry): {e2}")
                return None
        logging.error(f"[GoLogin] ERROR: Gagal menjalankan profil via pustaka: {e}")
        logging.error("[GoLogin] Pastikan aplikasi GoLogin sedang berjalan di RDP Anda.")
        return None

def update_bot_config(port):
    """Update cdp_url di bot_config.json."""
    logging.info(f"[Config] Mengupdate {BOT_CONFIG_PATH} dengan port {port}...")
    with open(BOT_CONFIG_PATH, 'r+') as f:
        config_data = json.load(f)
        config_data['cdp_url'] = f"http://127.0.0.1:{port}"
        f.seek(0)
        json.dump(config_data, f, indent=2)
        f.truncate()
    logging.info("[Config] File konfigurasi berhasil diupdate.")


def find_existing_cdp_port_by_cmdline() -> Optional[int]:
    """Coba temukan port CDP yang sudah aktif dengan membaca cmdline proses (tanpa membuka/tutup GoLogin)."""
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = (proc.info.get('name') or '').lower()
                cmdline = ' '.join(proc.info.get('cmdline') or []).lower()
                if not (('gologin' in name or 'chrome' in name or 'chromium' in name) or ('gologin' in cmdline or '--remote-debugging-port' in cmdline)):
                    continue
                m = re.search(r"--remote-debugging-port=(\d+)", cmdline)
                if m:
                    port = int(m.group(1))
                    if port > 0:
                        logging.info(f"[CDP] Ditemukan port DevTools dari proses: {port}")
                        return port
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception as e:
        logging.warning(f"[CDP] Gagal mencari port CDP via cmdline: {e}")
    return None

def find_existing_cdp_port_by_net() -> Optional[int]:
    """Coba temukan port CDP dengan melihat koneksi LISTEN milik proses chrome/chromium/gologin lalu probe /json/version."""
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                name = (proc.info.get('name') or '').lower()
                cmdline = ' '.join(proc.info.get('cmdline') or []).lower()
                if not (('gologin' in name or 'chrome' in name or 'chromium' in name) or ('gologin' in cmdline or '--remote-debugging-port' in cmdline)):
                    continue
                conns = []
                try:
                    conns = proc.net_connections(kind='inet')
                except Exception:
                    conns = []
                for c in conns:
                    try:
                        if not c.laddr:
                            continue
                        ip = getattr(c.laddr, 'ip', None) or (c.laddr[0] if isinstance(c.laddr, tuple) and len(c.laddr) > 0 else None)
                        port = getattr(c.laddr, 'port', None) or (c.laddr[1] if isinstance(c.laddr, tuple) and len(c.laddr) > 1 else None)
                        status = getattr(c, 'status', '')
                        if not port:
                            continue
                        if status and str(status).upper() != 'LISTEN':
                            continue
                        if ip and ip not in ('127.0.0.1', '0.0.0.0', '::1'):
                            continue
                        # Probe cepat
                        try:
                            r = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=0.5)
                            if r.ok:
                                logging.info(f"[CDP] Port DevTools terverifikasi via net/probe: {port}")
                                return int(port)
                        except Exception:
                            pass
                    except Exception:
                        continue
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception as e:
        logging.warning(f"[CDP] Gagal mencari port CDP via net: {e}")
    return None

def _kill_processes_for_port_by_net(port: int, grace_seconds: float = 2.0) -> tuple[bool, str]:
    """Bunuh proses yang menggunakan port tertentu via net_connections sebagai fallback."""
    try:
        victims = set()
        for p in psutil.process_iter(attrs=["pid", "name"]):
            try:
                for c in p.net_connections(kind="inet"):
                    if c.laddr and c.laddr.port == port:
                        victims.add(p.pid)
                    elif c.raddr and c.raddr.port == port:
                        victims.add(p.pid)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        victims = {pid for pid in victims if pid and pid > 0}
        if not victims:
            return False, f"Tidak ada proses di port {port}"
        for pid in victims:
            try:
                psutil.Process(pid).terminate()
            except psutil.NoSuchProcess:
                pass
        t0 = time.time()
        while time.time() - t0 < grace_seconds:
            alive = [pid for pid in victims if psutil.pid_exists(pid)]
            if not alive:
                return True, f"Terminated PID(s): {', '.join(map(str, victims))}"
            time.sleep(0.2)
        for pid in list(victims):
            if psutil.pid_exists(pid):
                try:
                    psutil.Process(pid).kill()
                except psutil.NoSuchProcess:
                    pass
        return True, f"Killed PID(s): {', '.join(map(str, victims))}"
    except Exception as e:
        return False, f"Kill by net error: {e}"

def stop_gologin_profile(token: str, profile_id: str, port: str | None) -> tuple[bool, str]:
    """Hentikan profil GoLogin via SDK. Fallback: kill proses di port CDP jika perlu."""
    logging.info("[GoLogin] Mencoba menghentikan profil...")
    
    # 1) via SDK
    try:
        if profile_id:
            gl = GoLogin({"token": token, "profile_id": profile_id})
            gl.stop()
            logging.info("[GoLogin] Profil berhasil dihentikan via SDK")
            return True, "Profil dihentikan via SDK"
    except Exception as e:
        logging.warning(f"[GoLogin] SDK stop gagal: {e}")
    
    # 2) fallback via kill process di port CDP
    try:
        p = int(str(port)) if port else None
    except Exception:
        p = None
        
    if p and p > 0:
        logging.info(f"[GoLogin] Mencoba fallback kill process di port {p}...")
        ok, msg = _kill_processes_for_port_by_net(p)
        if ok:
            logging.info(f"[GoLogin] Fallback berhasil: {msg}")
            return True, f"Fallback berhasil: {msg}"
        else:
            logging.warning(f"[GoLogin] Fallback gagal: {msg}")
    
    # 3) fallback terakhir: cari dan kill semua proses GoLogin
    try:
        logging.info("[GoLogin] Mencoba fallback kill semua proses GoLogin...")
        killed_count = 0
        
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                proc_info = proc.info
                proc_name = proc_info.get('name', '').lower()
                cmdline = ' '.join(proc_info.get('cmdline', [])).lower()
                
                # Cari proses yang terkait GoLogin
                if any(keyword in proc_name for keyword in ['gologin', 'chrome', 'chromium']) or \
                   any(keyword in cmdline for keyword in ['gologin', '--remote-debugging-port']):
                    
                    # Jika ada port spesifik, pastikan proses menggunakan port tersebut
                    if p:
                        port_found = False
                        try:
                            for conn in proc.connections():
                                if conn.laddr and conn.laddr.port == p:
                                    port_found = True
                                    break
                        except (psutil.AccessDenied, psutil.NoSuchProcess):
                            # Jika tidak bisa akses connections, cek cmdline
                            if f'--remote-debugging-port={p}' in cmdline or f':{p}' in cmdline:
                                port_found = True
                        
                        if not port_found:
                            continue
                    
                    # Kill process
                    try:
                        proc.terminate()
                        killed_count += 1
                        logging.info(f"[GoLogin] Terminated process: {proc_info.get('name')} (PID: {proc.pid})")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                        
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        if killed_count > 0:
            # Tunggu sebentar untuk proses terminate
            time.sleep(2)
            
            # Force kill jika masih ada yang hidup
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    proc_info = proc.info
                    proc_name = proc_info.get('name', '').lower()
                    cmdline = ' '.join(proc_info.get('cmdline', [])).lower()
                    
                    if any(keyword in proc_name for keyword in ['gologin', 'chrome', 'chromium']) or \
                       any(keyword in cmdline for keyword in ['gologin', '--remote-debugging-port']):
                        
                        if p:
                            port_found = False
                            try:
                                for conn in proc.connections():
                                    if conn.laddr and conn.laddr.port == p:
                                        port_found = True
                                        break
                            except (psutil.AccessDenied, psutil.NoSuchProcess):
                                if f'--remote-debugging-port={p}' in cmdline or f':{p}' in cmdline:
                                    port_found = True
                            
                            if not port_found:
                                continue
                        
                        try:
                            proc.kill()
                            logging.info(f"[GoLogin] Force killed process: {proc_info.get('name')} (PID: {proc.pid})")
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                            
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            return True, f"Berhasil menghentikan {killed_count} proses GoLogin"
        else:
            return False, "Tidak ada proses GoLogin yang ditemukan untuk dihentikan"
            
    except Exception as e:
        logging.error(f"[GoLogin] Error saat fallback kill: {e}")
        return False, f"Error saat menghentikan proses: {e}"

def read_fast_result():
    """Membaca hasil eksekusi cepat dari file JSON.

    Catatan: Jangan menghapus file di sini; biarkan watcher yang membersihkan
    (agar watcher dapat mendeteksi 'success' dan melakukan cooldown 3 menit).
    """
    try:
        if os.path.exists(FAST_RESULT_PATH):
            with open(FAST_RESULT_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return str(data.get('status', '')).strip().lower() or None
    except Exception as e:
        logging.error(f"[FastResult] Error membaca hasil: {e}")
    return None

def run_bot_with_retry(max_duration_minutes=2):
    """Menjalankan bot dengan retry selama maksimal 2 menit sampai sukses."""
    start_time = time.time()
    max_duration_seconds = max_duration_minutes * 60
    attempt = 1
    
    logging.info(f"[Bot] ⏰ MEMULAI TIMER 2 MENIT - Eksekusi cepat hingga 2 menit, tanpa menutup GoLogin.")
    logging.info(f"[Bot] Memulai eksekusi dengan retry maksimal {max_duration_minutes} menit...")
    
    while True:
        current_time = time.time()
        elapsed_time = current_time - start_time
        
        # PAKSA STOP setelah 2 menit - tidak peduli status apapun
        if elapsed_time >= max_duration_seconds:
            logging.info(f"[Bot] ⏰ WAKTU 2 MENIT HABIS! Menghentikan percobaan eksekusi cepat (GoLogin tetap berjalan).")
            logging.info(f"[Bot] Total percobaan: {attempt-1}, Total waktu: {elapsed_time:.1f} detik")
            break
            
        remaining_time = max_duration_seconds - elapsed_time
        logging.info(f"[Bot] 🔄 Percobaan ke-{attempt} (⏰ sisa waktu: {remaining_time:.1f} detik)")
        
        # Jalankan bot dengan timeout yang ketat
        try:
            # Set timeout untuk subprocess agar tidak melebihi sisa waktu
            subprocess_timeout = min(remaining_time - 5, 30)  # Maksimal 30 detik per run
            if subprocess_timeout <= 0:
                logging.info("[Bot] ⏰ Sisa waktu tidak cukup untuk percobaan lagi.")
                break
                
            result = subprocess.run([sys.executable, BOT_SCRIPT_PATH], timeout=subprocess_timeout)
            rc = result.returncode or 0
            logging.info(f"[Bot] Percobaan ke-{attempt} selesai dengan kode {rc}")
        except subprocess.TimeoutExpired:
            logging.info(f"[Bot] ⏰ Percobaan ke-{attempt} timeout, lanjut ke percobaan berikutnya")
            rc = 1
        
        # Cek hasil eksekusi
        status = read_fast_result()
        if status == 'success':
            logging.info(f"[Bot] ✅ SUKSES pada percobaan ke-{attempt}! Bot berhasil join.")
            return rc, True  # Return dengan flag sukses
            
        # Cek waktu lagi sebelum delay
        current_time = time.time()
        elapsed_time = current_time - start_time
        remaining_time = max_duration_seconds - elapsed_time
        
        # Jika waktu hampir habis, langsung break
        if remaining_time <= 10:
            logging.info(f"[Bot] ⏰ Sisa waktu {remaining_time:.1f} detik, tidak cukup untuk retry lagi.")
            break
            
        # Delay singkat sebelum retry berikutnya
        delay = min(3, remaining_time - 5)  # Delay 3 detik atau sisa waktu minus 5 detik
        if delay > 0:
            logging.info(f"[Bot] Belum sukses, retry dalam {delay} detik...")
            time.sleep(delay)
        
        attempt += 1
    
    final_elapsed = time.time() - start_time
    logging.info(f"[Bot] ⏰ TIMER SELESAI - Total {attempt-1} percobaan dalam {final_elapsed:.1f} detik")
    return rc, False  # Return dengan flag tidak sukses

def main():
    """Main function"""
    gologin_api_token = config.get("gologin_api_token")
    gologin_profile_name = config.get("gologin_profile_name")
    prepare_only = ("--prepare-only" in sys.argv)

    if not gologin_api_token or not gologin_profile_name:
        logging.error("[Config] ERROR: 'gologin_api_token' atau 'gologin_profile_name' tidak ditemukan di bot_config.json")
        sys.exit(1)

    # Reuse CDP yang sudah aktif bila memungkinkan
    cdp_url = config.get('cdp_url')
    reuse_port: Optional[int] = None
    # Gunakan cdp_url yang sudah ada TANPA memulai ulang profil
    if cdp_url:
        try:
            reuse_port = int(cdp_url.rsplit(":", 1)[1])
        except Exception:
            reuse_port = None
    if reuse_port:
        logging.info(f"[CDP] Will reuse DevTools at {cdp_url} (port {reuse_port}) without restarting GoLogin.")

    # STEP 1: Tentukan port CDP TANPA restart kecuali pada prepare-only
    started_new = False
    port: Optional[str] = None
    profile_id = get_profile_id(gologin_api_token, gologin_profile_name)

    if reuse_port:
        port = str(reuse_port)
        logging.info(f"[CDP] Reuse port dari config: {port}")
    else:
        # Coba deteksi port CDP yang sudah aktif dari proses
        detected = find_existing_cdp_port_by_cmdline()
        if not detected:
            detected = find_existing_cdp_port_by_net()
        if detected:
            port = str(detected)
            update_bot_config(port)
        else:
            if prepare_only:
                # HANYA di prepare-only: boleh memulai profil jika belum ada CDP aktif
                if profile_id:
                    port = start_gologin_profile(gologin_api_token, profile_id)
                    if not port:
                        logging.error("[GoLogin] Tidak bisa memulai profil pada prepare-only. Keluar.")
                        sys.exit(2)
                    started_new = True
                    update_bot_config(port)
                else:
                    logging.error("[GoLogin] Profil tidak ditemukan pada prepare-only. Keluar.")
                    sys.exit(2)
            else:
                # Pada eksekusi (bukan prepare): JANGAN memulai profil baru.
                logging.error("[CDP] Tidak menemukan CDP aktif untuk dieksekusi dan dilarang memulai ulang pada fase eksekusi.")
                sys.exit(2)

    # STEP 2: Verifikasi CDP endpoint UP (tanpa memulai ulang profil jika gagal)
    probe = f"http://127.0.0.1:{port}/json/version"
    logging.info(f"[CDP] Memeriksa DevTools endpoint: {probe}")
    ok = False
    for attempt in range(1, 6):  # retry sampai 5x
        try:
            r = requests.get(probe, timeout=3)
            if r.ok:
                ok = True
                break
            else:
                logging.warning(f"[CDP] Endpoint belum siap (status {r.status_code}) percobaan {attempt}/5")
        except Exception as e:
            logging.warning(f"[CDP] Gagal hubungi DevTools percobaan {attempt}/5: {e}")
        time.sleep(1)
    if not ok:
        # Coba fallback deteksi port dari proses lalu re-probe sekali lagi
        fallback = find_existing_cdp_port_by_cmdline()
        if not fallback:
            fallback = find_existing_cdp_port_by_net()
        if fallback and str(fallback) != str(port):
            logging.info(f"[CDP] Fallback ke port terdeteksi: {fallback}")
            port = str(fallback)
            update_bot_config(port)
            # Re-probe
            probe = f"http://127.0.0.1:{port}/json/version"
            logging.info(f"[CDP] Re-probe DevTools endpoint: {probe}")
            try:
                r = requests.get(probe, timeout=3)
                ok = bool(r.ok)
            except Exception:
                ok = False
        if not ok and prepare_only:
            # Terakhir: pada prepare-only boleh memulai profil agar CDP aktif
            if profile_id:
                port = start_gologin_profile(gologin_api_token, profile_id)
                if not port:
                    logging.error("[CDP] Gagal memulai profil pada prepare-only setelah fallback. Keluar.")
                    sys.exit(2)
                update_bot_config(port)
                ok = True  # anggap siap; verifikasi akan dilakukan berikutnya
            else:
                logging.error("[CDP] Profil tidak ditemukan pada prepare-only setelah fallback. Keluar.")
                sys.exit(2)
        if not ok:
            logging.error("[CDP] Endpoint DevTools tidak siap. Tidak akan memulai ulang GoLogin pada fase eksekusi. Keluar.")
            sys.exit(2)

    if prepare_only:
        logging.info("[Prepare] GoLogin aktif dan CDP siap. Keluar (prepare-only).")
        sys.exit(0)

    # STEP 3: Jalankan bot utama dengan sistem retry 2 menit
    logging.info("\n[Bot] Menjalankan skrip bot utama dengan sistem retry...")
    rc, success = run_bot_with_retry(max_duration_minutes=2)
    if success:
        logging.info("[GoLogin] ✅ Bot selesai. Profil GoLogin tetap berjalan 24/7.")
    else:
        logging.info("[GoLogin] ⏰ Bot selesai/timed out. Profil GoLogin tetap berjalan 24/7.")
    sys.exit(rc)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("\n[Main] Dihentikan oleh user")
        sys.exit(0)
    except Exception as e:
        logging.error(f"[Main] Fatal error: {e}")
        sys.exit(1)