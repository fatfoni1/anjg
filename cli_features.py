import argparse
from typing import Optional
from feature_flags import list_features, set_feature, is_enabled, load_flags, save_flags, DEFAULT_FLAGS


def cmd_list(_: argparse.Namespace) -> int:
    flags = list_features()
    print("Daftar fitur dan status:")
    for k, v in flags.items():
        print(f"- {k}: {'ON' if v else 'OFF'}")
    return 0


def _apply(feature: str, enabled: bool) -> int:
    feature = feature.strip().lower()
    flags = load_flags()
    # Tambahkan key baru jika belum ada
    if feature not in flags:
        flags[feature] = enabled
        save_flags(flags)
    else:
        set_feature(feature, enabled)
    print(f"Set '{feature}' => {'ON' if enabled else 'OFF'}")
    return 0


def cmd_on(ns: argparse.Namespace) -> int:
    return _apply(ns.feature, True)


def cmd_off(ns: argparse.Namespace) -> int:
    return _apply(ns.feature, False)


def cmd_toggle(ns: argparse.Namespace) -> int:
    feature = ns.feature.strip().lower()
    current = is_enabled(feature)
    return _apply(feature, not current)


def cmd_reset(_: argparse.Namespace) -> int:
    flags = DEFAULT_FLAGS.copy()
    save_flags(flags)
    print("Reset feature flags ke default:")
    for k, v in flags.items():
        print(f"- {k}: {'ON' if v else 'OFF'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="CLI ON/OFF fitur (rain, wheel, upgrader) tanpa menyentuh GUI"
    )
    sub = p.add_subparsers(dest='cmd', required=True)

    sp_list = sub.add_parser('list', help='Tampilkan status semua fitur')
    sp_list.set_defaults(func=cmd_list)

    sp_on = sub.add_parser('on', help='Aktifkan sebuah fitur')
    sp_on.add_argument('feature', help='Nama fitur, contoh: upgrader | rain | wheel')
    sp_on.set_defaults(func=cmd_on)

    sp_off = sub.add_parser('off', help='Nonaktifkan sebuah fitur')
    sp_off.add_argument('feature', help='Nama fitur, contoh: upgrader | rain | wheel')
    sp_off.set_defaults(func=cmd_off)

    sp_toggle = sub.add_parser('toggle', help='Toggle sebuah fitur (ON<->OFF)')
    sp_toggle.add_argument('feature', help='Nama fitur, contoh: upgrader | rain | wheel')
    sp_toggle.set_defaults(func=cmd_toggle)

    sp_reset = sub.add_parser('reset', help='Reset semua fitur ke default')
    sp_reset.set_defaults(func=cmd_reset)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    return ns.func(ns)


if __name__ == '__main__':
    raise SystemExit(main())
