"""Core logic: discover GitHub CLI accounts, read config, switch accounts.

Only the Python standard library is used so this runs on stock macOS and
Linux. Tokens are never read into output and SSH keys are never touched.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

DEFAULT_HOST = "github.com"
DEFAULT_ICON = "●"
GH_TIMEOUT = 15

# Extra places to look for `gh` when the caller's PATH is minimal (menu bar
# apps on macOS usually do not inherit the login shell PATH).
# Overridable with GH_CONTROL_SEARCH_PATH (os.pathsep separated).
FALLBACK_GH_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/home/linuxbrew/.linuxbrew/bin",
    "~/.local/bin",
    "/usr/bin",
    "/snap/bin",
)


class GhControlError(Exception):
    """Base error with a message that is safe to show to the user."""


class GhNotFoundError(GhControlError):
    pass


class NotLoggedInError(GhControlError):
    pass


class UnknownAccountError(GhControlError):
    pass


class SwitchError(GhControlError):
    pass


@dataclass
class Account:
    login: str
    active: bool = False
    label: str = ""
    icon: str = DEFAULT_ICON
    color: Optional[str] = None
    sfimage: Optional[str] = None
    git_name: Optional[str] = None
    git_email: Optional[str] = None
    # Where the identity came from: "config", "github", "mixed" or None.
    git_source: Optional[str] = None
    # Set by switch() when the identity could not be fetched; not serialised.
    git_warning: Optional[str] = None

    def __post_init__(self):
        if not self.label:
            self.label = self.login

    def to_dict(self) -> dict:
        return {
            "login": self.login,
            "active": self.active,
            "label": self.label,
            "icon": self.icon,
            "color": self.color,
            "sfimage": self.sfimage,
            "git_name": self.git_name,
            "git_email": self.git_email,
            "git_source": self.git_source,
        }

    def has_git_identity(self) -> bool:
        return bool(self.git_name and self.git_email)


@dataclass
class Config:
    host: str = DEFAULT_HOST
    accounts: Dict[str, dict] = field(default_factory=dict)
    notify: bool = True
    set_git_identity: bool = True
    auto_git_identity: bool = True
    path: str = ""
    error: Optional[str] = None


# --------------------------------------------------------------------------
# Paths


def _xdg_config_home() -> str:
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )


def gh_config_dir() -> str:
    return os.environ.get("GH_CONFIG_DIR") or os.path.join(_xdg_config_home(), "gh")


def hosts_file() -> str:
    return os.path.join(gh_config_dir(), "hosts.yml")


def config_path() -> str:
    return os.environ.get("GH_CONTROL_CONFIG") or os.path.join(
        _xdg_config_home(), "gh-control", "config.json"
    )


def find_gh() -> Optional[str]:
    """Return the path of the `gh` executable, or None."""
    override = os.environ.get("GH_CONTROL_GH")
    if override:
        return override if os.access(override, os.X_OK) else None
    found = shutil.which("gh")
    if found:
        return found
    search = os.environ.get("GH_CONTROL_SEARCH_PATH")
    dirs = search.split(os.pathsep) if search is not None else FALLBACK_GH_DIRS
    for d in dirs:
        if not d:
            continue
        candidate = os.path.join(os.path.expanduser(d), "gh")
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def require_gh() -> str:
    gh = find_gh()
    if not gh:
        raise GhNotFoundError(
            "GitHub CLI (gh) not found. Install it from https://cli.github.com"
        )
    return gh


# --------------------------------------------------------------------------
# Config


def load_config() -> Config:
    """Load the optional config file. Never raises; problems go to .error."""
    path = config_path()
    cfg = Config(path=path)
    if not os.path.exists(path):
        return cfg
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        cfg.error = "Cannot read {}: {}".format(path, exc)
        return cfg
    if not isinstance(data, dict):
        cfg.error = "{} must contain a JSON object".format(path)
        return cfg
    host = data.get("host")
    if isinstance(host, str) and host.strip():
        cfg.host = host.strip()
    accounts = data.get("accounts")
    if isinstance(accounts, dict):
        cfg.accounts = {
            str(k): (v if isinstance(v, dict) else {}) for k, v in accounts.items()
        }
    if isinstance(data.get("notify"), bool):
        cfg.notify = data["notify"]
    if isinstance(data.get("set_git_identity"), bool):
        cfg.set_git_identity = data["set_git_identity"]
    if isinstance(data.get("auto_git_identity"), bool):
        cfg.auto_git_identity = data["auto_git_identity"]
    return cfg


def _str_or_none(value) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _fill_identity(account: Account, cfg: Config, cached: Optional[dict]) -> None:
    """Set git_name/git_email from config, then `cached`, and git_source."""
    extra = cfg.accounts.get(account.login, {})
    sources = set()
    for attr, cache_key in (("git_name", "name"), ("git_email", "email")):
        value = _str_or_none(extra.get(attr))
        if value:
            sources.add("config")
        elif cached:
            value = _str_or_none(cached.get(cache_key))
            if value:
                sources.add("github")
        setattr(account, attr, value)
    if len(sources) > 1:
        account.git_source = "mixed"
    else:
        account.git_source = sources.pop() if sources else None


def _decorate(account: Account, cfg: Config, cache: Optional[dict] = None) -> Account:
    extra = cfg.accounts.get(account.login, {})
    account.label = _str_or_none(extra.get("label")) or account.login
    account.icon = _str_or_none(extra.get("icon")) or DEFAULT_ICON
    account.color = _str_or_none(extra.get("color"))
    account.sfimage = _str_or_none(extra.get("sfimage"))
    cached = None
    if cfg.auto_git_identity and cache:
        host_cache = cache.get(cfg.host)
        if isinstance(host_cache, dict) and isinstance(host_cache.get(account.login), dict):
            cached = host_cache[account.login]
    _fill_identity(account, cfg, cached)
    return account


# --------------------------------------------------------------------------
# hosts.yml


_KEY_RE = re.compile(r"""^("[^"]*"|'[^']*'|[^:#][^:]*?)\s*:(?:\s+(.*))?$""")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_simple_yaml(text: str) -> dict:
    """Parse the small subset of YAML used by gh's hosts.yml.

    Supports nested mappings by indentation, scalar values, quoted strings,
    `{}` and comments. Lists and multi-line scalars are ignored.
    """
    root: dict = {}
    stack = [(-1, root)]
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        match = _KEY_RE.match(stripped)
        if not match:
            continue
        key = _unquote(match.group(1).strip())
        value = (match.group(2) or "").strip()
        if value and value[0] not in "\"'":
            value = re.sub(r"\s+#.*$", "", value)
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if value == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        elif value == "{}":
            parent[key] = {}
        elif value in ("~", "null"):
            parent[key] = None
        else:
            parent[key] = _unquote(value)
    return root


