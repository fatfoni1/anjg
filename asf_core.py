import os
import requests
from cryptography.fernet import Fernet
from typing import List, Dict

# ========== KONFIGURASI (ENV override) ==========
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "6763257363")
TG_TOKEN   = os.environ.get("TG_TOKEN",   "8265838438:AAFE-9BoNnwjDO2cl08mqwEoGSSKVfphrFA")
API_REDEEM_URL = "https://api.flip.gg/api/coupon/redeem"

# Seluruh file kredensial disimpan persis di direktori modul ini (struktur sekarang)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENCRYPTED_FILE = os.path.join(BASE_DIR, "akun.enc")
KEY_FILE       = os.path.join(BASE_DIR, "kunci.key")
SEED_FILE      = os.path.join(BASE_DIR, "seed.enc")

# ========== Enkripsi & Kunci ==========
def generate_key():
    if not os.path.exists(KEY_FILE):
        key = Fernet.generate_key()
        with open(KEY_FILE, "wb") as f:
            f.write(key)

def load_key() -> bytes:
    if not os.path.exists(KEY_FILE):
        generate_key()
    with open(KEY_FILE, "rb") as f:
        return f.read()

def encrypt_data(data: str) -> bytes:
    generate_key()
    fernet = Fernet(load_key())
    return fernet.encrypt(data.encode())

def decrypt_data(encrypted: bytes) -> str:
    fernet = Fernet(load_key())
    return fernet.decrypt(encrypted).decode()

# ========== Akun Handling ==========
def load_accounts() -> List[Dict[str, str]]:
    """Membaca akun dari akun.enc terenkripsi.
    Format setelah dekripsi: satu baris per akun -> "ID|Nama=Token" atau "Nama=Token" (backward compatibility).
    """
    if not os.path.exists(ENCRYPTED_FILE):
        return []
    try:
        with open(ENCRYPTED_FILE, "rb") as f:
            decrypted = decrypt_data(f.read())
            accounts = []
            for line_num, line in enumerate(decrypted.splitlines()):
                line = line.strip()
                if not line or "=" not in line:
                    continue
                
                # Cek format baru dengan ID: "ID|Nama=Token"
                if "|" in line and "=" in line:
                    id_name, token = line.split("=", 1)
                    if "|" in id_name:
                        acc_id, name = id_name.split("|", 1)
                        accounts.append({"id": acc_id.strip(), "name": name.strip(), "token": token.strip()})
                    else:
                        # Fallback jika format tidak sesuai
                        name = id_name.strip()
                        accounts.append({"id": str(line_num + 1), "name": name, "token": token.strip()})
                else:
                    # Format lama: "Nama=Token" - assign ID berdasarkan urutan
                    name, token = line.split("=", 1)
                    accounts.append({"id": str(line_num + 1), "name": name.strip(), "token": token.strip()})
            
            # Sort berdasarkan ID untuk mempertahankan urutan konsisten
            accounts.sort(key=lambda x: int(x.get("id", "999")))
            return accounts
    except Exception as e:
        print(f"❌ Gagal mendekripsi / membaca akun.enc: {e}")
        return []

def save_accounts(accounts: List[Dict[str, str]]):
    """Menyimpan akun ke akun.enc terenkripsi dengan ID untuk urutan konsisten."""
    # Assign ID jika belum ada
    for i, acc in enumerate(accounts):
        if "id" not in acc or not acc["id"]:
            acc["id"] = str(i + 1)
    
    # Sort berdasarkan ID untuk mempertahankan urutan
    accounts.sort(key=lambda x: int(x.get("id", "999")))
    
    # Format: "ID|Nama=Token"
    plain = "\n".join(f"{acc['id']}|{acc['name']}={acc['token']}" for acc in accounts)
    encrypted = encrypt_data(plain)
    with open(ENCRYPTED_FILE, "wb") as f:
        f.write(encrypted)

# ========== Seed Phrases Handling ==========
def load_seed_phrases() -> Dict[str, str]:
    """Membaca seed phrase dari seed.enc terenkripsi.
    Mengembalikan mapping {nama: seed_phrase}.
    Format setelah dekripsi: satu baris per akun -> "Nama=seed phrase".
    """
    if not os.path.exists(SEED_FILE):
        return {}
    try:
        with open(SEED_FILE, "rb") as f:
            decrypted = decrypt_data(f.read())
            result: Dict[str, str] = {}
            for line in decrypted.splitlines():
                line = line.strip()
                if not line or "=" not in line:
                    continue
                name, seed = line.split("=", 1)
                name = name.strip()
                seed = seed.strip()
                if name and seed:
                    result[name] = seed
            return result
    except Exception:
        print("❌ Gagal mendekripsi / membaca seed.enc. Pastikan kunci.key sesuai.")
        return {}

