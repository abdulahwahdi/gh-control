"""Command line interface for gh-control.

Each subcommand is a `cmd_<name>(args)` function registered in
`build_parser()`, so new commands can be added in one place.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import List, Optional

from gh_control import __version__, core, installer, menu


def _print_account(account: core.Account) -> None:
    mark = "*" if account.active else " "
    extra = "" if account.label == account.login else "  ({})".format(account.label)
    print("{} {} {}{}".format(mark, account.icon, account.login, extra))


def cmd_list(args) -> int:
    accounts = core.list_accounts()
    if args.json:
        print(json.dumps([a.to_dict() for a in accounts], indent=2, ensure_ascii=False))
        return 0
    if not accounts:
        raise core.NotLoggedInError("No GitHub accounts found. Run: gh auth login")
    for account in accounts:
        _print_account(account)
    return 0


def cmd_current(args) -> int:
    account = core.active_account()
    if account is None:
        raise core.NotLoggedInError("No active GitHub account. Run: gh auth login")
    if args.json:
        print(json.dumps(account.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(account.login)
    return 0


def _report_switch(account: core.Account) -> None:
    print("Switched to {} {} ({})".format(account.icon, account.login, account.label))


def cmd_switch(args) -> int:
    _report_switch(core.switch(args.user, notify_user=False if args.no_notify else None))
    return 0


def cmd_toggle(args) -> int:
    cfg = core.load_config()
    target = core.next_account(cfg)
    _report_switch(core.switch(target.login, cfg, notify_user=False if args.no_notify else None))
    return 0


def cmd_menu(args) -> int:
    print(menu.safe_render(args.format))
    return 0


def _sample_config() -> dict:
    accounts = {}
    try:
        for account in core.list_accounts():
            accounts[account.login] = {
                "label": account.login,
                "icon": core.DEFAULT_ICON,
                "color": "",
                "git_name": "",
                "git_email": "",
            }
    except core.GhControlError:
        pass
    return {"host": core.DEFAULT_HOST, "notify": True, "accounts": accounts}


def cmd_open_config(args) -> int:
    path = core.config_path()
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(_sample_config(), fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    if args.print_path or not shutil.which(opener):
        print(path)
        return 0
    subprocess.Popen(
        [opener, path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return 0


def cmd_install(args) -> int:
    return installer.install(dry_run=args.dry_run, frontend=args.frontend, target_dir=args.plugin_dir)


def cmd_uninstall(args) -> int:
    return installer.uninstall(dry_run=args.dry_run, target_dir=args.plugin_dir)


def cmd_doctor(args) -> int:
    return installer.doctor()


def build_parser()-> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gh-control",
        description="Show and switch the active GitHub CLI (gh) account.",
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("list", help="list logged-in accounts (* = active)")
    p.add_argument("--json", action="store_true", help="output JSON")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("current", help="print the active account")
    p.add_argument("--json", action="store_true", help="output JSON")
    p.set_defaults(func=cmd_current)

    p = sub.add_parser("switch", help="switch to an account")
    p.add_argument("user", help="GitHub login to activate")
    p.add_argument("--no-notify", action="store_true", help="skip the desktop notification")
    p.set_defaults(func=cmd_switch)

    p = sub.add_parser("toggle", help="switch to the next account")
    p.add_argument("--no-notify", action="store_true", help="skip the desktop notification")
    p.set_defaults(func=cmd_toggle)

    p = sub.add_parser("menu", help="print the top-bar menu")
    p.add_argument("--format", choices=menu.FORMATS, default="swiftbar")
    p.set_defaults(func=cmd_menu)

    p = sub.add_parser("open-config", help="create (if missing) and open the config file")
    p.add_argument("--print-path", action="store_true", help="only print the path")
    p.set_defaults(func=cmd_open_config)

    p = sub.add_parser("install", help="install the top-bar plugin for this desktop")
    p.add_argument("--frontend", choices=installer.FRONTENDS, default="auto",
                   help="menu-bar app to install for (default: detect)")
    p.add_argument("--plugin-dir", metavar="DIR", help="override the plugin folder")
    p.add_argument("--dry-run", action="store_true", help="only show what would be done")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall", help="remove the top-bar plugin (config is kept)")
    p.add_argument("--plugin-dir", metavar="DIR", help="also look in this plugin folder")
    p.add_argument("--dry-run", action="store_true", help="only show what would be done")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("doctor", help="check the setup and suggest fixes")
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except core.GhControlError as exc:
        print("gh-control: {}".format(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
