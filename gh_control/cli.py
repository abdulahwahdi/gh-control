"""Command line interface for gh-control.

Each subcommand is a `cmd_<name>(args)` function registered in
`build_parser()`, so new commands can be added in one place.
"""

import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
from typing import List, Optional

from gh_control import __version__, core, installer, menu, updater


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


def _format_identity(name: Optional[str], email: Optional[str]) -> str:
    return "{} <{}>".format(name or "(no name)", email or "no email")


def _report_switch(account: core.Account, cfg: core.Config) -> None:
    print("Switched to {} {} ({})".format(account.icon, account.login, account.label))
    if not cfg.set_git_identity:
        return
    if account.git_name or account.git_email:
        print("Git identity: {}".format(_format_identity(account.git_name, account.git_email)))
    if account.git_warning:
        print("gh-control: could not fetch git identity: {}".format(account.git_warning),
              file=sys.stderr)


def cmd_switch(args) -> int:
    cfg = core.load_config()
    account = core.switch(args.user, cfg, notify_user=False if args.no_notify else None)
    _report_switch(account, cfg)
    return 0


def cmd_toggle(args) -> int:
    cfg = core.load_config()
    target = core.next_account(cfg)
    account = core.switch(target.login, cfg, notify_user=False if args.no_notify else None)
    _report_switch(account, cfg)
    return 0


def _find_account(login: str, cfg: core.Config) -> core.Account:
    accounts = core.list_accounts(cfg)
    if not accounts:
        raise core.NotLoggedInError("No GitHub accounts found. Run: gh auth login")
    for account in accounts:
        if account.login == login:
            return account
    raise core.UnknownAccountError(
        "Unknown account '{}' on {}. Known accounts: {}".format(
            login, cfg.host, ", ".join(a.login for a in accounts)
        )
    )


def _target_account(login: Optional[str], cfg: core.Config) -> core.Account:
    if login:
        return _find_account(login, cfg)
    account = core.active_account(cfg)
    if account is None:
        raise core.NotLoggedInError("No active GitHub account. Run: gh auth login")
    return account


def _identity_show(args) -> int:
    cfg = core.load_config()
    accounts = core.list_accounts(cfg)
    git = {
        "global": core.read_git_identity("global"),
        "local": core.read_git_identity("local"),
        "effective": core.read_git_identity(),
    }
    if args.json:
        print(json.dumps({"accounts": [a.to_dict() for a in accounts], "git": git},
                         indent=2, ensure_ascii=False))
        return 0
    if not accounts:
        raise core.NotLoggedInError("No GitHub accounts found. Run: gh auth login")
    for account in accounts:
        mark = "*" if account.active else " "
        if account.git_name or account.git_email:
            identity = "{}  ({})".format(
                _format_identity(account.git_name, account.git_email), account.git_source
            )
        else:
            identity = "(no git identity — run: gh-control identity sync)"
        print("{} {} {}  {}".format(mark, account.icon, account.login, identity))
    print("git global: {}".format(_format_identity(git["global"]["name"], git["global"]["email"])))
    local = git["local"]
    in_repo = bool(local["name"] or local["email"])
    if in_repo:
        print("this repo: {}".format(_format_identity(local["name"], local["email"])))
    active = next((a for a in accounts if a.active), None)
    effective = git["effective"]["email"]
    if active is not None and active.git_email and effective != active.git_email:
        print("! git commits here use <{}>, but the active account is {}. "
              "Fix: gh-control identity apply{}".format(
                  effective or "no email", active.login, " --local" if in_repo else ""))
    return 0


def _identity_sync(args) -> int:
    # An explicit sync fetches even when auto_git_identity is off.
    cfg = dataclasses.replace(core.load_config(), auto_git_identity=True)
    targets = [_find_account(args.user, cfg)] if args.user else core.list_accounts(cfg)
    if not targets:
        raise core.NotLoggedInError("No GitHub accounts found. Run: gh auth login")
    failed = 0
    for account in targets:
        warning = core.ensure_git_identity(account, cfg, refresh=args.force)
        if warning:
            failed += 1
            print("! {}: {}".format(account.login, warning))
        else:
            print("✓ {}: {}".format(
                account.login, _format_identity(account.git_name, account.git_email)))
    return 1 if failed == len(targets) else 0


