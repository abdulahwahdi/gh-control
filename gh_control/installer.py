"""Install / uninstall the top-bar plugin and diagnose the setup.

Plugins ship inside the package (gh_control/plugins/), so this works the
same from a git checkout, a pip/pipx install or Homebrew. Installing means
symlinking the plugin into the menu-bar app's plugin folder (SwiftBar, xbar,
Argos) or writing an autostart entry for the AppIndicator tray. Everything
lives in the user's home directory; root is never needed. All operations are
idempotent and never replace files that gh-control did not create.
"""

import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional, TextIO, Tuple

from gh_control import __version__, core

FRONTENDS = ("auto", "swiftbar", "xbar", "argos", "tray")
MAC_FRONTENDS = ("swiftbar", "xbar")
LINUX_FRONTENDS = ("argos", "tray")
MIN_GH = (2, 40)
MIN_PYTHON = (3, 8)

PLUGIN_ROOT = os.path.join(os.path.dirname(os.path.realpath(__file__)), "plugins")
PLUGIN_FILES = {
    "swiftbar": os.path.join("swiftbar", "gh-control.30s.py"),
    "xbar": os.path.join("swiftbar", "gh-control.30s.py"),
    "argos": os.path.join("argos", "gh-control.30s.sh"),
    "tray": os.path.join("tray", "gh_control_tray.py"),
}
# Basenames of our plugin files; a symlink to one of these is "ours".
PLUGIN_NAMES = ("gh-control.30s.py", "gh-control.30s.sh")
AUTOSTART_FILE = "gh-control-tray.desktop"
AUTOSTART_MARKER = "X-GH-Control-Managed=true"
ARGOS_UUID = "argos@pew.worldwidemann.com"
SWIFTBAR_DOMAIN = "com.ameba.SwiftBar"

OK, FAIL, WARN = "✓", "✗", "!"


class InstallError(core.GhControlError):
    pass


# --------------------------------------------------------------------------
# Paths and detection


def _home() -> str:
    return os.path.expanduser("~")


def _data_home() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.join(_home(), ".local", "share")


def _stable_path(path: str) -> str:
    """Prefer Homebrew's version-independent opt/ path over Cellar/<ver>/."""
    m = re.match(r"^(.*)/Cellar/([^/]+)/[^/]+/(.*)$", path)
    if m:
        alt = os.path.join(m.group(1), "opt", m.group(2), m.group(3))
        if os.path.exists(alt) and os.path.realpath(alt) == os.path.realpath(path):
            return alt
    return path


def plugin_source(frontend: str) -> str:
    return _stable_path(os.path.join(PLUGIN_ROOT, PLUGIN_FILES[frontend]))


