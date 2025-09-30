import json
import os
import threading
from typing import Dict, Any

# Lokasi penyimpanan file konfigurasi
_BASE_DIR = os.path.dirname(__file__)
_CONFIG_DIR = os.path.join(_BASE_DIR, 'config')
_CONFIG_PATH = os.path.join(_CONFIG_DIR, 'feature_flags.json')

# Default: semua fitur ON
DEFAULT_FLAGS: Dict[str, bool] = {
    'upgrader': True,
    'rain': True,
    'wheel': True,
    'maintenance': False,
}

_lock = threading.RLock()


def _ensure_config_dir() -> None:
    if not os.path.isdir(_CONFIG_DIR):
        os.makedirs(_CONFIG_DIR, exist_ok=True)


def _ensure_config_file() -> None:
    """Pastikan file konfigurasi ada. Bila tidak, tulis default."""
    _ensure_config_dir()
    if not os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_FLAGS, f, ensure_ascii=False, indent=2, sort_keys=True)


def load_flags() -> Dict[str, bool]:
    """Muat flag dari file. Jika rusak/tidak ada, kembalikan default dan tulis ulang."""
    with _lock:
        _ensure_config_file()
        try:
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                data: Dict[str, Any] = json.load(f)
                # Pastikan semua key default minimal ada
                merged: Dict[str, bool] = {**DEFAULT_FLAGS, **{k: bool(v) for k, v in data.items()}}
                # Bila ada perubahan struktur, simpan kembali
                if merged != data:
                    with open(_CONFIG_PATH, 'w', encoding='utf-8') as fw:
                        json.dump(merged, fw, ensure_ascii=False, indent=2, sort_keys=True)
                return merged
        except Exception:
            # Bila gagal baca, tulis ulang default
            with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_FLAGS, f, ensure_ascii=False, indent=2, sort_keys=True)
            return DEFAULT_FLAGS.copy()


def save_flags(flags: Dict[str, bool]) -> None:
    with _lock:
        _ensure_config_dir()
        # Normalisasi boolean
        normalized = {k: bool(v) for k, v in flags.items()}
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2, sort_keys=True)


def is_enabled(feature_name: str) -> bool:
    """Cek apakah fitur aktif. Jika key tidak ada, default True kecuali maintenance."""
    flags = load_flags()
    # Special case: maintenance default ke False jika tidak ada
    if feature_name == 'maintenance':
        return bool(flags.get(feature_name, False))
    return bool(flags.get(feature_name, True))


def set_feature(feature_name: str, enabled: bool) -> Dict[str, bool]:
    """Set ON/OFF fitur dan simpan. Mengembalikan state terbaru."""
    with _lock:
        flags = load_flags()
        flags[feature_name] = bool(enabled)
        save_flags(flags)
        return flags


def list_features() -> Dict[str, bool]:
    return load_flags()


__all__ = [
    'DEFAULT_FLAGS',
    'load_flags',
    'save_flags',
    'is_enabled',
    'set_feature',
    'list_features',
]