def save_seed_phrases(seeds: Dict[str, str]):
    """Menyimpan mapping nama->seed ke seed.enc terenkripsi."""
    try:
        if not isinstance(seeds, dict):
            return
        lines = []
        for name, seed in seeds.items():
            name = (name or "").strip()
            seed = (seed or "").strip()
            if not name or not seed:
                continue
            lines.append(f"{name}={seed}")
        plain = "\n".join(lines)
        encrypted = encrypt_data(plain)
        with open(SEED_FILE, "wb") as f:
            f.write(encrypted)
    except Exception as e:
        print(f"❌ Gagal menyimpan seed.enc: {e}")

# ========== API: GET PROFILE + SALDO ==========

def get_profile(token: str) -> Dict:
    try:
        headers = {
            "x-auth-token": token,
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        }
        res = requests.get("https://api.flip.gg/api/user", headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("user", {})
    except Exception:
        pass
    return {}

def get_balance(token: str) -> float:
    try:
        profile = get_profile(token)
        return float(profile.get("wallet", 0) or 0)
    except Exception:
        return 0.0

# ========== Kirim Log Telegram ==========
async def send_telegram(msg: str, context=None, disable_notif: bool = False):
    if context:
        try:
            await context.bot.send_message(
                chat_id=getattr(context, "_chat_id", None) or TG_CHAT_ID,
                text=msg,
                disable_notification=disable_notif,
                parse_mode="HTML",
            )
            return
        except Exception as e:
            print(f"❌ Gagal kirim via bot context: {e}")
    # Fallback via HTTP
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                json={
                    "chat_id": TG_CHAT_ID,
                    "text": msg,
                    "disable_notification": disable_notif,
                    "parse_mode": "HTML",
                },
                timeout=10,
            )
    except Exception as e:
        print(f"❌ Gagal kirim via HTTP: {e}")

# ========== VIP/Level ==========