def swiftbar_pref_dir() -> Optional[str]:
    """SwiftBar's configured plugin folder, if its preferences are readable."""
    if not shutil.which("defaults"):
        return None
    try:
        proc = subprocess.run(
            ["defaults", "read", SWIFTBAR_DOMAIN, "PluginDirectory"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = proc.stdout.strip()
    if proc.returncode != 0 or not value:
        return None
    return os.path.expanduser(value)


def swiftbar_default_dir() -> str:
    return os.path.join(_home(), "Library", "Application Support", "SwiftBar", "Plugins")


def xbar_dir() -> str:
    return os.path.join(_home(), "Library", "Application Support", "xbar", "plugins")


def argos_dir() -> str:
    return os.path.join(core._xdg_config_home(), "argos")


def autostart_path() -> str:
    return os.path.join(core._xdg_config_home(), "autostart", AUTOSTART_FILE)


def _mac_app(name: str) -> bool:
    return any(
        os.path.isdir(os.path.join(os.path.expanduser(d), name + ".app"))
        for d in ("/Applications", "~/Applications")
    )


def argos_extension_installed() -> bool:
    dirs = [os.path.join(_data_home(), "gnome-shell", "extensions")]
    dirs += ["/usr/local/share/gnome-shell/extensions", "/usr/share/gnome-shell/extensions"]
    return any(os.path.isdir(os.path.join(d, ARGOS_UUID)) for d in dirs)


def _is_mac(platform: Optional[str]) -> bool:
    return (platform or sys.platform) == "darwin"


def detect_frontend(platform: Optional[str] = None) -> str:
    if _is_mac(platform):
        if swiftbar_pref_dir() or os.path.isdir(swiftbar_default_dir()) or _mac_app("SwiftBar"):
            return "swiftbar"
        if os.path.isdir(xbar_dir()) or _mac_app("xbar"):
            return "xbar"
        return "swiftbar"
    if os.path.isdir(argos_dir()) or argos_extension_installed():
        return "argos"
    return "tray"


def plugin_dir(frontend: str) -> str:
    if frontend == "swiftbar":
        return swiftbar_pref_dir() or swiftbar_default_dir()
    if frontend == "xbar":
        return xbar_dir()
    if frontend == "argos":
        return argos_dir()
    raise ValueError("{} has no plugin folder".format(frontend))


def resolve_frontend(frontend: str = "auto", platform: Optional[str] = None) -> str:
    if frontend not in FRONTENDS:
        raise InstallError("Unknown frontend {!r}; choose from {}".format(frontend, ", ".join(FRONTENDS)))
    if frontend == "auto":
        return detect_frontend(platform)
    allowed = MAC_FRONTENDS if _is_mac(platform) else LINUX_FRONTENDS
    if frontend not in allowed:
        raise InstallError(
            "{} is not available on this OS; use one of: {}".format(frontend, ", ".join(allowed))
        )
    return frontend


def _candidate_dirs(platform: Optional[str], extra: Optional[str] = None) -> List[str]:
    if _is_mac(platform):
        dirs = [swiftbar_pref_dir(), swiftbar_default_dir(), xbar_dir()]
    else:
        dirs = [argos_dir()]
    if extra:
        dirs.insert(0, os.path.expanduser(extra))
    seen, result = set(), []
    for d in dirs:
        if d and os.path.realpath(d) not in seen:
            seen.add(os.path.realpath(d))
            result.append(d)
    return result


def _is_our_link(path: str) -> bool:
    return os.path.islink(path) and os.path.basename(os.readlink(path)) in PLUGIN_NAMES


def find_installed(directory: str) -> List[str]:
    """Symlinks in `directory` that point at a gh-control plugin (any interval)."""
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if name.startswith("gh-control.") and _is_our_link(os.path.join(directory, name))
    )


def _is_our_autostart(path: str) -> bool:
    try:
        with open(path, encoding="utf-8") as fh:
            return AUTOSTART_MARKER in fh.read()
    except (OSError, UnicodeDecodeError):
        return False


def _desktop_exec_arg(value: str) -> str:
    if value and all(c.isalnum() or c in "/._-+=:@," for c in value):
        return value
    for ch in ("\\", '"', "`", "$"):
        value = value.replace(ch, "\\" + ch)
    return '"{}"'.format(value).replace("\\", "\\\\")


def autostart_entry(source: str) -> str:
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Name=gh-control",
            "Comment=Show and switch the active GitHub CLI account",
            "Exec=/usr/bin/env python3 {}".format(_desktop_exec_arg(source)),
            "Icon=avatar-default-symbolic",
            "Terminal=false",
            "X-GNOME-Autostart-enabled=true",
            AUTOSTART_MARKER,
            "",
        ]
    )