def _identity_set(args) -> int:
    if args.name is None and args.email is None:
        raise core.GhControlError("identity set: give --name and/or --email")
    if args.email and "@" not in args.email:
        raise core.GhControlError("Invalid email address: {}".format(args.email))
    cfg = core.load_config()
    _find_account(args.login, cfg)
    updates = {}
    if args.name is not None:
        updates["git_name"] = args.name.strip()
    if args.email is not None:
        updates["git_email"] = args.email.strip()
    print("Saved to {}".format(core.update_account_config(args.login, updates)))
    cfg = core.load_config()
    account = _find_account(args.login, cfg)
    if account.active and cfg.set_git_identity:
        core.apply_git_identity(account)
        print("{} is the active account, so the global git identity is now: {}".format(
            account.login, _format_identity(account.git_name, account.git_email)))
    return 0


def _identity_apply(args) -> int:
    cfg = core.load_config()
    account = _target_account(args.user, cfg)
    warning = core.ensure_git_identity(account, cfg)
    if warning:
        print("gh-control: could not fetch git identity: {}".format(warning), file=sys.stderr)
    if not (account.git_name or account.git_email):
        raise core.GhControlError(
            "No git identity for {0}. Run: gh-control identity set {0} --name ... --email ...".format(
                account.login
            )
        )
    scope = "local" if args.local else "global"
    core.apply_git_identity(account, scope=scope, cwd=args.path)
    where = ""
    if args.local:
        where = " for {}".format(os.path.abspath(args.path or os.getcwd()))
    print("Set {} git identity{}: {}".format(
        scope, where, _format_identity(account.git_name, account.git_email)))
    return 0


IDENTITY_ACTIONS = {
    "show": _identity_show,
    "sync": _identity_sync,
    "set": _identity_set,
    "apply": _identity_apply,
}


def cmd_identity(args) -> int:
    return IDENTITY_ACTIONS[args.action or "show"](args)


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
                "git_name": account.git_name or "",
                "git_email": account.git_email or "",
            }
    except core.GhControlError:
        pass
    return {
        "host": core.DEFAULT_HOST,
        "notify": True,
        "set_git_identity": True,
        "auto_git_identity": True,
        "check_updates": True,
        "accounts": accounts,
    }


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


def _update_check(args) -> int:
    info = updater.check(force=True)
    if args.json:
        data = info.to_dict()
        data.update(current=__version__, method=updater.install_method(), newer=info.newer)
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif args.quiet:
        pass
    elif not info.tag:
        print("No release published yet")
    elif info.newer:
        print("Update available: {} -> {}  ({})\nRun: gh-control update".format(
            __version__, info.tag, info.url or updater.repo()))
    else:
        print("gh-control {} is up to date".format(__version__))
    return 0


def cmd_update(args) -> int:
    try:
        if args.check:
            return _update_check(args)
        return updater.run_update(dry_run=args.dry_run)
    except core.GhControlError:
        if args.quiet:
            return 1
        raise


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

    p = sub.add_parser("identity", help="show and manage the git identity of each account")
    p.set_defaults(func=cmd_identity, action="show", json=False)
    actions = p.add_subparsers(dest="action", metavar="ACTION")

    a = actions.add_parser("show", help="show each account's git identity (default)")
    a.add_argument("--json", action="store_true", help="output JSON")

    a = actions.add_parser("sync", help="fetch names and noreply emails from GitHub")
    a.add_argument("--force", action="store_true", help="refetch even if already known")
    a.add_argument("--user", metavar="LOGIN", help="only sync this account")

    a = actions.add_parser("set", help="save a git identity for an account in the config")
    a.add_argument("login", help="GitHub login")
    a.add_argument("--name", help="git user.name (empty string removes it)")
    a.add_argument("--email", help="git user.email (empty string removes it)")

    a = actions.add_parser("apply", help="write an account's identity to git")
    a.add_argument("--local", action="store_true",
                   help="set it for the current repository only")
    a.add_argument("--user", metavar="LOGIN", help="account to use (default: active)")
    a.add_argument("--path", metavar="DIR", help="repository to use with --local")

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

    p = sub.add_parser("update", help="check for and install the latest gh-control release")
    p.add_argument("--check", action="store_true", help="only check, do not install")
    p.add_argument("--json", action="store_true", help="with --check: output JSON")
    p.add_argument("--dry-run", action="store_true", help="only show what would be done")
    p.add_argument("--quiet", action="store_true", help="print nothing (for background checks)")
    p.set_defaults(func=cmd_update)

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
