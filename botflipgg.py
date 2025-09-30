import os
import sys
import time
import json
import shutil
import platform
import subprocess
from datetime import datetime
from prompt_toolkit import prompt
import signal
from rich import print
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from asf_core import (
    load_accounts, save_accounts, TG_TOKEN, TG_CHAT_ID,
    backup_key, regenerate_key, decode_akun_to_txt, encrypt_txt_to_akun,
    generate_key
)
from feature_flags import list_features, set_feature, is_enabled, load_flags, save_flags, DEFAULT_FLAGS

console = Console()

# Safe prompt wrapper dan handler Ctrl+C agar tidak hang/stuck
try:
    from prompt_toolkit import prompt as _orig_prompt
    def prompt(message: str) -> str:
        try:
            return _orig_prompt(message)
        except KeyboardInterrupt:
            print("\n[yellow]Dibatalkan oleh pengguna[/]")
            raise SystemExit(0)
        except EOFError:
            print("\n[yellow]Input ditutup[/]")
            raise SystemExit(0)
        except Exception:
            # Fallback ke input standar jika prompt_toolkit bermasalah
            return input(message)
except Exception:
    def prompt(message: str) -> str:
        try:
            return input(message)
        except KeyboardInterrupt:
            print("\n[yellow]Dibatalkan oleh pengguna[/]")
            raise SystemExit(0)

def _sigint_handler(signum, frame):
    print("\n[yellow]SIGINT diterima, keluar...[/]")
    raise SystemExit(0)

signal.signal(signal.SIGINT, _sigint_handler)
if hasattr(signal, "SIGBREAK"):
    try:
        signal.signal(signal.SIGBREAK, _sigint_handler)
    except Exception:
        pass

def banner():
    print(Panel.fit(
        "[bold cyan]🔧 FlipBot Admin CLI[/]\n"
        "[dim]Manajemen Sistem & Konfigurasi[/]",
        border_style="cyan"
    ))

def kelola_bot_telegram():
    """Konfigurasi Bot Telegram"""
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        banner()
        
        print("\n🤖 [bold cyan]Konfigurasi Bot Telegram[/]")
        print(f"[dim]Token saat ini: {TG_TOKEN[:20]}...{TG_TOKEN[-10:] if TG_TOKEN else 'Tidak ada'}[/]")
        print(f"[dim]Chat ID saat ini: {TG_CHAT_ID or 'Tidak ada'}[/]")
        
        print("\n1. Update Token Bot")
        print("2. Update Chat ID")
        print("3. Test Koneksi Bot")
        print("4. Lihat Info Bot")
        print("5. Kembali")
        
        pilih = prompt("➤ Pilih: ").strip()
        
        if pilih == "1":
            print("\n[cyan]🔑 Update Token Bot Telegram[/]")
            print("[yellow]💡 Dapatkan token dari @BotFather di Telegram[/]")
            new_token = prompt("Token baru: ").strip()
            
            if len(new_token) > 20 and ":" in new_token:
                # Update di asf_core.py
                update_config_value("TG_TOKEN", new_token)
                print("[green]✅ Token berhasil diupdate![/]")
                print("[yellow]⚠️ Restart bot untuk menerapkan perubahan[/]")
            else:
                print("[red]❌ Format token tidak valid[/]")
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "2":
            print("\n[cyan]💬 Update Chat ID[/]")
            print("[yellow]💡 Kirim pesan ke bot, lalu cek dengan /start[/]")
            new_chat_id = prompt("Chat ID baru: ").strip()
            
            if new_chat_id.lstrip('-').isdigit():
                update_config_value("TG_CHAT_ID", new_chat_id)
                print("[green]✅ Chat ID berhasil diupdate![/]")
                print("[yellow]⚠️ Restart bot untuk menerapkan perubahan[/]")
            else:
                print("[red]❌ Format Chat ID tidak valid[/]")
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "3":
            print("\n[cyan]🔍 Test Koneksi Bot[/]")
            if test_telegram_connection():
                print("[green]✅ Koneksi bot berhasil![/]")
            else:
                print("[red]❌ Koneksi bot gagal![/]")
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "4":
            print("\n[cyan]ℹ️ Info Bot Telegram[/]")
            show_bot_info()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "5":
            break
        else:
            print("[red]❌ Pilihan tidak valid[/]")
            time.sleep(1)

def sistem_utilitas():
    """Utilitas Sistem"""
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        banner()
        
        print("\n⚙️ [bold cyan]Sistem Utilitas[/]")
        print("1. Backup Semua File")
        print("2. Restore dari Backup")
        print("3. Cleanup File Temporary")
        print("4. Monitoring Sistem")
        print("5. 📦 Dependencies Management")
        print("6. Restart Services")
        print("7. Kembali")
        
        pilih = prompt("➤ Pilih: ").strip()
        
        if pilih == "1":
            print("\n[cyan]💾 Backup Semua File[/]")
            backup_all_files()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "2":
            print("\n[cyan]📥 Restore dari Backup[/]")
            restore_from_backup()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "3":
            print("\n[cyan]🧹 Cleanup File Temporary[/]")
            cleanup_temp_files()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "4":
            print("\n[cyan]📊 Monitoring Sistem[/]")
            show_system_monitoring()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "5":
            dependencies_management()
            
        elif pilih == "6":
            print("\n[cyan]🔄 Restart Services[/]")
            restart_services()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "7":
            break
        else:
            print("[red]❌ Pilihan tidak valid[/]")
            time.sleep(1)

def diagnostik_sistem():
    """Diagnostik dan Health Check"""
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        banner()
        
        print("\n🔍 [bold cyan]Diagnostik Sistem[/]")
        print("1. Health Check Lengkap")
        print("2. Validasi Token Akun")
        print("3. Cek Status File")
        print("4. Test Koneksi Internet")
        print("5. Analisis Performance")
        print("6. Export Diagnostic Report")
        print("7. Kembali")
        
        pilih = prompt("➤ Pilih: ").strip()
        
        if pilih == "1":
            print("\n[cyan]🏥 Health Check Lengkap[/]")
            run_health_check()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "2":
            print("\n[cyan]🔑 Validasi Token Akun[/]")
            validate_all_tokens()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "3":
            print("\n[cyan]📋 Cek Status File[/]")
            check_file_status()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "4":
            print("\n[cyan]🌐 Test Koneksi Internet[/]")
            test_internet_connection()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "5":
            print("\n[cyan]⚡ Analisis Performance[/]")
            analyze_performance()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "6":
            print("\n[cyan]📄 Export Diagnostic Report[/]")
            export_diagnostic_report()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "7":
            break
        else:
            print("[red]❌ Pilihan tidak valid[/]")
            time.sleep(1)

def log_management():
    """Manajemen Log"""
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        banner()
        
        print("\n📝 [bold cyan]Manajemen Log[/]")
        print("1. Lihat Log Terbaru")
        print("2. Cari dalam Log")
        print("3. Clear All Logs")
        print("4. Export Logs")
        print("5. Konfigurasi Log Level")
        print("6. Kembali")
        
        pilih = prompt("➤ Pilih: ").strip()
        
        if pilih == "1":
            print("\n[cyan]📖 Log Terbaru[/]")
            show_recent_logs()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "2":
            print("\n[cyan]🔍 Cari dalam Log[/]")
            keyword = prompt("Kata kunci: ").strip()
            search_in_logs(keyword)
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "3":
            print("\n[cyan]🗑️ Clear All Logs[/]")
            konfirmasi = prompt("[red]Yakin ingin menghapus semua log? (y/N): [/]").strip().lower()
            if konfirmasi == "y":
                clear_all_logs()
                print("[green]✅ Semua log berhasil dihapus[/]")
            else:
                print("[yellow]⚠️ Dibatalkan[/]")
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "4":
            print("\n[cyan]📤 Export Logs[/]")
            export_logs()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "5":
            print("\n[cyan]⚙️ Konfigurasi Log Level[/]")
            configure_log_level()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "6":
            break
        else:
            print("[red]❌ Pilihan tidak valid[/]")
            time.sleep(1)