def tray_deps_available() -> bool:
    """Whether the system python3 can load PyGObject (needed by the tray)."""
    python = shutil.which("python3")
    if not python:
        return False
    try:
        proc = subprocess.run(
            [python, "-c", "import gi"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


# --------------------------------------------------------------------------
# Output


class _Printer:
    def __init__(self, out: Optional[TextIO], dry_run: bool = False):
        self.out = out or sys.stdout
        self.dry_run = dry_run

    def line(self, text: str = "") -> None:
        print(text, file=self.out)

    def mark(self, symbol: str, text: str, fix: Optional[str] = None) -> None:
        self.line("{} {}".format(symbol, text))
        if fix:
            for fix_line in fix.splitlines():
                self.line("    {}".format(fix_line))

    def action(self, text: str) -> None:
        self.line("{} {}".format("[dry-run] would" if self.dry_run else "→", text))


# --------------------------------------------------------------------------
# install / uninstall


def _ensure_executable(path: str, p: _Printer) -> None:
    if os.access(path, os.X_OK):
        return
    if p.dry_run:
        p.action("chmod +x {}".format(path))
        return
    try:
        mode = os.stat(path).st_mode
        os.chmod(path, mode | 0o111)
    except OSError as exc:
        raise InstallError("Plugin {} is not executable and chmod failed: {}".format(path, exc))


def _install_link(directory: str, source: str, p: _Printer) -> Tuple[bool, str, bool]:
    """Symlink `source` into `directory`. Returns (ok, path, changed)."""
    existing = find_installed(directory)
    for link in existing:
        if os.path.realpath(link) == os.path.realpath(source):
            p.mark(OK, "Plugin already installed: {}".format(link))
            return True, link, False
    if existing:
        # Stale link (e.g. old checkout or Homebrew version): re-point it,
        # keeping the user's chosen file name / refresh interval.
        link = existing[0]
        p.action("update {} -> {}".format(link, source))
        if not p.dry_run:
            os.remove(link)
            os.symlink(source, link)
        return True, link, True

    dest = os.path.join(directory, os.path.basename(source))
    if os.path.lexists(dest):
        p.mark(
            FAIL,
            "{} exists and was not created by gh-control; leaving it alone.".format(dest),
            "Move it away and run `gh-control install` again.",
        )
        return False, dest, False
    if not os.path.isdir(directory):
        p.action("create folder {}".format(directory))
        if not p.dry_run:
            os.makedirs(directory, exist_ok=True)
    p.action("link {} -> {}".format(dest, source))
    if not p.dry_run:
        os.symlink(source, dest)
    return True, dest, True


def _install_autostart(source: str, p: _Printer) -> Tuple[bool, str, bool]:
    path = autostart_path()
    content = autostart_entry(source)
    if os.path.lexists(path):
        if not _is_our_autostart(path):
            p.mark(
                FAIL,
                "{} exists and was not created by gh-control; leaving it alone.".format(path),
                "Move it away and run `gh-control install` again.",
            )
            return False, path, False
        with open(path, encoding="utf-8") as fh:
            if fh.read() == content:
                p.mark(OK, "Tray autostart already installed: {}".format(path))
                return True, path, False
        p.action("update {}".format(path))
    else:
        p.action("write {}".format(path))
    if not p.dry_run:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return True, path, True


def _next_steps(frontend: str, source: str, p: _Printer) -> None:
    p.line()
    p.line("Next steps:")
    if frontend == "swiftbar":
        if not _mac_app("SwiftBar"):
            p.line("  • Install SwiftBar: brew install --cask swiftbar  (or https://swiftbar.app)")
        p.line("  • Open SwiftBar; if it asks for a plugin folder, choose the one above.")
        p.line("    Using another folder? Run: gh-control install --plugin-dir <folder>")
    elif frontend == "xbar":
        if not _mac_app("xbar"):
            p.line("  • Install xbar: brew install --cask xbar  (or https://xbarapp.com)")
        p.line("  • Open xbar and choose “Refresh all”.")
    elif frontend == "argos":
        if not argos_extension_installed():
            p.line("  • Install the Argos GNOME extension:")
            p.line("    https://extensions.gnome.org/extension/1176/argos/")
        p.line("  • Argos picks the plugin up automatically (refreshes every 30s).")
    elif frontend == "tray":
        if not tray_deps_available():
            p.line("  • Install the tray dependencies:")
            p.line("      Debian/Ubuntu: sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1")
            p.line("      Fedora:        sudo dnf install python3-gobject libayatana-appindicator-gtk3")
            p.line("      Arch:          sudo pacman -S python-gobject libayatana-appindicator")
        p.line("  • The tray starts at your next login. Start it now with:")
        p.line("      nohup python3 {} >/dev/null 2>&1 &".format(shlex.quote(source)))
    p.line("  • Log in to each account once: gh auth login")
    p.line("  • Check everything: gh-control doctor")


def install(
    dry_run: bool = False,
    frontend: str = "auto",
    target_dir: Optional[str] = None,
    platform: Optional[str] = None,
    out: Optional[TextIO] = None,
) -> int:
    """Install the plugin for `frontend`. Returns a process exit code."""
    p = _Printer(out, dry_run)
    try:
        frontend = resolve_frontend(frontend, platform)
    except InstallError as exc:
        p.mark(FAIL, str(exc))
        return 1
    if target_dir and frontend == "tray":
        p.mark(FAIL, "--plugin-dir does not apply to the tray frontend.")
        return 1
    source = plugin_source(frontend)
    if not os.path.isfile(source):
        p.mark(FAIL, "Plugin file missing: {}".format(source), "Reinstall gh-control.")
        return 1
    p.line("Installing gh-control {} for {}{}".format(
        __version__, frontend, " (dry run)" if dry_run else ""))
    try:
        _ensure_executable(source, p)
        if frontend == "tray":
            ok, path, changed = _install_autostart(source, p)
        else:
            directory = os.path.expanduser(target_dir) if target_dir else plugin_dir(frontend)
            ok, path, changed = _install_link(directory, source, p)
    except (OSError, InstallError) as exc:
        p.mark(FAIL, "Install failed: {}".format(exc))
        return 1
    if not ok:
        return 1
    if changed and not dry_run:
        p.mark(OK, "Installed {} plugin: {}".format(frontend, path))
    _next_steps(frontend, source, p)
    return 0


def uninstall(
    dry_run: bool = False,
    target_dir: Optional[str] = None,
    platform: Optional[str] = None,
    out: Optional[TextIO] = None,
) -> int:
    """Remove every plugin link / autostart entry gh-control created."""
    p = _Printer(out, dry_run)
    removed = 0
    try:
        for directory in _candidate_dirs(platform, target_dir):
            for link in find_installed(directory):
                p.action("remove {}".format(link))
                if not dry_run:
                    os.remove(link)
                removed += 1
        path = autostart_path()
        if os.path.isfile(path) and _is_our_autostart(path):
            p.action("remove {}".format(path))
            if not dry_run:
                os.remove(path)
            removed += 1
    except OSError as exc:
        p.mark(FAIL, "Uninstall failed: {}".format(exc))
        return 1
    if not removed:
        p.mark(OK, "No gh-control plugin installed; nothing to remove.")
    elif not dry_run:
        p.mark(OK, "Removed {} item(s).".format(removed))
    if os.path.exists(core.config_path()):
        p.line("Your config was kept: {}".format(core.config_path()))
    return 0


# --------------------------------------------------------------------------
# doctor


def gh_version(gh: str) -> Optional[Tuple[int, int, int]]:
    try:
        proc = subprocess.run(
            [gh, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            universal_newlines=True,
            timeout=core.GH_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", proc.stdout or "")
    if proc.returncode != 0 or not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def installed_plugins(platform: Optional[str] = None) -> List[str]:
    found = [link for d in _candidate_dirs(platform) for link in find_installed(d)]
    if not _is_mac(platform) and _is_our_autostart(autostart_path()):
        found.append(autostart_path())
    return found


def _doctor_git_identity(p: _Printer, cfg: core.Config, accounts: List[core.Account]) -> None:
    """Warn about missing or mismatched git identities (offline, never a blocker)."""
    if shutil.which("git") is None:
        p.mark(WARN, "git not found; git identity switching is skipped")
        return
    missing = [a.login for a in accounts if not a.has_git_identity()]
    if not missing:
        p.mark(OK, "Git identity set for all {} account(s)".format(len(accounts)))
    elif cfg.set_git_identity:
        p.mark(
            WARN,
            "No git identity for: {}".format(", ".join(missing)),
            "Run: gh-control identity sync  (or: gh-control identity set <login> --name ... --email ...)",
        )
    active = next((a for a in accounts if a.active), None)
    if active and active.git_email and cfg.set_git_identity:
        global_email = core.read_git_identity("global")["email"]
        if global_email != active.git_email:
            p.mark(
                WARN,
                "Global git email is {} but the active account {} uses {}".format(
                    global_email or "(unset)", active.login, active.git_email
                ),
                "Run: gh-control identity apply",
            )


def doctor(platform: Optional[str] = None, out: Optional[TextIO] = None) -> int:
    """Check the setup and print fixes. Returns 1 if something blocks use."""
    p = _Printer(out)
    blockers = 0
    mac = _is_mac(platform)
    p.line("gh-control {} doctor".format(__version__))

    py = sys.version_info
    if py[:2] >= MIN_PYTHON:
        p.mark(OK, "Python {}.{}.{}".format(*py[:3]))
    else:
        blockers += 1
        p.mark(FAIL, "Python {}.{} is too old".format(*py[:2]), "Install Python 3.8 or newer.")

    gh = core.find_gh()
    if not gh:
        blockers += 1
        p.mark(
            FAIL,
            "GitHub CLI (gh) not found",
            "Install it: brew install gh  (or see https://cli.github.com)",
        )
    else:
        version = gh_version(gh)
        if version is None:
            p.mark(WARN, "gh found at {}, but its version could not be read".format(gh))
        elif version[:2] < MIN_GH:
            blockers += 1
            p.mark(
                FAIL,
                "gh {}.{}.{} is too old (switching needs >= {}.{})".format(*(version + MIN_GH)),
                "Upgrade gh: brew upgrade gh  (or see https://cli.github.com)",
            )
        else:
            p.mark(OK, "gh {}.{}.{} ({})".format(*(version + (gh,))))

    cfg = core.load_config()
    if cfg.error:
        p.mark(WARN, "Config problem: {}".format(cfg.error), "Fix it with: gh-control open-config")
    elif os.path.exists(cfg.path):
        p.mark(OK, "Config OK: {}".format(cfg.path))
    else:
        p.mark(OK, "No config file (optional); create one with: gh-control open-config")

    if gh:
        try:
            accounts = core.list_accounts(cfg)
        except core.GhControlError as exc:
            accounts = []
            p.mark(WARN, "Could not list accounts: {}".format(exc))
        active = next((a.login for a in accounts if a.active), None)
        logins = ", ".join(a.login + (" (active)" if a.active else "") for a in accounts)
        if not accounts:
            blockers += 1
            p.mark(FAIL, "No accounts logged in on {}".format(cfg.host), "Run: gh auth login")
        elif len(accounts) < 2:
            p.mark(
                WARN,
                "Only one account logged in: {}".format(logins),
                "Add your other account with: gh auth login",
            )
        else:
            p.mark(OK, "{} accounts on {}: {}".format(len(accounts), cfg.host, logins))
        if accounts and not active:
            p.mark(WARN, "No active account", "Pick one with: gh-control switch <login>")
        if accounts:
            _doctor_git_identity(p, cfg, accounts)

    plugins = installed_plugins(platform)
    broken = [path for path in plugins if not os.path.exists(path)]
    if broken:
        p.mark(WARN, "Broken plugin link: {}".format(", ".join(broken)), "Run: gh-control install")
    elif plugins:
        p.mark(OK, "Plugin installed: {}".format(", ".join(plugins)))
    else:
        p.mark(WARN, "Top-bar plugin not installed", "Run: gh-control install")

    if mac:
        apps = [name for name in ("SwiftBar", "xbar") if _mac_app(name)]
        if apps:
            p.mark(OK, "Menu bar app: {}".format(", ".join(apps)))
        else:
            p.mark(WARN, "SwiftBar / xbar not found", "Install one: brew install --cask swiftbar")
    else:
        if argos_extension_installed():
            p.mark(OK, "Argos GNOME extension found")
        elif tray_deps_available():
            p.mark(OK, "PyGObject available for the tray frontend")
        else:
            p.mark(
                WARN,
                "Neither Argos nor PyGObject (tray) found",
                "GNOME: https://extensions.gnome.org/extension/1176/argos/\n"
                "Other desktops: sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1",
            )

    notifier = "osascript" if mac else "notify-send"
    if shutil.which(notifier):
        p.mark(OK, "Notifications via {}".format(notifier))
    else:
        fix = None if mac else "Optional: sudo apt install libnotify-bin  (or your distro's libnotify)"
        p.mark(WARN, "{} not found; switch notifications are skipped".format(notifier), fix)

    cli = shutil.which("gh-control")
    if cli:
        p.mark(OK, "gh-control on PATH: {}".format(cli))
    else:
        p.mark(
            WARN,
            "gh-control is not on your PATH",
            'Add ~/.local/bin to PATH, e.g.: echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.profile',
        )

    p.line()
    if blockers:
        p.line("{} {} problem(s) must be fixed before gh-control can work.".format(FAIL, blockers))
        return 1
    p.line("{} Ready to go.".format(OK))
    return 0