def accounts_from_hosts_file(host: str) -> Optional[List[Account]]:
    """Read accounts from hosts.yml. Returns None if not usable."""
    try:
        with open(hosts_file(), encoding="utf-8") as fh:
            data = parse_simple_yaml(fh.read())
    except OSError:
        return None
    entry = data.get(host)
    if not isinstance(entry, dict):
        return None
    active = entry.get("user") if isinstance(entry.get("user"), str) else None
    users = entry.get("users")
    logins: List[str] = []
    if isinstance(users, dict):
        logins = [str(u) for u in users]
    elif active:
        # Older single-account layout without a users map.
        logins = [active]
    if not logins:
        return None
    return [Account(login=u, active=(u == active)) for u in logins]


# --------------------------------------------------------------------------
# gh auth status fallback


def _run_gh(args: List[str]) -> subprocess.CompletedProcess:
    gh = require_gh()
    try:
        return subprocess.run(
            [gh] + args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=GH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise GhControlError("gh {} timed out".format(" ".join(args[:2])))
    except OSError as exc:
        raise GhNotFoundError("Cannot run gh: {}".format(exc))


_STATUS_ACCOUNT_RE = re.compile(
    r"(?:Logged in to|Failed to log in to)\s+(\S+)\s+(?:account|as)\s+(\S+)"
)
_STATUS_ACTIVE_RE = re.compile(r"Active account:\s*(true|false)")


def parse_auth_status_text(text: str, host: str) -> List[Account]:
    accounts: List[Account] = []
    current: Optional[Account] = None
    saw_active_flag = False
    for line in text.splitlines():
        m = _STATUS_ACCOUNT_RE.search(line)
        if m:
            current = None
            if m.group(1) == host:
                current = Account(login=m.group(2))
                accounts.append(current)
            continue
        m = _STATUS_ACTIVE_RE.search(line)
        if m and current is not None:
            saw_active_flag = True
            current.active = m.group(1) == "true"
    if accounts and not saw_active_flag:
        # Old gh versions only support one account per host.
        accounts[0].active = True
    return accounts


def parse_auth_status_json(text: str, host: str) -> Optional[List[Account]]:
    try:
        data = json.loads(text)
    except ValueError:
        return None
    entries = (data.get("hosts") or {}).get(host) if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    accounts = []
    for item in entries:
        if isinstance(item, dict) and item.get("login"):
            accounts.append(
                Account(login=str(item["login"]), active=bool(item.get("active")))
            )
    return accounts


def accounts_from_gh(host: str) -> List[Account]:
    proc = _run_gh(["auth", "status", "--hostname", host, "--json", "hosts"])
    if proc.returncode == 0:
        parsed = parse_auth_status_json(proc.stdout, host)
        if parsed is not None:
            return parsed
    proc = _run_gh(["auth", "status", "--hostname", host])
    # gh prints to stderr on some versions and exits 1 if any account fails.
    return parse_auth_status_text(proc.stdout + "\n" + proc.stderr, host)


# --------------------------------------------------------------------------
# Public API


def list_accounts(cfg: Optional[Config] = None) -> List[Account]:
    """List accounts for the configured host, active one flagged.

    Reads hosts.yml (fast, offline) and falls back to `gh auth status`.
    Returns an empty list if nobody is logged in.
    """
    cfg = cfg or load_config()
    accounts = accounts_from_hosts_file(cfg.host)
    if accounts is None:
        accounts = accounts_from_gh(cfg.host)
    # Accounts named in the config come first, in config order.
    order = {login: i for i, login in enumerate(cfg.accounts)}
    accounts.sort(key=lambda a: order.get(a.login, len(order)))
    cache = load_identity_cache() if cfg.auto_git_identity else None
    return [_decorate(a, cfg, cache) for a in accounts]


def active_account(cfg: Optional[Config] = None) -> Optional[Account]:
    for account in list_accounts(cfg):
        if account.active:
            return account
    return None


def next_account(cfg: Optional[Config] = None) -> Account:
    """Return the account after the active one (wrapping around)."""
    accounts = list_accounts(cfg)
    if not accounts:
        raise NotLoggedInError("No GitHub accounts found. Run: gh auth login")
    if len(accounts) == 1:
        raise GhControlError(
            "Only one account ({}) is logged in. Add another with: gh auth login".format(
                accounts[0].login
            )
        )
    for i, account in enumerate(accounts):
        if account.active:
            return accounts[(i + 1) % len(accounts)]
    return accounts[0]


# --------------------------------------------------------------------------
# Git identity


def identities_path() -> str:
    return os.path.join(os.path.dirname(config_path()), "identities.json")


def load_identity_cache() -> dict:
    """Read the cached GitHub identities. Never raises."""
    try:
        with open(identities_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_identity_cache(data: dict) -> None:
    path = identities_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError as exc:
        raise GhControlError("Cannot write {}: {}".format(path, exc))


def noreply_email(login: str, user_id: int, host: str) -> str:
    if host == DEFAULT_HOST:
        return "{}+{}@users.noreply.github.com".format(user_id, login)
    return "{}+{}@users.noreply.{}".format(user_id, login, host)


def fetch_github_identity(login: str, host: str) -> dict:
    """Fetch the public name and noreply email of `login` via `gh api`."""
    proc = _run_gh(["api", "users/{}".format(login), "--hostname", host])
    if proc.returncode != 0:
        raise GhControlError(
            "gh api users/{} failed: {}".format(login, proc.stderr.strip())
        )
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        raise GhControlError("gh api users/{} returned invalid JSON".format(login))
    user_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        raise GhControlError("gh api users/{} returned no user id".format(login))
    return {
        "name": _str_or_none(data.get("name")) or login,
        "email": noreply_email(login, user_id, host),
        "id": user_id,
    }


def ensure_git_identity(account: Account, cfg: Config, refresh: bool = False) -> Optional[str]:
    """Fill missing identity fields from GitHub and cache the result.

    Returns a warning message if the fetch or the cache write failed, else
    None. Values from the config always win over fetched ones.
    """
    if not cfg.auto_git_identity or (account.has_git_identity() and not refresh):
        return None
    try:
        fetched = fetch_github_identity(account.login, cfg.host)
    except GhControlError as exc:
        return str(exc)
    _fill_identity(account, cfg, fetched)
    cache = load_identity_cache()
    host_cache = cache.get(cfg.host)
    if not isinstance(host_cache, dict):
        host_cache = cache[cfg.host] = {}
    host_cache[account.login] = fetched
    try:
        save_identity_cache(cache)
    except GhControlError as exc:
        return str(exc)
    return None


def _run_git(git: str, args: List[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [git] + args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        cwd=cwd,
    )


def apply_git_identity(account: Account, scope: str = "global", cwd: Optional[str] = None) -> List[str]:
    """Set git user.name/user.email globally or for one repo. Returns what was set."""
    if scope not in ("global", "local"):
        raise GhControlError("Unknown git config scope: {}".format(scope))
    changes: List[str] = []
    git = shutil.which("git")
    if not git:
        if scope == "local":
            raise GhControlError("git not found")
        return changes
    if scope == "local":
        where = cwd or os.getcwd()
        try:
            proc = _run_git(git, ["rev-parse", "--is-inside-work-tree"], cwd=where)
            inside = proc.returncode == 0 and proc.stdout.strip() == "true"
        except OSError:
            inside = False
        if not inside:
            raise GhControlError("Not inside a git repository: {}".format(where))
    for key, value in (("user.name", account.git_name), ("user.email", account.git_email)):
        if not value:
            continue
        proc = _run_git(git, ["config", "--" + scope, key, value], cwd=cwd)
        if proc.returncode != 0:
            message = "git config --{} {} failed: {}".format(scope, key, proc.stderr.strip())
            if scope == "global":
                raise SwitchError("Switched gh account, but " + message)
            raise GhControlError(message)
        changes.append("{}={}".format(key, value))
    return changes


def read_git_identity(scope: Optional[str] = None, cwd: Optional[str] = None) -> dict:
    """Return {"name", "email"} as git sees them (None when unset). Never raises.

    `scope` is "global", "local" or None for the effective value in `cwd`.
    """
    result: Dict[str, Optional[str]] = {"name": None, "email": None}
    git = shutil.which("git")
    if not git:
        return result
    flags = ["--" + scope] if scope else []
    for name, key in (("name", "user.name"), ("email", "user.email")):
        try:
            proc = _run_git(git, ["config"] + flags + ["--get", key], cwd=cwd)
        except OSError:
            continue
        if proc.returncode == 0:
            result[name] = _str_or_none(proc.stdout)
    return result


def notify(title: str, message: str) -> None:
    """Best-effort desktop notification; silently skipped if unavailable."""
    try:
        if sys.platform == "darwin" and shutil.which("osascript"):
            def esc(s: str) -> str:
                return s.replace("\\", "\\\\").replace('"', '\\"')

            script = 'display notification "{}" with title "{}"'.format(
                esc(message), esc(title)
            )
            cmd = ["osascript", "-e", script]
        elif shutil.which("notify-send"):
            cmd = ["notify-send", "--app-name=gh-control", title, message]
        else:
            return
        subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def switch(user: str, cfg: Optional[Config] = None, notify_user: Optional[bool] = None) -> Account:
    """Make `user` the active gh account and apply optional git identity."""
    cfg = cfg or load_config()
    gh = require_gh()
    accounts = list_accounts(cfg)
    if not accounts:
        raise NotLoggedInError("No GitHub accounts found. Run: gh auth login")
    target = next((a for a in accounts if a.login == user), None)
    if target is None:
        raise UnknownAccountError(
            "Unknown account '{}' on {}. Known accounts: {}".format(
                user, cfg.host, ", ".join(a.login for a in accounts)
            )
        )
    if not target.active:
        proc = subprocess.run(
            [gh, "auth", "switch", "--hostname", cfg.host, "--user", user],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=GH_TIMEOUT,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            hint = ""
            if "unknown command" in detail.lower():
                hint = " (gh >= 2.40 is required for account switching)"
            raise SwitchError("gh auth switch failed{}: {}".format(hint, detail))
    if cfg.set_git_identity:
        # Runs after the switch; users/<login> is public, so any token works.
        target.git_warning = ensure_git_identity(target, cfg)
        apply_git_identity(target)
    target.active = True
    for account in accounts:
        if account is not target:
            account.active = False
    if notify_user is None:
        notify_user = cfg.notify and not os.environ.get("GH_CONTROL_NO_NOTIFY")
    if notify_user:
        notify("GitHub account", "Switched to {} {} ({})".format(target.icon, target.label, target.login))
    return target