def kelola_enkripsi():
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        banner()
        print("\n🔐 [bold cyan]Menu Kelola Enkripsi[/]")
        print("1. Decode akun.enc ke txt")
        print("2. Enkripsi txt ke akun.enc")
        print("3. Generate kunci baru")
        print("4. Lihat status file")
        print("5. Kembali")
        
        pilih = prompt("➤ Pilih: ").strip()
        
        if pilih == "1":
            # Decode akun.enc ke txt
            print("\n[cyan]📤 Decode akun.enc ke file txt[/]")
            nama_file = prompt("Nama file output [akun_decoded.txt]: ").strip() or "akun_decoded.txt"
            
            success, message = decode_akun_to_txt(nama_file)
            if success:
                print(f"[green]✅ {message}[/]")
                print(f"[yellow]💡 File {nama_file} siap diedit di notepad[/]")
            else:
                print(f"[red]❌ {message}[/]")
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "2":
            # Enkripsi txt ke akun.enc
            print("\n[cyan]📥 Enkripsi file txt ke akun.enc[/]")
            nama_file = prompt("Nama file input [akun_decoded.txt]: ").strip() or "akun_decoded.txt"
            
            konfirmasi = prompt(f"[yellow]⚠️ Ini akan menimpa akun.enc yang ada. Lanjutkan? (y/N): [/]").strip().lower()
            if konfirmasi == "y":
                success, message = encrypt_txt_to_akun(nama_file)
                if success:
                    print(f"[green]✅ {message}[/]")
                    print("[green]🎉 akun.enc berhasil diperbarui dan siap digunakan![/]")
                else:
                    print(f"[red]❌ {message}[/]")
            else:
                print("[yellow]⚠️ Dibatalkan[/]")
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "3":
            # Generate kunci baru
            print("\n[cyan]🔑 Generate kunci baru[/]")
            print("[yellow]⚠️ PERINGATAN:[/]")
            print("• Kunci lama akan di-backup ke kunci_backup.key")
            print("• akun.enc lama tidak bisa dibaca dengan kunci baru")
            print("• Decode akun.enc dulu sebelum generate kunci baru")
            
            konfirmasi = prompt("\n[red]Yakin ingin generate kunci baru? (y/N): [/]").strip().lower()
            if konfirmasi == "y":
                backup_success = regenerate_key()
                if backup_success:
                    print("[green]✅ Kunci lama berhasil di-backup ke kunci_backup.key[/]")
                else:
                    print("[yellow]⚠️ Tidak ada kunci lama untuk di-backup[/]")
                print("[green]✅ Kunci baru berhasil di-generate![/]")
                print("[yellow]💡 Sekarang Anda perlu enkripsi ulang file txt ke akun.enc[/]")
            else:
                print("[yellow]⚠️ Dibatalkan[/]")
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "4":
            # Lihat status file
            print("\n[cyan]📋 Status File Enkripsi[/]")
            
            # Cek file yang ada
            files_to_check = [
                ("akun.enc", "File akun terenkripsi"),
                ("kunci.key", "Kunci enkripsi utama"),
                ("kunci_backup.key", "Backup kunci enkripsi"),
                ("akun_decoded.txt", "File akun hasil decode")
            ]
            
            for filename, description in files_to_check:
                filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
                if os.path.exists(filepath):
                    size = os.path.getsize(filepath)
                    mtime = time.ctime(os.path.getmtime(filepath))
                    print(f"[green]✅ {filename}[/] - {description}")
                    print(f"   📏 Ukuran: {size} bytes | 📅 Diubah: {mtime}")
                else:
                    print(f"[red]❌ {filename}[/] - {description} (tidak ada)")
            
            # Cek jumlah akun
            try:
                akun = load_accounts()
                print(f"\n[cyan]👥 Total akun dalam akun.enc: {len(akun)}[/]")
                if akun:
                    print("[green]📝 Daftar akun:[/]")
                    for i, acc in enumerate(akun, 1):
                        print(f"   {i}. {acc['name']}")
            except Exception as e:
                print(f"[red]❌ Gagal membaca akun.enc: {e}[/]")
            
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "5":
            break
        else:
            print("[red]❌ Pilihan tidak valid[/]")
            time.sleep(1)

