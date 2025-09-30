import sys
from bot_module import start_bot
from botflipgg import main as start_cli
from asf_core import generate_key

if __name__ == "__main__":
    generate_key()
    if len(sys.argv) > 1:
        mode = sys.argv[1].lower()
        if mode == "cli":
            start_cli()
        elif mode == "gui":
            # Lazy import GUI agar tidak error di server headless
            try:
                from gui import start_gui
            except Exception as e:
                print(f"❌ Gagal memuat GUI: {e}")
                sys.exit(1)
            start_gui()
        elif mode == "bot":
            start_bot()
        else:
            print("❌ Mode tidak dikenali. Gunakan: cli / gui / bot")
    else:
        print("❗ Gunakan perintah dengan argumen: cli / gui / bot")