def get_vip(token: str) -> Dict:
    url = "https://api.flip.gg/api/vip"
    headers = {
        "x-auth-token": token,
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    try:
        res = requests.get(url, headers=headers, timeout=10)
        return res.json() if res.status_code == 200 else {}
    except Exception:
        return {}


def get_level_number(token: str):
    """Mendapatkan level number dari VIP data"""
    try:
        vip = get_vip(token)
        if not vip:
            return 0
        
        current_level = vip.get("currentLevel", {})
        if not current_level:
            return 0
        
        # Ambil level number dari currentLevel
        level = current_level.get("level", 0)
        
        # Pastikan level adalah integer
        if level is None:
            return 0
        
        return int(level) if level else 0
        
    except Exception as e:
        print(f"❌ Error get_level_number: {e}")
        return 0

# ========== Fungsi Enkripsi Tambahan ==========

def backup_key():
    """Backup kunci yang ada ke kunci_backup.key"""
    if os.path.exists(KEY_FILE):
        backup_file = os.path.join(BASE_DIR, "kunci_backup.key")
        with open(KEY_FILE, "rb") as src:
            with open(backup_file, "wb") as dst:
                dst.write(src.read())
        return True
    return False

def regenerate_key():
    """Generate kunci baru dan backup kunci lama"""
    # Backup kunci lama jika ada
    backup_success = backup_key()
    
    # Generate kunci baru
    key = Fernet.generate_key()
    with open(KEY_FILE, "wb") as f:
        f.write(key)
    
    return backup_success

def decode_akun_to_txt(output_file="akun_decoded.txt"):
    """Decode akun.enc menjadi file txt dengan format ID|Nama=Token"""
    if not os.path.exists(ENCRYPTED_FILE):
        return False, "File akun.enc tidak ditemukan"
    
    try:
        accounts = load_accounts()
        if not accounts:
            return False, "Tidak ada akun atau gagal decode"
        
        output_path = os.path.join(BASE_DIR, output_file)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("# Format: ID|Nama=Token\n")
            f.write("# ID menentukan urutan akun (jangan diubah untuk konsistensi)\n\n")
            for acc in accounts:
                acc_id = acc.get("id", "1")
                f.write(f"{acc_id}|{acc['name']}={acc['token']}\n")
        
        return True, f"Berhasil decode ke {output_file}"
    except Exception as e:
        return False, f"Error: {str(e)}"

def encrypt_txt_to_akun(input_file="akun_decoded.txt"):
    """Enkripsi file txt menjadi akun.enc dengan dukungan format ID|Nama=Token"""
    input_path = os.path.join(BASE_DIR, input_file)
    
    if not os.path.exists(input_path):
        return False, f"File {input_file} tidak ditemukan"
    
    try:
        accounts = []
        with open(input_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):  # Skip baris kosong dan komentar
                    continue
                
                if "=" not in line:
                    return False, f"Format salah di baris {line_num}: {line}"
                
                # Cek format baru dengan ID: "ID|Nama=Token"
                if "|" in line:
                    id_name, token = line.split("=", 1)
                    if "|" in id_name:
                        acc_id, name = id_name.split("|", 1)
                        accounts.append({"id": acc_id.strip(), "name": name.strip(), "token": token.strip()})
                    else:
                        return False, f"Format ID salah di baris {line_num}: {line}"
                else:
                    # Format lama: "Nama=Token" - assign ID otomatis
                    name, token = line.split("=", 1)
                    accounts.append({"id": str(line_num), "name": name.strip(), "token": token.strip()})
                
                if not accounts[-1]["name"] or not accounts[-1]["token"]:
                    return False, f"Nama atau token kosong di baris {line_num}"
        
        if not accounts:
            return False, "Tidak ada akun valid ditemukan"
        
        # Simpan ke akun.enc
        save_accounts(accounts)
        return True, f"Berhasil enkripsi {len(accounts)} akun ke akun.enc"
        
    except Exception as e:
        return False, f"Error: {str(e)}"

# ========== Fungsi Validasi Token Cepat ==========
async def inject_and_validate_token_fast(page, token, account_name):
    """Suntik token dan validasi dengan indikator Balance (diperbaiki untuk stabilitas)"""
    import asyncio
    
    try:
        # 1. Pre-check token dengan requests (cepat dan hemat resource)
        is_valid, reason = validate_token_requests_fast(token)
        if not is_valid:
            return False, f"Pre-check gagal: {reason}"
        
        # 2. Pastikan halaman siap dengan timeout yang lebih realistis
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await page.goto("https://flip.gg", timeout=10000)  # Diperpanjang dari 6000
                await page.wait_for_load_state("domcontentloaded", timeout=5000)  # Diperpanjang dari 3000
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    return False, f"Gagal load halaman setelah {max_retries} percobaan: {str(e)}"
                await asyncio.sleep(1)  # Tunggu sebelum retry
        
        # 3. Suntik token dengan retry mechanism yang lebih robust
        token_injected = False
        for attempt in range(3):
            try:
                # Clear existing token first
                await page.evaluate("window.localStorage.removeItem('token')")
                await asyncio.sleep(0.5)
                
                # Inject new token
                await page.evaluate(f"window.localStorage.setItem('token', '{token}')")
                
                # Verify token was set
                stored_token = await page.evaluate("window.localStorage.getItem('token')")
                if stored_token == token:
                    token_injected = True
                    break
                    
            except Exception as e:
                if "Access is denied" in str(e) or "SecurityError" in str(e):
                    # Try reload and retry
                    try:
                        await page.reload()
                        await page.wait_for_load_state("domcontentloaded", timeout=3000)
                        await asyncio.sleep(1)
                    except:
                        pass
                else:
                    if attempt == 2:  # Last attempt
                        return False, f"Error suntik token: {str(e)}"
                    await asyncio.sleep(0.5)
        
        if not token_injected:
            return False, "Gagal suntik token setelah 3 percobaan"
        
        # 4. Refresh untuk apply token dengan error handling
        try:
            await page.reload()
            await page.wait_for_load_state("domcontentloaded", timeout=5000)  # Diperpanjang
            await asyncio.sleep(1)  # Beri waktu untuk proses login
        except Exception as e:
            return False, f"Gagal refresh halaman: {str(e)}"
        
        # 5. Validasi dengan multiple indicators (lebih robust)
        validation_success = False
        
        # CEK INDIKATOR SUKSES - Balance section (PRIORITAS UTAMA)
        try:
            balance_selectors = [
                ".tss-1vqppwl-balance",
                "[class*='balance']",
                ".balance",
                "div:has-text('Balance')"
            ]
            
            for selector in balance_selectors:
                try:
                    balance_section = await page.wait_for_selector(selector, timeout=2000)
                    if balance_section:
                        validation_success = True
                        break
                except:
                    continue
                    
            if validation_success:
                return True, "Token valid - Balance section terdeteksi"
        except:
            pass
        
        # CEK INDIKATOR SUKSES - Tombol deposit/withdraw
        try:
            action_buttons = [
                "button:has-text('deposit')",
                "button:has-text('Deposit')",
                "button:has-text('withdraw')",
                "button:has-text('Withdraw')",
                "[data-testid*='deposit']",
                "[data-testid*='withdraw']"
            ]
            
            for selector in action_buttons:
                try:
                    button = await page.wait_for_selector(selector, timeout=1500)
                    if button:
                        validation_success = True
                        break
                except:
                    continue
                    
            if validation_success:
                return True, "Token valid - Action button terdeteksi"
        except:
            pass
        
        # CEK INDIKATOR SUKSES - User menu/profile
        try:
            user_selectors = [
                "[data-testid*='user']",
                "[class*='user-menu']",
                "[class*='profile']",
                "button[aria-label*='user']",
                "div:has-text('Level')"
            ]
            
            for selector in user_selectors:
                try:
                    user_element = await page.wait_for_selector(selector, timeout=1500)
                    if user_element:
                        validation_success = True
                        break
                except:
                    continue
                    
            if validation_success:
                return True, "Token valid - User element terdeteksi"
        except:
            pass
        
        # CEK INDIKATOR GAGAL - Dialog login/connect
        try:
            login_selectors = [
                ".MuiDialog-container",
                "[role='dialog']",
                "div:has-text('Connect')",
                "div:has-text('Login')",
                "button:has-text('Connect')",
                "button:has-text('Login')"
            ]
            
            for selector in login_selectors:
                try:
                    dialog = await page.wait_for_selector(selector, timeout=1000)
                    if dialog:
                        return False, "Token expired - Dialog login terdeteksi"
                except:
                    continue
        except:
            pass
        
        # Final check - jika tidak ada indikator sukses atau gagal yang jelas
        # Coba deteksi berdasarkan URL atau page title
        try:
            current_url = page.url
            page_title = await page.title()
            
            # Jika masih di halaman utama dan tidak ada redirect ke login
            if "flip.gg" in current_url and "login" not in current_url.lower():
                return True, "Token valid - Halaman utama loaded tanpa redirect"
        except:
            pass
        
        # Jika semua validasi gagal
        return False, "Token expired - Tidak ada indikator login berhasil terdeteksi"
        
    except Exception as e:
        return False, f"Validasi gagal: {str(e)}"

def validate_token_requests_fast(token):
    """Validasi token dengan requests cepat (pre-check) dengan perbaikan stabilitas"""
    # Pre-check token dasar
    try:
        if not token or not isinstance(token, str) or len(token.strip()) < 10:
            return False, "Token kosong/terlalu pendek"
        token = token.strip()
        # Timeouts dan retry ringan untuk gangguan sementara
        headers = {
            "x-auth-token": token,
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
        import time
        last_exc = None
        for attempt in range(2):  # 1x retry untuk error sementara
            try:
                response = requests.get("https://api.flip.gg/api/user", headers=headers, timeout=8)
                status = response.status_code
                if status == 200:
                    return True, "Token valid"
                if status == 401:
                    return False, "Token expired"
                if status == 403:
                    return False, "Token diblokir/akses ditolak"
                if status == 429:
                    # Rate limit - retry sekali
                    if attempt == 0:
                        time.sleep(0.8)
                        continue
                    return False, "Rate limited (429) - coba lagi"
                if 500 <= status < 600:
                    # Server error - retry sekali
                    if attempt == 0:
                        time.sleep(0.8)
                        continue
                    return False, f"Server error {status} - coba lagi"
                # Status lain dianggap error API
                return False, f"API error: Status {status}"
            except requests.exceptions.Timeout as e:
                last_exc = e
                if attempt == 0:
                    time.sleep(0.8)
                    continue
                return False, "Request timeout - coba lagi"
            except requests.exceptions.ConnectionError as e:
                last_exc = e
                if attempt == 0:
                    time.sleep(0.8)
                    continue
                return False, "Connection error - coba lagi"
            except Exception as e:
                last_exc = e
                # Tidak retry untuk error lain
                return False, f"Request error: {str(e)}"
        # Fallback jika keluar dari loop tanpa return
        if last_exc:
            return False, f"Request error: {str(last_exc)}"
        return False, "Tidak diketahui"
    except Exception as e:
        return False, f"Validator error: {str(e)}"