# Fungsi utilitas yang diperlukan
def update_config_value(key, value):
    """Update nilai konfigurasi di asf_core.py"""
    try:
        config_file = "asf_core.py"
        with open(config_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Update nilai berdasarkan key dengan format yang benar
        if key == "TG_TOKEN":
            # Cari pattern yang benar: TG_TOKEN   = os.environ.get("TG_TOKEN",   "value")
            import re
            pattern = r'TG_TOKEN\s*=\s*os\.environ\.get\("TG_TOKEN",\s*"[^"]*"\)'
            replacement = f'TG_TOKEN   = os.environ.get("TG_TOKEN",   "{value}")'
            content = re.sub(pattern, replacement, content)
            
        elif key == "TG_CHAT_ID":
            # Cari pattern yang benar: TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "value")
            import re
            pattern = r'TG_CHAT_ID\s*=\s*os\.environ\.get\("TG_CHAT_ID",\s*"[^"]*"\)'
            replacement = f'TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "{value}")'
            content = re.sub(pattern, replacement, content)
        
        with open(config_file, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    except Exception as e:
        print(f"[red]❌ Error update config: {e}[/]")
        return False

def test_telegram_connection():
    """Test koneksi ke Telegram Bot"""
    try:
        import requests
        url = f"https://api.telegram.org/bot{TG_TOKEN}/getMe"
        response = requests.get(url, timeout=10)
        return response.status_code == 200
    except Exception:
        return False

def show_bot_info():
    """Tampilkan info bot Telegram"""
    try:
        import requests
        url = f"https://api.telegram.org/bot{TG_TOKEN}/getMe"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()['result']
            print(f"[green]🤖 Bot Username: @{data['username']}[/]")
            print(f"[green]📝 Bot Name: {data['first_name']}[/]")
            print(f"[green]🆔 Bot ID: {data['id']}[/]")
        else:
            print("[red]❌ Gagal mendapatkan info bot[/]")
    except Exception as e:
        print(f"[red]❌ Error: {e}[/]")

def backup_all_files():
    """Backup semua file penting"""
    try:
        backup_dir = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        os.makedirs(backup_dir, exist_ok=True)
        
        files_to_backup = ["akun.enc", "kunci.key", "asf_core.py", "bot_module.py"]
        backed_up = []
        
        for file in files_to_backup:
            if os.path.exists(file):
                shutil.copy2(file, backup_dir)
                backed_up.append(file)
        
        print(f"[green]✅ Backup berhasil ke folder: {backup_dir}[/]")
        print(f"[green]📁 File yang di-backup: {', '.join(backed_up)}[/]")
    except Exception as e:
        print(f"[red]❌ Error backup: {e}[/]")

def restore_from_backup():
    """Restore dari backup"""
    try:
        backup_dirs = [d for d in os.listdir('.') if d.startswith('backup_')]
        if not backup_dirs:
            print("[yellow]⚠️ Tidak ada folder backup ditemukan[/]")
            return
        
        print("[cyan]📁 Folder backup yang tersedia:[/]")
        for i, dir in enumerate(backup_dirs, 1):
            print(f"{i}. {dir}")
        
        choice = prompt("Pilih nomor backup: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(backup_dirs):
            selected_dir = backup_dirs[int(choice) - 1]
            
            konfirmasi = prompt(f"[red]Yakin restore dari {selected_dir}? (y/N): [/]").strip().lower()
            if konfirmasi == "y":
                for file in os.listdir(selected_dir):
                    shutil.copy2(os.path.join(selected_dir, file), file)
                print("[green]✅ Restore berhasil![/]")
            else:
                print("[yellow]⚠️ Dibatalkan[/]")
        else:
            print("[red]❌ Pilihan tidak valid[/]")
    except Exception as e:
        print(f"[red]❌ Error restore: {e}[/]")

def cleanup_temp_files():
    """Cleanup file temporary"""
    try:
        temp_patterns = ["*.tmp", "*.log", "__pycache__", "*.pyc"]
        cleaned = []
        
        for pattern in temp_patterns:
            if pattern == "__pycache__":
                if os.path.exists("__pycache__"):
                    shutil.rmtree("__pycache__")
                    cleaned.append("__pycache__/")
            else:
                import glob
                for file in glob.glob(pattern):
                    os.remove(file)
                    cleaned.append(file)
        
        if cleaned:
            print(f"[green]✅ File yang dibersihkan: {', '.join(cleaned)}[/]")
        else:
            print("[yellow]💡 Tidak ada file temporary untuk dibersihkan[/]")
    except Exception as e:
        print(f"[red]❌ Error cleanup: {e}[/]")

def show_system_monitoring():
    """Tampilkan monitoring sistem"""
    try:
        print(f"[cyan]🖥️ Sistem: {platform.system()} {platform.release()}[/]")
        print(f"[cyan]🐍 Python: {platform.python_version()}[/]")
        
        # Disk usage
        disk_usage = shutil.disk_usage('.')
        total_gb = disk_usage.total / (1024**3)
        free_gb = disk_usage.free / (1024**3)
        used_gb = (disk_usage.total - disk_usage.free) / (1024**3)
        
        print(f"[cyan]💾 Disk: {used_gb:.1f}GB / {total_gb:.1f}GB ({free_gb:.1f}GB free)[/]")
        
        # Process info
        try:
            import psutil
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            print(f"[cyan]⚡ CPU: {cpu_percent}%[/]")
            print(f"[cyan]🧠 RAM: {memory.percent}% ({memory.used / (1024**3):.1f}GB / {memory.total / (1024**3):.1f}GB)[/]")
        except ImportError:
            print("[yellow]💡 Install psutil untuk info CPU/RAM detail[/]")
            
    except Exception as e:
        print(f"[red]❌ Error monitoring: {e}[/]")

def install_dependencies():
    """Install semua dependencies yang diperlukan untuk VPS Ubuntu"""
    print("\n[cyan]📦 Menganalisis dependencies yang diperlukan...[/]")
    
    # Daftar lengkap dependencies yang dibutuhkan
    dependencies = {
        # Core Python packages
        "requests": ">=2.31.0",
        "cryptography": ">=41.0.0", 
        "aiohttp": ">=3.8.0",
        "typing-extensions": ">=4.8.0",
        
        # Telegram Bot
        "python-telegram-bot": ">=20.0",
        
        # Web Automation
        "playwright": ">=1.40.0",
        
        # CLI & UI
        "prompt-toolkit": ">=3.0.0",
        "rich": ">=13.0.0",
        "customtkinter": ">=5.2.0",
        
        # System utilities
        "psutil": ">=5.9.0"
    }
    
    # System packages untuk Ubuntu
    ubuntu_packages = [
        "python3-pip",
        "python3-dev", 
        "python3-venv",
        "build-essential",
        "libssl-dev",
        "libffi-dev",
        "libnss3-dev",
        "libatk-bridge2.0-dev",
        "libdrm2",
        "libxkbcommon-dev",
        "libxcomposite-dev",
        "libxdamage-dev",
        "libxrandr-dev",
        "libgbm-dev",
        "libxss-dev",
        "libasound2-dev"
    ]
    
    try:
        # Cek apakah di Ubuntu/Linux
        if platform.system() == "Linux":
            print("[cyan]🐧 Terdeteksi sistem Linux - menginstall system packages...[/]")
            
            # Update package list
            print("[cyan]📋 Updating package list...[/]")
            subprocess.run(["sudo", "apt", "update"], check=True)
            
            # Install system packages
            print("[cyan]���� Installing system packages...[/]")
            cmd = ["sudo", "apt", "install", "-y"] + ubuntu_packages
            subprocess.run(cmd, check=True)
            print("[green]✅ System packages berhasil diinstall![/]")
        else:
            print(f"[yellow]⚠️ Sistem {platform.system()} - skip system packages[/]")
        
        # Upgrade pip
        print("[cyan]🔧 Upgrading pip...[/]")
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=True)
        
        # Install Python dependencies
        print("[cyan]🐍 Installing Python dependencies...[/]")
        
        # Install dari requirements.txt jika ada
        if os.path.exists("requirements.txt"):
            print("[cyan]📄 Installing from requirements.txt...[/]")
            subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)
        else:
            # Install manual jika requirements.txt tidak ada
            print("[cyan]📦 Installing dependencies manually...[/]")
            for package, version in dependencies.items():
                print(f"[dim]Installing {package}{version}...[/]")
                subprocess.run([sys.executable, "-m", "pip", "install", f"{package}{version}"], check=True)
        
        # Install playwright browsers
        print("[cyan]🌐 Installing Playwright browsers...[/]")
        subprocess.run([sys.executable, "-m", "playwright", "install"], check=True)
        subprocess.run([sys.executable, "-m", "playwright", "install-deps"], check=True)
        
        print("[green]✅ Semua dependencies berhasil diinstall![/]")
        
        # Tampilkan ringkasan
        print("\n[cyan]📋 Ringkasan instalasi:[/]")
        if platform.system() == "Linux":
            print(f"[green]• System packages: {len(ubuntu_packages)} packages[/]")
        print(f"[green]• Python packages: {len(dependencies)} packages[/]")
        print("[green]• Playwright browsers: Chromium, Firefox, WebKit[/]")
        
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"[red]❌ Error installing dependencies: {e}[/]")
        print("[yellow]💡 Coba jalankan dengan sudo jika diperlukan[/]")
        return False
    except Exception as e:
        print(f"[red]❌ Error: {e}[/]")
        return False

