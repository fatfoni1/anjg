#!/usr/bin/env python3
"""
FlipBot Dependencies Installer
Script untuk menginstall semua dependencies yang diperlukan di VPS Ubuntu
"""

import os
import sys
import platform
import subprocess
from datetime import datetime

def print_banner():
    print("=" * 60)
    print("🔧 FlipBot Dependencies Installer")
    print("📦 Installer untuk VPS Ubuntu")
    print("=" * 60)

def check_system():
    """Cek sistem operasi dan versi Python"""
    print(f"🖥️ Sistem: {platform.system()} {platform.release()}")
    print(f"🐍 Python: {platform.python_version()}")
    print(f"📍 Python Path: {sys.executable}")
    
    if platform.system() != "Linux":
        print("⚠️ Script ini dioptimalkan untuk Linux/Ubuntu")
        response = input("Lanjutkan? (y/N): ").strip().lower()
        if response != "y":
            print("❌ Instalasi dibatalkan")
            sys.exit(1)
    
    # Cek versi Python minimum
    if sys.version_info < (3, 8):
        print("❌ Python 3.8+ diperlukan")
        sys.exit(1)
    
    print("✅ Sistem check passed")

def install_ubuntu_packages():
    """Install system packages untuk Ubuntu"""
    packages = [
        "python3-pip", "python3-dev", "python3-venv", "build-essential",
        "libssl-dev", "libffi-dev", "libnss3-dev", "libatk-bridge2.0-dev",
        "libdrm2", "libxkbcommon-dev", "libxcomposite-dev", "libxdamage-dev",
        "libxrandr-dev", "libgbm-dev", "libxss-dev", "libasound2-dev",
        "curl", "wget", "git"
    ]
    
    try:
        print("\n📋 Updating package list...")
        subprocess.run(["sudo", "apt", "update"], check=True)
        
        print(f"📦 Installing {len(packages)} system packages...")
        cmd = ["sudo", "apt", "install", "-y"] + packages
        subprocess.run(cmd, check=True)
        
        print("✅ Ubuntu system packages installed successfully!")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Error installing Ubuntu packages: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def install_python_packages():
    """Install Python packages"""
    packages = {
        "requests": ">=2.31.0",
        "cryptography": ">=41.0.0", 
        "aiohttp": ">=3.8.0",
        "python-telegram-bot": ">=20.0",
        "playwright": ">=1.40.0",
        "prompt-toolkit": ">=3.0.0",
        "rich": ">=13.0.0",
        "customtkinter": ">=5.2.0",
        "psutil": ">=5.9.0",
        "typing-extensions": ">=4.8.0"
    }
    
    try:
        # Upgrade pip first
        print("\n🔧 Upgrading pip...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=True)
        
        # Install packages
        print(f"🐍 Installing {len(packages)} Python packages...")
        for package, version in packages.items():
            print(f"  Installing {package}{version}...")
            subprocess.run([sys.executable, "-m", "pip", "install", f"{package}{version}"], check=True)
        
        print("✅ Python packages installed successfully!")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Error installing Python packages: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

def install_playwright_browsers():
    """Install Playwright browsers"""
    try:
        print("\n🌐 Installing Playwright browsers...")
        subprocess.run([sys.executable, "-m", "playwright", "install"], check=True)
        
        if platform.system() == "Linux":
            print("🔧 Installing browser system dependencies...")
            subprocess.run([sys.executable, "-m", "playwright", "install-deps"], check=True)
        
        print("✅ Playwright browsers installed successfully!")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Error installing Playwright browsers: {e}")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False

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
        print("✅ requirements.txt created successfully!")
        return True
    except Exception as e:
        print(f"❌ Error creating requirements.txt: {e}")
        return False

def verify_installation():
    """Verifikasi instalasi"""
    print("\n🔍 Verifying installation...")
    
    required_packages = [
        "requests", "cryptography", "aiohttp", "telegram",
        "playwright", "prompt_toolkit", "rich", "customtkinter", "psutil"
    ]
    
    success_count = 0
    for package in required_packages:
        try:
            __import__(package)
            print(f"✅ {package}")
            success_count += 1
        except ImportError:
            print(f"❌ {package}")
    
    print(f"\n📊 Verification: {success_count}/{len(required_packages)} packages OK")
    
    if success_count == len(required_packages):
        print("🎉 All dependencies installed successfully!")
        return True
    else:
        print("⚠️ Some packages failed to install")
        return False

def main():
    print_banner()
    
    # System check
    check_system()
    
    # Konfirmasi
    print("\nScript ini akan menginstall:")
    print("• Ubuntu system packages (memerlukan sudo)")
    print("• Python packages via pip")
    print("• Playwright browsers")
    print("• Membuat requirements.txt")
    
    response = input("\nLanjutkan instalasi? (y/N): ").strip().lower()
    if response != "y":
        print("❌ Instalasi dibatalkan")
        sys.exit(0)
    
    start_time = datetime.now()
    print(f"\n🚀 Memulai instalasi pada {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    success_steps = 0
    total_steps = 4
    
    # Step 1: Ubuntu packages
    if platform.system() == "Linux":
        print("\n" + "="*50)
        print("STEP 1/4: Installing Ubuntu System Packages")
        print("="*50)
        if install_ubuntu_packages():
            success_steps += 1
    else:
        print("\n⏭️ Skipping Ubuntu packages (not Linux)")
        success_steps += 1
    
    # Step 2: Python packages
    print("\n" + "="*50)
    print("STEP 2/4: Installing Python Packages")
    print("="*50)
    if install_python_packages():
        success_steps += 1
    
    # Step 3: Playwright browsers
    print("\n" + "="*50)
    print("STEP 3/4: Installing Playwright Browsers")
    print("="*50)
    if install_playwright_browsers():
        success_steps += 1
    
    # Step 4: Create requirements.txt
    print("\n" + "="*50)
    print("STEP 4/4: Creating requirements.txt")
    print("="*50)
    if create_requirements_file():
        success_steps += 1
    
    # Verification
    print("\n" + "="*50)
    print("VERIFICATION")
    print("="*50)
    verify_installation()
    
    # Summary
    end_time = datetime.now()
    duration = end_time - start_time
    
    print("\n" + "="*60)
    print("📋 INSTALLATION SUMMARY")
    print("="*60)
    print(f"⏱️ Duration: {duration}")
    print(f"✅ Successful steps: {success_steps}/{total_steps}")
    
    if success_steps == total_steps:
        print("🎉 Installation completed successfully!")
        print("\n📝 Next steps:")
        print("1. Run: python botflipgg.py")
        print("2. Go to: Sistem Utilitas > Dependencies Management")
        print("3. Use: Cek Status Dependencies untuk verifikasi")
    else:
        print("⚠️ Installation completed with some issues")
        print("💡 Check error messages above and retry failed steps")
    
    print("="*60)

if __name__ == "__main__":
    main()