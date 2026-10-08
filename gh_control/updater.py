"""Check for and install new gh-control releases.

The latest release is looked up with `gh api repos/<repo>/releases/latest`,
so gh-control never makes HTTP requests of its own and never handles tokens.
The result is cached next to the config file. Upgrading uses whichever
method installed gh-control: pipx, pip, Homebrew, or the plain copy made by
install.sh (replaced from the release tarball, also fetched through gh). A
git checkout is never modified; only a `git pull` hint is printed.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from typing import List, Optional, TextIO, Tuple

from gh_control import __version__, core

DEFAULT_REPO = "abdulahwahdi/gh-control"
RELEASE_HOST = "github.com"
CHECK_INTERVAL = 24 * 3600
METHODS = ("homebrew", "pipx", "pip", "source", "checkout", "unknown")

PACKAGE_DIR = os.path.dirname(os.path.realpath(__file__))
ROOT_DIR = os.path.dirname(PACKAGE_DIR)

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")


def repo() -> str:
    return os.environ.get("GH_CONTROL_REPO") or DEFAULT_REPO


def cache_path() -> str:
    return os.path.join(os.path.dirname(core.config_path()), "update-check.json")


def tarball_url(tag: str) -> str:
    return "https://github.com/{}/archive/refs/tags/{}.tar.gz".format(repo(), tag)


# --------------------------------------------------------------------------
# Versions


def parse_version(text: Optional[str]) -> Optional[Tuple[int, ...]]:
    """`v1.2.3` / `1.2` -> (1, 2, 3) / (1, 2, 0); None for anything else."""
    if not isinstance(text, str):
        return None
    m = _VERSION_RE.match(text.strip()[1:] if text.strip().startswith("v") else text.strip())
    if not m:
        return None
    return tuple(int(part or 0) for part in m.groups())


def is_newer(tag: Optional[str], current: str = __version__) -> bool:
    latest, running = parse_version(tag), parse_version(current)
    if latest is None or running is None:
        return False
    return latest > running


@dataclass
class ReleaseInfo:
    tag: Optional[str]
    url: Optional[str]
    checked_at: int
    error: Optional[str] = None

    @property
    def newer(self) -> bool:
        return bool(self.tag) and is_newer(self.tag)

    def to_dict(self) -> dict:
        return {
            "checked_at": self.checked_at,
            "latest": self.tag,
            "url": self.url,
            "error": self.error,
        }


# --------------------------------------------------------------------------
# Release check and cache


def fetch_latest_release() -> ReleaseInfo:
    proc = core._run_gh(
        ["api", "repos/{}/releases/latest".format(repo()), "--hostname", RELEASE_HOST]
    )
    now = int(time.time())
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if "404" in err or "Not Found" in err:
            return ReleaseInfo(tag=None, url=None, checked_at=now)
        raise core.GhControlError("Cannot check for updates: {}".format(err or "gh api failed"))
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        raise core.GhControlError("Cannot check for updates: invalid response from gh api")
    if not isinstance(data, dict):
        raise core.GhControlError("Cannot check for updates: invalid response from gh api")
    if data.get("draft") or data.get("prerelease"):
        raise core.GhControlError("Cannot check for updates: latest release is not final")
    tag = data.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise core.GhControlError("Cannot check for updates: release has no tag")
    url = data.get("html_url")
    return ReleaseInfo(tag=tag, url=url if isinstance(url, str) else None, checked_at=now)


def load_cached() -> Optional[ReleaseInfo]:
    """The last check result, or None. Never raises."""
    try:
        with open(cache_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("checked_at"), int):
        return None
    tag, url, error = data.get("latest"), data.get("url"), data.get("error")
    return ReleaseInfo(
        tag=tag if isinstance(tag, str) else None,
        url=url if isinstance(url, str) else None,
        checked_at=data["checked_at"],
        error=error if isinstance(error, str) else None,
    )


def save_cached(info: ReleaseInfo) -> None:
    path = cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(info.to_dict(), fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError:
        pass


def cache_is_stale() -> bool:
    cached = load_cached()
    return cached is None or time.time() - cached.checked_at >= CHECK_INTERVAL


def check(force: bool = True) -> ReleaseInfo:
    """Return the latest release, from the cache unless `force` or stale."""
    cached = load_cached()
    if not force and cached is not None and time.time() - cached.checked_at < CHECK_INTERVAL:
        return cached
    try:
        info = fetch_latest_release()
    except core.GhControlError as exc:
        # Record the failure so the background check waits a full interval.
        save_cached(ReleaseInfo(
            tag=cached.tag if cached else None,
            url=cached.url if cached else None,
            checked_at=int(time.time()),
            error=str(exc),
        ))
        raise
    save_cached(info)
    return info


# --------------------------------------------------------------------------
# Install method


def _data_home() -> str:
    # Same as installer._data_home(); duplicated to keep this module standalone.
    return os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )


def install_method() -> str:
    override = os.environ.get("GH_CONTROL_INSTALL_METHOD")
    if override in METHODS:
        return override
    if os.path.isdir(os.path.join(ROOT_DIR, ".git")) or os.path.isfile(
        os.path.join(ROOT_DIR, ".git")
    ):
        return "checkout"
    if "/Cellar/gh-control/" in PACKAGE_DIR:
        return "homebrew"
    if ROOT_DIR == os.path.realpath(os.path.join(_data_home(), "gh-control")):
        return "source"
    prefix = os.path.realpath(sys.prefix)
    if os.sep + "pipx" + os.sep in prefix and "venvs" in prefix:
        return "pipx"
    if "site-packages" in PACKAGE_DIR or "dist-packages" in PACKAGE_DIR:
        return "pip"
    return "unknown"


def upgrade_command(method: str, tag: str) -> Optional[List[str]]:
    if method == "pipx":
        pipx = shutil.which("pipx")
        if not pipx:
            raise core.GhControlError("pipx not found; reinstall with the one-line installer")
        return [pipx, "install", "--force", tarball_url(tag)]
    if method == "pip":
        user = ["--user"] if sys.prefix == getattr(sys, "base_prefix", sys.prefix) else []
        return [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet"] + user + [
            tarball_url(tag)
        ]
    if method == "homebrew":
        brew = shutil.which("brew")
        if not brew:
            raise core.GhControlError("brew not found; run: brew upgrade gh-control")
        return [brew, "upgrade", "gh-control"]
    return None


# --------------------------------------------------------------------------
# Replacing a plain copy (install.sh without pip/pipx)


def _safe_relpath(name: str) -> Optional[str]:
    """Member name without its top-level folder; raises on unsafe names."""
    parts = name.replace("\\", "/").split("/")
    if name.startswith("/") or ".." in parts:
        raise core.GhControlError("Refusing unsafe path in release tarball: {}".format(name))
    parts = [p for p in parts[1:] if p and p != "."]
    return os.path.join(*parts) if parts else None


def _extract(archive: str, target: str) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            _safe_relpath(member.name)
        for member in members:
            rel = _safe_relpath(member.name)
            if rel is None:
                continue
            path = os.path.join(target, rel)
            if member.isdir():
                os.makedirs(path, exist_ok=True)
            elif member.issym():
                link = os.path.normpath(os.path.join(os.path.dirname(path), member.linkname))
                if os.path.isabs(member.linkname) or not link.startswith(target + os.sep):
                    continue
                os.makedirs(os.path.dirname(path), exist_ok=True)
                os.symlink(member.linkname, path)
            elif member.islnk():
                try:
                    source = _safe_relpath(member.linkname)
                except core.GhControlError:
                    continue
                if source is None or not os.path.isfile(os.path.join(target, source)):
                    continue
                os.makedirs(os.path.dirname(path), exist_ok=True)
                shutil.copyfile(os.path.join(target, source), path)
            elif member.isfile():
                os.makedirs(os.path.dirname(path), exist_ok=True)
                src = tar.extractfile(member)
                if src is None:
                    continue
                with src, open(path, "wb") as fh:
                    shutil.copyfileobj(src, fh)
                os.chmod(path, 0o755 if member.mode & 0o111 else 0o644)


def replace_source_install(tag: str, dest: str = ROOT_DIR) -> None:
    """Replace the copy at `dest` with the release `tag`, atomically-ish."""
    gh = core.require_gh()
    dest = os.path.abspath(dest)
    parent = os.path.dirname(dest)
    old = dest + ".old"
    try:
        tmp = tempfile.mkdtemp(prefix=".gh-control-update-", dir=parent)
    except OSError as exc:
        raise core.GhControlError("Cannot update {}: {}".format(dest, exc))
    moved = False
    try:
        archive = os.path.join(tmp, "release.tar.gz")
        with open(archive, "wb") as fh:
            try:
                proc = subprocess.run(
                    [gh, "api", "repos/{}/tarball/{}".format(repo(), tag),
                     "--hostname", RELEASE_HOST],
                    stdin=subprocess.DEVNULL,
                    stdout=fh,
                    stderr=subprocess.PIPE,
                    timeout=120,
                )
            except subprocess.TimeoutExpired:
                raise core.GhControlError("Downloading {} timed out".format(tag))
        if proc.returncode != 0:
            raise core.GhControlError("Cannot download {}: {}".format(
                tag, proc.stderr.decode("utf-8", "replace").strip() or "gh api failed"))
        new = os.path.join(tmp, "new")
        os.makedirs(new)
        try:
            _extract(archive, new)
        except (tarfile.TarError, EOFError) as exc:
            raise core.GhControlError("Invalid release tarball: {}".format(exc))
        script = os.path.join(new, "bin", "gh-control")
        if not (os.path.isfile(os.path.join(new, "gh_control", "__init__.py"))
                and os.path.isfile(script)):
            raise core.GhControlError("Release tarball {} does not contain gh-control".format(tag))
        os.chmod(script, 0o755)
        shutil.rmtree(old, ignore_errors=True)
        if os.path.exists(dest):
            os.rename(dest, old)
            moved = True
        os.rename(new, dest)
        moved = False
        shutil.rmtree(old, ignore_errors=True)
    except OSError as exc:
        if moved and not os.path.exists(dest):
            os.rename(old, dest)
        raise core.GhControlError("Cannot update {}: {}".format(dest, exc))
    except core.GhControlError:
        if moved and not os.path.exists(dest):
            os.rename(old, dest)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# `gh-control update`


def run_update(dry_run: bool = False, out: Optional[TextIO] = None) -> int:
    out = out or sys.stdout
    info = check(force=True)
    if not info.tag:
        print("No release of {} has been published yet.".format(repo()), file=out)
        return 0
    if not info.newer:
        print("gh-control {} is up to date (latest: {}).".format(__version__, info.tag), file=out)
        return 0
    tag = info.tag
    method = install_method()
    print("Updating gh-control {} -> {} ({})".format(__version__, tag, method), file=out)
    if method == "checkout":
        print("This is a git checkout; update it with: git -C {} pull".format(ROOT_DIR), file=out)
        return 1
    if method == "unknown":
        print("Cannot tell how gh-control was installed. Reinstall with:", file=out)
        print("  curl -fsSL https://raw.githubusercontent.com/{}/main/install.sh "
              "| sh -s -- --ref {}".format(repo(), tag), file=out)
        return 1
    if method == "source":
        if dry_run:
            print("[dry-run] replace {} with gh api repos/{}/tarball/{}".format(
                ROOT_DIR, repo(), tag), file=out)
            return 0
        replace_source_install(tag)
    else:
        cmd = upgrade_command(method, tag) or []
        if dry_run:
            print("[dry-run] {}".format(" ".join(cmd)), file=out)
            return 0
        out.flush()
        try:
            code = subprocess.run(cmd, stdin=subprocess.DEVNULL, timeout=600).returncode
        except subprocess.TimeoutExpired:
            raise core.GhControlError("{} timed out".format(" ".join(cmd)))
        except OSError as exc:
            raise core.GhControlError("Cannot run {}: {}".format(cmd[0], exc))
        if code != 0:
            raise core.GhControlError("{} failed (exit {})".format(" ".join(cmd), code))
    print("✓ Updated to {}. Restart the tray app if you use it.".format(tag), file=out)
    save_cached(ReleaseInfo(tag=tag, url=info.url, checked_at=int(time.time())))
    return 0