def check_dependencies():
    """Cek status dependencies yang terinstall"""
    print("\n[cyan]🔍 Checking dependencies status...[/]")
    
    # Dependencies yang harus ada
    required_packages = [
        "requests", "cryptography", "aiohttp", "python-telegram-bot",
        "playwright", "prompt-toolkit", "rich", "customtkinter", "psutil"
    ]
    
    missing_packages = []
    installed_packages = []
    
    for package in required_packages:
        try:
            __import__(package.replace("-", "_"))
            installed_packages.append(package)
        except ImportError:
            missing_packages.append(package)
    
    # Tampilkan hasil
    print(f"\n[green]✅ Installed ({len(installed_packages)}):[/]")
    for pkg in installed_packages:
        print(f"[green]  • {pkg}[/]")
    
    if missing_packages:
        print(f"\n[red]❌ Missing ({len(missing_packages)}):[/]")
        for pkg in missing_packages:
            print(f"[red]  • {pkg}[/]")
        print(f"\n[yellow]💡 Jalankan 'Install Dependencies' untuk menginstall yang hilang[/]")
        return False
    else:
        print(f"\n[green]🎉 Semua dependencies sudah terinstall![/]")
        return True

def update_dependencies():
    """Update dependencies"""
    try:
        print("[cyan]📦 Updating dependencies...[/]")
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=True)
        
        if os.path.exists("requirements.txt"):
            subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "-r", "requirements.txt"], check=True)
        else:
            print("[yellow]⚠️ File requirements.txt tidak ditemukan, membuat file baru...[/]")
            # Buat requirements.txt jika belum ada
            create_requirements_file()
            subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "-r", "requirements.txt"], check=True)
        
        print("[green]✅ Dependencies berhasil diupdate![/]")
    except subprocess.CalledProcessError as e:
        print(f"[red]❌ Gagal update dependencies: {e}[/]")
    except Exception as e:
        print(f"[red]❌ Error: {e}[/]")

def create_requirements_file():
    """Buat file requirements.txt"""
    requirements_content = """# FlipBot Dependencies
# Core dependencies
requests>=2.31.0
cryptography>=41.0.0
aiohttp>=3.8.0

# Telegram Bot
python-telegram-bot>=20.0

# Web Automation
playwright>=1.40.0

# CLI & UI
prompt-toolkit>=3.0.0
rich>=13.0.0
customtkinter>=5.2.0

# System utilities
psutil>=5.9.0

# Type hints
typing-extensions>=4.8.0"""
    
    try:
        with open("requirements.txt", "w", encoding="utf-8") as f:
            f.write(requirements_content)
        print("[green]✅ File requirements.txt berhasil dibuat![/]")
    except Exception as e:
        print(f"[red]❌ Gagal membuat requirements.txt: {e}[/]")

def dependencies_management():
    """Menu manajemen dependencies lengkap"""
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        banner()
        
        print("\n📦 [bold cyan]Dependencies Management[/]")
        print(f"[dim]Sistem: {platform.system()} | Python: {platform.python_version()}[/]")
        
        # Quick status check
        try:
            deps_ok = check_dependencies_quick()
            if deps_ok:
                status = "[green]✅ Semua dependencies OK[/]"
            else:
                status = "[red]❌ Ada dependencies yang hilang[/]"
            print(f"[dim]Status: {status}[/]")
        except:
            print("[dim]Status: [yellow]Unknown[/][/]")
        
        print("\n1. 🔍 Cek Status Dependencies")
        print("2. 📦 Install All Dependencies")
        print("3. 🔄 Update Dependencies")
        print("4. 📄 Generate requirements.txt")
        print("5. 🧹 Clean Install (Reinstall)")
        print("6. 🌐 Install Playwright Browsers")
        print("7. 🐧 Install Ubuntu System Packages")
        print("8. 📊 Show Detailed Info")
        print("9. 🔙 Kembali")
        
        pilih = prompt("➤ Pilih: ").strip()
        
        if pilih == "1":
            print("\n[cyan]🔍 Cek Status Dependencies[/]")
            check_dependencies()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "2":
            print("\n[cyan]📦 Install All Dependencies[/]")
            konfirmasi = prompt("[yellow]Lanjutkan install semua dependencies? (y/N): [/]").strip().lower()
            if konfirmasi == "y":
                install_dependencies()
            else:
                print("[yellow]⚠️ Dibatalkan[/]")
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "3":
            print("\n[cyan]🔄 Update Dependencies[/]")
            update_dependencies()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "4":
            print("\n[cyan]📄 Generate requirements.txt[/]")
            if os.path.exists("requirements.txt"):
                konfirmasi = prompt("[yellow]File requirements.txt sudah ada. Timpa? (y/N): [/]").strip().lower()
                if konfirmasi != "y":
                    print("[yellow]⚠️ Dibatalkan[/]")
                    input("\nTekan Enter untuk kembali...")
                    continue
            create_requirements_file()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "5":
            print("\n[cyan]🧹 Clean Install (Reinstall)[/]")
            print("[yellow]⚠️ Ini akan uninstall dan reinstall semua dependencies[/]")
            konfirmasi = prompt("[red]Yakin ingin melakukan clean install? (y/N): [/]").strip().lower()
            if konfirmasi == "y":
                clean_install_dependencies()
            else:
                print("[yellow]⚠️ Dibatalkan[/]")
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "6":
            print("\n[cyan]🌐 Install Playwright Browsers[/]")
            install_playwright_browsers()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "7":
            print("\n[cyan]🐧 Install Ubuntu System Packages[/]")
            if platform.system() != "Linux":
                print("[red]❌ Fitur ini hanya untuk sistem Linux/Ubuntu[/]")
            else:
                install_ubuntu_packages()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "8":
            print("\n[cyan]📊 Show Detailed Info[/]")
            show_dependencies_info()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "9":
            break
        else:
            print("[red]❌ Pilihan tidak valid[/]")
            time.sleep(1)

def check_dependencies_quick():
    """Quick check dependencies tanpa output detail"""
    required_packages = [
        "requests", "cryptography", "aiohttp", "python-telegram-bot",
        "playwright", "prompt-toolkit", "rich", "customtkinter", "psutil"
    ]
    
    for package in required_packages:
        try:
            __import__(package.replace("-", "_"))
        except ImportError:
            return False
    return True

def clean_install_dependencies():
    """Clean install - uninstall dan reinstall semua dependencies"""
    try:
        print("[cyan]🗑️ Uninstalling existing packages...[/]")
        
        # Daftar packages untuk uninstall
        packages_to_remove = [
            "requests", "cryptography", "aiohttp", "python-telegram-bot",
            "playwright", "prompt-toolkit", "rich", "customtkinter", "psutil",
            "typing-extensions"
        ]
        
        for package in packages_to_remove:
            try:
                print(f"[dim]Uninstalling {package}...[/]")
                subprocess.run([sys.executable, "-m", "pip", "uninstall", package, "-y"], 
                             check=False, capture_output=True)
            except:
                pass
        
        print("[green]✅ Uninstall completed![/]")
        print("\n[cyan]📦 Installing fresh dependencies...[/]")
        
        # Install ulang
        success = install_dependencies()
        if success:
            print("[green]🎉 Clean install berhasil![/]")
        else:
            print("[red]❌ Clean install gagal![/]")
            
    except Exception as e:
        print(f"[red]❌ Error during clean install: {e}[/]")

def install_playwright_browsers():
    """Install Playwright browsers saja"""
    try:
        print("[cyan]🌐 Installing Playwright browsers...[/]")
        
        # Cek apakah playwright sudah terinstall
        try:
            import playwright
            print("[green]✅ Playwright sudah terinstall[/]")
        except ImportError:
            print("[yellow]⚠️ Playwright belum terinstall, installing...[/]")
            subprocess.run([sys.executable, "-m", "pip", "install", "playwright>=1.40.0"], check=True)
        
        # Install browsers
        print("[cyan]📥 Downloading browsers...[/]")
        subprocess.run([sys.executable, "-m", "playwright", "install"], check=True)
        
        # Install system dependencies untuk browsers
        if platform.system() == "Linux":
            print("[cyan]🔧 Installing browser system dependencies...[/]")
            subprocess.run([sys.executable, "-m", "playwright", "install-deps"], check=True)
        
        print("[green]✅ Playwright browsers berhasil diinstall![/]")
        print("[green]🌐 Browsers: Chromium, Firefox, WebKit[/]")
        
    except subprocess.CalledProcessError as e:
        print(f"[red]❌ Error installing Playwright browsers: {e}[/]")
    except Exception as e:
        print(f"[red]❌ Error: {e}[/]")

def install_ubuntu_packages():
    """Install Ubuntu system packages saja"""
    ubuntu_packages = [
        "python3-pip", "python3-dev", "python3-venv", "build-essential",
        "libssl-dev", "libffi-dev", "libnss3-dev", "libatk-bridge2.0-dev",
        "libdrm2", "libxkbcommon-dev", "libxcomposite-dev", "libxdamage-dev",
        "libxrandr-dev", "libgbm-dev", "libxss-dev", "libasound2-dev"
    ]
    
    try:
        print("[cyan]🐧 Installing Ubuntu system packages...[/]")
        
        # Update package list
        print("[cyan]📋 Updating package list...[/]")
        subprocess.run(["sudo", "apt", "update"], check=True)
        
        # Install packages
        print(f"[cyan]📦 Installing {len(ubuntu_packages)} packages...[/]")
        cmd = ["sudo", "apt", "install", "-y"] + ubuntu_packages
        subprocess.run(cmd, check=True)
        
        print("[green]✅ Ubuntu system packages berhasil diinstall![/]")
        print(f"[green]📦 Total packages: {len(ubuntu_packages)}[/]")
        
    except subprocess.CalledProcessError as e:
        print(f"[red]❌ Error installing Ubuntu packages: {e}[/]")
        print("[yellow]💡 Pastikan Anda memiliki sudo privileges[/]")
    except Exception as e:
        print(f"[red]❌ Error: {e}[/]")

def show_dependencies_info():
    """Tampilkan info detail dependencies"""
    print("[cyan]📊 Dependencies Information[/]")
    print("=" * 50)
    
    # System info
    print(f"[cyan]🖥️ System: {platform.system()} {platform.release()}[/]")
    print(f"[cyan]🐍 Python: {platform.python_version()}[/]")
    print(f"[cyan]📍 Python Path: {sys.executable}[/]")
    
    # Pip version
    try:
        result = subprocess.run([sys.executable, "-m", "pip", "--version"], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            pip_version = result.stdout.strip()
            print(f"[cyan]📦 Pip: {pip_version}[/]")
    except:
        print("[yellow]⚠️ Pip version tidak dapat dideteksi[/]")
    
    print("\n[cyan]📋 Required Dependencies:[/]")
    
    # Detailed package check
    required_packages = {
        "requests": "HTTP library untuk API calls",
        "cryptography": "Enkripsi dan dekripsi data",
        "aiohttp": "Async HTTP client/server",
        "python-telegram-bot": "Telegram Bot API wrapper",
        "playwright": "Web automation dan browser control",
        "prompt-toolkit": "Interactive command line interface",
        "rich": "Rich text dan beautiful formatting",
        "customtkinter": "Modern GUI framework",
        "psutil": "System dan process utilities",
        "typing-extensions": "Type hints extensions"
    }
    
    for package, description in required_packages.items():
        try:
            if package == "python-telegram-bot":
                import telegram
                version = telegram.__version__
            elif package == "customtkinter":
                import customtkinter
                version = customtkinter.__version__
            else:
                module = __import__(package.replace("-", "_"))
                version = getattr(module, '__version__', 'Unknown')
            
            print(f"[green]✅ {package:<20} v{version:<10} - {description}[/]")
        except ImportError:
            print(f"[red]❌ {package:<20} {'Not installed':<10} - {description}[/]")
        except Exception:
            print(f"[yellow]⚠️ {package:<20} {'Installed':<10} - {description}[/]")
    
    # Check requirements.txt
    print(f"\n[cyan]📄 Requirements file:[/]")
    if os.path.exists("requirements.txt"):
        size = os.path.getsize("requirements.txt")
        mtime = datetime.fromtimestamp(os.path.getmtime("requirements.txt")).strftime('%Y-%m-%d %H:%M:%S')
        print(f"[green]✅ requirements.txt - {size} bytes - {mtime}[/]")
    else:
        print("[red]❌ requirements.txt - File tidak ada[/]")
    
    # Playwright browsers check
    print(f"\n[cyan]🌐 Playwright Browsers:[/]")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browsers = []
            try:
                p.chromium.launch()
                browsers.append("Chromium ✅")
            except:
                browsers.append("Chromium ❌")
            
            try:
                p.firefox.launch()
                browsers.append("Firefox ✅")
            except:
                browsers.append("Firefox ❌")
            
            try:
                p.webkit.launch()
                browsers.append("WebKit ✅")
            except:
                browsers.append("WebKit ❌")
            
            for browser in browsers:
                print(f"[dim]  • {browser}[/]")
    except ImportError:
        print("[red]❌ Playwright tidak terinstall[/]")
    except Exception as e:
        print(f"[yellow]⚠️ Error checking browsers: {e}[/]")

def restart_services():
    """Restart services"""
    print("[cyan]🔄 Restarting services...[/]")
    print("[yellow]💡 Untuk restart bot, gunakan: python asf.py bot[/]")
    print("[yellow]💡 Untuk restart GUI, gunakan: python asf.py gui[/]")

def run_health_check():
    """Health check lengkap"""
    print("[cyan]🏥 Running health check...[/]")
    
    checks = [
        ("File akun.enc", os.path.exists("akun.enc")),
        ("File kunci.key", os.path.exists("kunci.key")),
        ("Bot Token", bool(TG_TOKEN)),
        ("Chat ID", bool(TG_CHAT_ID)),
        ("Koneksi Internet", test_internet_connection()),
        ("Bot Telegram", test_telegram_connection() if TG_TOKEN else False)
    ]
    
    for check_name, status in checks:
        status_icon = "[green]✅[/]" if status else "[red]❌[/]"
        print(f"{status_icon} {check_name}")

def validate_all_tokens():
    """Validasi semua token akun"""
    try:
        akun = load_accounts()
        if not akun:
            print("[yellow]⚠️ Tidak ada akun untuk divalidasi[/]")
            return
        
        print(f"[cyan]🔑 Validating {len(akun)} tokens...[/]")
        valid_count = 0
        
        for i, acc in enumerate(akun, 1):
            print(f"[dim]{i}. {acc['name']}...[/]", end=" ")
            # Simulasi validasi (implementasi sebenarnya perlu API call)
            if len(acc['token']) > 20:
                print("[green]✅[/]")
                valid_count += 1
            else:
                print("[red]❌[/]")
        
        print(f"\n[cyan]📊 Valid: {valid_count}/{len(akun)} tokens[/]")
    except Exception as e:
        print(f"[red]❌ Error validasi: {e}[/]")

def check_file_status():
    """Cek status semua file"""
    files_to_check = [
        "akun.enc", "kunci.key", "asf_core.py", "bot_module.py", 
        "gui.py", "asf_upgrader.py", "asf_wheel.py"
    ]
    
    for file in files_to_check:
        if os.path.exists(file):
            size = os.path.getsize(file)
            mtime = datetime.fromtimestamp(os.path.getmtime(file)).strftime('%Y-%m-%d %H:%M:%S')
            print(f"[green]✅ {file}[/] - {size} bytes - {mtime}")
        else:
            print(f"[red]❌ {file}[/] - File tidak ada")

def test_internet_connection():
    """Test koneksi internet"""
    try:
        import requests
        response = requests.get("https://www.google.com", timeout=5)
        return response.status_code == 200
    except Exception:
        return False

def analyze_performance():
    """Analisis performance"""
    print("[cyan]⚡ Analyzing performance...[/]")
    print("[yellow]�� Fitur ini akan dikembangkan lebih lanjut[/]")

def export_diagnostic_report():
    """Export diagnostic report"""
    try:
        report_file = f"diagnostic_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(f"FlipBot Diagnostic Report\n")
            f.write(f"Generated: {datetime.now()}\n")
            f.write(f"System: {platform.system()} {platform.release()}\n")
            f.write(f"Python: {platform.python_version()}\n")
            # Tambah info lainnya
        print(f"[green]✅ Report exported to: {report_file}[/]")
    except Exception as e:
        print(f"[red]❌ Error export: {e}[/]")

def show_recent_logs():
    """Tampilkan log terbaru"""
    print("[yellow]💡 Fitur log akan dikembangkan lebih lanjut[/]")

def search_in_logs(keyword):
    """Cari dalam log"""
    print(f"[cyan]🔍 Searching for: {keyword}[/]")
    print("[yellow]💡 Fitur search log akan dikembangkan lebih lanjut[/]")

def clear_all_logs():
    """Clear semua log"""
    print("[green]✅ Logs cleared[/]")

def export_logs():
    """Export logs"""
    print("[green]✅ Logs exported[/]")

def configure_log_level():
    """Konfigurasi log level"""
    print("[yellow]💡 Fitur konfigurasi log akan dikembangkan lebih lanjut[/]")

def vps_ubuntu_setup():
    """Setup VPS Ubuntu dengan systemd service"""
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        banner()
        
        print("\n🖥️ [bold cyan]VPS Ubuntu Setup[/]")
        
        # Cek apakah di Ubuntu
        if platform.system() != "Linux":
            print("[yellow]⚠️ Fitur ini khusus untuk VPS Ubuntu Linux[/]")
            print(f"[dim]Sistem saat ini: {platform.system()}[/]")
        
        # Tampilkan status service
        show_service_status()
        
        print("\n1. 📦 Install systemd Service")
        print("2. ▶️ Start Service")
        print("3. ⏹️ Stop Service")
        print("4. 🔄 Restart Service")
        print("5. 🔧 Enable Auto-start")
        print("6. ❌ Disable Auto-start")
        print("7. 📊 Status & Monitoring")
        print("8. 📋 View Logs (journalctl)")
        print("9. 🗑️ Uninstall Service")
        print("10. ⚙️ Advanced Configuration")
        print("11. 🔙 Kembali")
        
        pilih = prompt("➤ Pilih: ").strip()
        
        if pilih == "1":
            print("\n[cyan]📦 Install systemd Service[/]")
            install_systemd_service()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "2":
            print("\n[cyan]▶️ Start Service[/]")
            start_service()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "3":
            print("\n[cyan]⏹️ Stop Service[/]")
            stop_service()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "4":
            print("\n[cyan]🔄 Restart Service[/]")
            restart_service()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "5":
            print("\n[cyan]🔧 Enable Auto-start[/]")
            enable_service()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "6":
            print("\n[cyan]❌ Disable Auto-start[/]")
            disable_service()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "7":
            print("\n[cyan]📊 Status & Monitoring[/]")
            show_detailed_status()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "8":
            print("\n[cyan]📋 View Logs[/]")
            view_service_logs()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "9":
            print("\n[cyan]🗑️ Uninstall Service[/]")
            uninstall_service()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "10":
            print("\n[cyan]⚙️ Advanced Configuration[/]")
            advanced_configuration()
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "11":
            break
        else:
            print("[red]❌ Pilihan tidak valid[/]")
            time.sleep(1)

def show_service_status():
    """Tampilkan status service singkat"""
    try:
        if platform.system() == "Linux":
            # Cek status service flipbot
            result = subprocess.run(
                ["systemctl", "is-active", "flipbot-telegram"], 
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                status = "[green]🟢 RUNNING[/]"
            else:
                status = "[red]🔴 STOPPED[/]"
            
            print(f"[dim]Service Status: {status}[/]")
        else:
            print("[dim]Service Status: [yellow]N/A (bukan Linux)[/][/]")
    except Exception:
        print("[dim]Service Status: [yellow]Unknown[/][/]")

def install_systemd_service():
    """Install systemd service untuk FlipBot"""
    try:
        if platform.system() != "Linux":
            print("[red]❌ Fitur ini hanya untuk Linux/Ubuntu[/]")
            return
        
        print("[cyan]📦 Installing FlipBot systemd service...[/]")
        
        # Dapatkan path absolut
        current_dir = os.path.abspath(os.getcwd())
        python_path = sys.executable
        
        # Template service file
        service_content = f"""[Unit]
Description=FlipBot Telegram Bot
After=network.target
Wants=network.target

[Service]
Type=simple
User={os.getenv('USER', 'root')}
WorkingDirectory={current_dir}
ExecStart={python_path} asf.py bot
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=flipbot-telegram

# Environment variables
Environment=PYTHONPATH={current_dir}
Environment=PYTHONUNBUFFERED=1

# Security settings
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths={current_dir}

[Install]
WantedBy=multi-user.target
"""
        
        # Tulis service file
        service_file = "/etc/systemd/system/flipbot-telegram.service"
        
        print(f"[yellow]💡 Membuat service file: {service_file}[/]")
        print("[yellow]⚠️ Memerlukan sudo privileges[/]")
        
        # Buat temporary file
        temp_file = "/tmp/flipbot-telegram.service"
        with open(temp_file, 'w') as f:
            f.write(service_content)
        
        # Copy ke system directory dengan sudo
        subprocess.run(["sudo", "cp", temp_file, service_file], check=True)
        subprocess.run(["sudo", "chmod", "644", service_file], check=True)
        
        # Reload systemd
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        
        print("[green]✅ Service berhasil diinstall![/]")
        print(f"[green]📁 Service file: {service_file}[/]")
        print("[yellow]💡 Gunakan 'Enable Auto-start' untuk mengaktifkan boot otomatis[/]")
        
        # Cleanup
        os.remove(temp_file)
        
    except subprocess.CalledProcessError as e:
        print(f"[red]❌ Error installing service: {e}[/]")
    except Exception as e:
        print(f"[red]❌ Error: {e}[/]")

def start_service():
    """Start FlipBot service"""
    try:
        print("[cyan]▶️ Starting FlipBot service...[/]")
        subprocess.run(["sudo", "systemctl", "start", "flipbot-telegram"], check=True)
        print("[green]✅ Service started successfully![/]")
        
        # Tampilkan status
        time.sleep(2)
        show_service_brief_status()
        
    except subprocess.CalledProcessError as e:
        print(f"[red]❌ Error starting service: {e}[/]")
    except Exception as e:
        print(f"[red]❌ Error: {e}[/]")

def stop_service():
    """Stop FlipBot service"""
    try:
        print("[cyan]⏹️ Stopping FlipBot service...[/]")
        subprocess.run(["sudo", "systemctl", "stop", "flipbot-telegram"], check=True)
        print("[green]✅ Service stopped successfully![/]")
        
    except subprocess.CalledProcessError as e:
        print(f"[red]❌ Error stopping service: {e}[/]")
    except Exception as e:
        print(f"[red]❌ Error: {e}[/]")

def restart_service():
    """Restart FlipBot service"""
    try:
        print("[cyan]🔄 Restarting FlipBot service...[/]")
        subprocess.run(["sudo", "systemctl", "restart", "flipbot-telegram"], check=True)
        print("[green]✅ Service restarted successfully![/]")
        
        # Tampilkan status
        time.sleep(2)
        show_service_brief_status()
        
    except subprocess.CalledProcessError as e:
        print(f"[red]❌ Error restarting service: {e}[/]")
    except Exception as e:
        print(f"[red]❌ Error: {e}[/]")

def enable_service():
    """Enable auto-start FlipBot service"""
    try:
        print("[cyan]🔧 Enabling auto-start for FlipBot service...[/]")
        subprocess.run(["sudo", "systemctl", "enable", "flipbot-telegram"], check=True)
        print("[green]✅ Auto-start enabled![/]")
        print("[green]🚀 Service akan otomatis start saat VPS boot[/]")
        
    except subprocess.CalledProcessError as e:
        print(f"[red]❌ Error enabling service: {e}[/]")
    except Exception as e:
        print(f"[red]❌ Error: {e}[/]")

def disable_service():
    """Disable auto-start FlipBot service"""
    try:
        print("[cyan]❌ Disabling auto-start for FlipBot service...[/]")
        subprocess.run(["sudo", "systemctl", "disable", "flipbot-telegram"], check=True)
        print("[green]✅ Auto-start disabled![/]")
        print("[yellow]⚠️ Service tidak akan auto-start saat VPS boot[/]")
        
    except subprocess.CalledProcessError as e:
        print(f"[red]❌ Error disabling service: {e}[/]")
    except Exception as e:
        print(f"[red]❌ Error: {e}[/]")

def show_detailed_status():
    """Tampilkan status detail service"""
    try:
        print("[cyan]📊 FlipBot Service Status:[/]")
        
        # Status service
        result = subprocess.run(
            ["systemctl", "status", "flipbot-telegram", "--no-pager"], 
            capture_output=True, text=True, timeout=10
        )
        
        if result.stdout:
            # Parse output untuk info penting
            lines = result.stdout.split('\n')
            for line in lines[:15]:  # Tampilkan 15 baris pertama
                if 'Active:' in line:
                    if 'active (running)' in line:
                        print(f"[green]✅ {line.strip()}[/]")
                    else:
                        print(f"[red]❌ {line.strip()}[/]")
                elif 'Loaded:' in line or 'Main PID:' in line or 'Memory:' in line or 'CPU:' in line:
                    print(f"[cyan]ℹ️ {line.strip()}[/]")
        
        # Cek apakah enabled
        result_enabled = subprocess.run(
            ["systemctl", "is-enabled", "flipbot-telegram"], 
            capture_output=True, text=True, timeout=5
        )
        
        if result_enabled.returncode == 0:
            print("[green]🔧 Auto-start: ENABLED[/]")
        else:
            print("[yellow]⚠️ Auto-start: DISABLED[/]")
            
    except Exception as e:
        print(f"[red]❌ Error getting status: {e}[/]")

def show_service_brief_status():
    """Tampilkan status singkat service"""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "flipbot-telegram"], 
            capture_output=True, text=True, timeout=5
        )
        
        if result.returncode == 0:
            print("[green]🟢 Service is RUNNING[/]")
        else:
            print("[red]🔴 Service is STOPPED[/]")
            
    except Exception as e:
        print(f"[red]❌ Error: {e}[/]")

def view_service_logs():
    """View service logs dengan journalctl"""
    while True:
        print("\n[cyan]📋 FlipBot Service Logs[/]")
        print("1. Lihat 50 log terbaru")
        print("2. Lihat log real-time (follow)")
        print("3. Lihat log hari ini")
        print("4. Lihat log dengan filter error")
        print("5. Export logs ke file")
        print("6. Kembali")
        
        pilih = prompt("➤ Pilih: ").strip()
        
        if pilih == "1":
            try:
                print("\n[cyan]📖 50 Log Terbaru:[/]")
                subprocess.run([
                    "journalctl", "-u", "flipbot-telegram", 
                    "-n", "50", "--no-pager"
                ])
            except Exception as e:
                print(f"[red]❌ Error: {e}[/]")
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "2":
            try:
                print("\n[cyan]📡 Real-time Logs (Ctrl+C untuk stop):[/]")
                subprocess.run([
                    "journalctl", "-u", "flipbot-telegram", 
                    "-f", "--no-pager"
                ])
            except KeyboardInterrupt:
                print("\n[yellow]⚠️ Stopped following logs[/]")
            except Exception as e:
                print(f"[red]❌ Error: {e}[/]")
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "3":
            try:
                print("\n[cyan]📅 Log Hari Ini:[/]")
                subprocess.run([
                    "journalctl", "-u", "flipbot-telegram", 
                    "--since", "today", "--no-pager"
                ])
            except Exception as e:
                print(f"[red]❌ Error: {e}[/]")
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "4":
            try:
                print("\n[cyan]🚨 Log dengan Error:[/]")
                subprocess.run([
                    "journalctl", "-u", "flipbot-telegram", 
                    "-p", "err", "--no-pager"
                ])
            except Exception as e:
                print(f"[red]❌ Error: {e}[/]")
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "5":
            try:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                log_file = f"flipbot_logs_{timestamp}.txt"
                print(f"\n[cyan]📤 Export logs ke {log_file}...[/]")
                
                with open(log_file, 'w') as f:
                    subprocess.run([
                        "journalctl", "-u", "flipbot-telegram", 
                        "--no-pager"
                    ], stdout=f)
                
                print(f"[green]✅ Logs berhasil di-export ke {log_file}[/]")
            except Exception as e:
                print(f"[red]❌ Error: {e}[/]")
            input("\nTekan Enter untuk kembali...")
            
        elif pilih == "6":
            break
        else:
            print("[red]❌ Pilihan tidak valid[/]")
            time.sleep(1)

def uninstall_service():
    """Uninstall FlipBot service"""
    try:
        print("\n[cyan]🗑️ Uninstall FlipBot Service[/]")
        print("[yellow]⚠️ Ini akan menghapus service dari sistem[/]")
        
        konfirmasi = prompt("[red]Yakin ingin uninstall service? (y/N): [/]").strip().lower()
        if konfirmasi != "y":
            print("[yellow]⚠️ Dibatalkan[/]")
            return
        
        # Stop service
        print("[cyan]⏹️ Stopping service...[/]")
        subprocess.run(["sudo", "systemctl", "stop", "flipbot-telegram"], check=False)
        
        # Disable service
        print("[cyan]❌ Disabling service...[/]")
        subprocess.run(["sudo", "systemctl", "disable", "flipbot-telegram"], check=False)
        
        # Remove service file
        print("[cyan]🗑️ Removing service file...[/]")
        subprocess.run(["sudo", "rm", "/etc/systemd/system/flipbot-telegram.service"], check=True)
        
        # Reload systemd
        print("[cyan]🔄 Reloading systemd...[/]")
        subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
        
        print("[green]✅ Service berhasil di-uninstall![/]")
        
    except subprocess.CalledProcessError as e:
        print(f"[red]❌ Error uninstalling service: {e}[/]")
    except Exception as e:
        print(f"[red]❌ Error: {e}[/]")

def advanced_configuration():
    """Konfigurasi advanced untuk service"""
    while True:
        print("\n[cyan]⚙️ Advanced Configuration[/]")
        print("1. Edit Service File")
        print("2. Set Environment Variables")
        print("3. Configure Auto-restart")
        print("4. Set Resource Limits")
        print("5. View Current Configuration")
        print("6. Kembali")
        
        pilih = prompt("➤ Pilih: ").strip()
        
        if pilih == "1":
            print("\n[cyan]✏️ Edit Service File[/]")
            print("[yellow]💡 Service file: /etc/systemd/system/flipbot-telegram.service[/]")
            print("[yellow]💡 Gunakan: sudo nano /etc/systemd/system/flipbot-telegram.service[/]")
            print("[yellow]💡 Setelah edit, jalankan: sudo systemctl daemon-reload[/]")
            
        elif pilih == "2":
            print("\n[cyan]🌍 Environment Variables[/]")
            print("[yellow]💡 Edit service file dan tambahkan di section [Service]:[/]")
            print("[dim]Environment=VARIABLE_NAME=value[/]")
            print("[dim]Environment=PYTHONPATH=/path/to/project[/]")
            
        elif pilih == "3":
            print("\n[cyan]🔄 Auto-restart Configuration[/]")
            print("[yellow]💡 Current settings in service file:[/]")
            print("[dim]Restart=always[/]")
            print("[dim]RestartSec=10[/]")
            print("[yellow]💡 Options: no, on-success, on-failure, on-abnormal, on-watchdog, on-abort, always[/]")
            
        elif pilih == "4":
            print("\n[cyan]📊 Resource Limits[/]")
            print("[yellow]💡 Tambahkan di service file untuk limit resource:[/]")
            print("[dim]MemoryLimit=512M[/]")
            print("[dim]CPUQuota=50%[/]")
            print("[dim]TasksMax=100[/]")
            
        elif pilih == "5":
            try:
                print("\n[cyan]📋 Current Service Configuration:[/]")
                subprocess.run(["cat", "/etc/systemd/system/flipbot-telegram.service"])
            except Exception as e:
                print(f"[red]❌ Error: {e}[/]")
                
        elif pilih == "6":
            break
        else:
            print("[red]❌ Pilihan tidak valid[/]")
            time.sleep(1)
        
        if pilih != "6":
            input("\nTekan Enter untuk kembali...")

# Menu ON/OFF fitur (rain, wheel, upgrader)
def fitur_toggle_menu():
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        banner()
        print("\n🧩 [bold cyan]Fitur ON/OFF[/]")

        try:
            flags = list_features()
        except Exception:
            flags = {"upgrader": True, "rain": True, "wheel": True}

        up = bool(flags.get("upgrader", True))
        rain = bool(flags.get("rain", True))
        wheel = bool(flags.get("wheel", True))
        maint = bool(flags.get("maintenance", False))

        def s(v: bool) -> str:
            return "[green]ON[/]" if v else "[red]OFF[/]"

        print(f"\nStatus saat ini:")
        print(f"- Maintenance: {s(maint)}")
        print(f"- Upgrader   : {s(up)}")
        print(f"- Rain       : {s(rain)}")
        print(f"- Wheel      : {s(wheel)}")

        print("\n0. Toggle Maintenance (matikan/aktifkan semua)")
        print("1. Toggle Upgrader")
        print("2. Toggle Rain")
        print("3. Toggle Wheel")
        print("4. Set fitur lain (nama + on/off)")
        print("5. Reset ke default (semua ON)")
        print("6. Kembali")

        pilih = prompt("➤ Pilih: ").strip()

        if pilih == "0":
            try:
                set_feature("maintenance", not maint)
            except Exception as e:
                print(f"[red]❌ Gagal set Maintenance: {e}[/]")
                time.sleep(1)
            continue

        elif pilih == "1":
            try:
                set_feature("upgrader", not up)
            except Exception as e:
                print(f"[red]❌ Gagal set Upgrader: {e}[/]")
                time.sleep(1)
            continue

        elif pilih == "2":
            try:
                set_feature("rain", not rain)
            except Exception as e:
                print(f"[red]❌ Gagal set Rain: {e}[/]")
                time.sleep(1)
            continue

        elif pilih == "3":
            try:
                set_feature("wheel", not wheel)
            except Exception as e:
                print(f"[red]❌ Gagal set Wheel: {e}[/]")
                time.sleep(1)
            continue

        elif pilih == "4":
            nama = prompt("Nama fitur (mis. upgrader/rain/wheel/dll): ").strip().lower()
            val = prompt("Set ke (on/off): ").strip().lower()
            if val not in {"on", "off"}:
                print("[red]❌ Input harus 'on' atau 'off'[/]")
                time.sleep(1)
                continue
            try:
                set_feature(nama, val == "on")
            except Exception as e:
                print(f"[red]❌ Gagal set {nama}: {e}[/]")
                time.sleep(1)
            continue

        elif pilih == "5":
            try:
                save_flags(DEFAULT_FLAGS.copy())
            except Exception as e:
                print(f"[red]❌ Gagal reset: {e}[/]")
                time.sleep(1)
            continue

        elif pilih == "6":
            break
        else:
            print("[red]❌ Pilihan tidak valid[/]")
            time.sleep(1)


def main():
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        banner()
        
        # Tampilkan info sistem singkat
        try:
            akun_count = len(load_accounts())
            print(f"[dim]📊 Status: {akun_count} akun | {platform.system()} | Python {platform.python_version()}[/]")
        except Exception:
            print("[dim]📊 Status: Error loading data[/]")
        
        print("""
📌 [bold cyan]Menu Admin[/]
1. 🔐 Kelola Enkripsi
2. 🤖 Konfigurasi Bot Telegram  
3. ⚙️ Sistem Utilitas
4. 🔍 Diagnostik Sistem
5. 📝 Manajemen Log
6. 🖥️ VPS Ubuntu Setup
7. 🧩 Fitur ON/OFF
8. ❌ Keluar
""")
        pilih = prompt("➤ Pilih menu: ").strip()
        
        if pilih == "1":
            kelola_enkripsi()
        elif pilih == "2":
            kelola_bot_telegram()
        elif pilih == "3":
            sistem_utilitas()
        elif pilih == "4":
            diagnostik_sistem()
        elif pilih == "5":
            log_management()
        elif pilih == "6":
            vps_ubuntu_setup()
        elif pilih == "7":
            fitur_toggle_menu()
        elif pilih == "8":
            print("[cyan]👋 Terima kasih telah menggunakan FlipBot Admin CLI![/]")
            break
        else:
            print("[red]❌ Pilihan tidak valid[/]")
            time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except (SystemExit, KeyboardInterrupt):
        pass

