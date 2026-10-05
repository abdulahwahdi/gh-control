"""Render the top-bar menu for SwiftBar/xbar, Argos, or as JSON.

Rendering never raises: any problem becomes a "⚠ gh" warning menu so the
bar always shows something useful.
"""

import json
import os
import sys
from typing import List, Optional

from gh_control import __version__, core

WARN_COLOR = "#d29922"
FORMATS = ("swiftbar", "argos", "json")

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def command_args() -> List[str]:
    """Absolute command used by menu actions to call back into gh-control."""
    launcher = os.path.join(_REPO_ROOT, "bin", "gh-control")
    if os.path.isfile(launcher) and os.access(launcher, os.X_OK):
        return [os.path.realpath(launcher)]
    return [os.path.realpath(sys.executable), "-m", "gh_control"]


def build_state(cfg: Optional[core.Config] = None) -> dict:
    """Collect everything the menu needs; errors are captured, not raised."""
    state = {
        "version": __version__,
        "host": core.DEFAULT_HOST,
        "active": None,
        "accounts": [],
        "error": None,
        "error_kind": None,
        "config_path": core.config_path(),
        "config_error": None,
    }
    try:
        cfg = cfg or core.load_config()
        state["host"] = cfg.host
        state["config_error"] = cfg.error
        core.require_gh()
        accounts = core.list_accounts(cfg)
        if not accounts:
            raise core.NotLoggedInError(
                "No accounts logged in on {}. Run: gh auth login".format(cfg.host)
            )
        state["accounts"] = [a.to_dict() for a in accounts]
        active = next((a for a in accounts if a.active), None)
        state["active"] = active.login if active else None
    except core.GhNotFoundError as exc:
        state["error"], state["error_kind"] = str(exc), "gh-missing"
    except core.NotLoggedInError as exc:
        state["error"], state["error_kind"] = str(exc), "not-logged-in"
    except Exception as exc:  # the bar must never show a traceback
        state["error"], state["error_kind"] = (str(exc) or exc.__class__.__name__), "error"
    return state


# --------------------------------------------------------------------------
# Line formatting


def _clean(text: str) -> str:
    return str(text).replace("|", "¦").replace("\n", " ")


def _swiftbar_value(value: str) -> str:
    value = str(value)
    if value and not any(c in value for c in ' "\'\t'):
        return value
    return '"{}"'.format(value.replace('"', '\\"'))


def _shell_word(value: str) -> str:
    """Double-quote a word for Argos' bash= (which is wrapped in single quotes)."""
    value = str(value)
    if value and all(c.isalnum() or c in "/._-+=:@," for c in value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return '"{}"'.format(escaped.replace("'", ""))


class _Writer:
    def __init__(self, fmt: str):
        self.fmt = fmt
        self.lines: List[str] = []

    def text(self, text: str) -> str:
        text = _clean(text)
        if self.fmt == "argos":
            text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return text

    def item(self, text: str, run: Optional[List[str]] = None, **params) -> None:
        attrs = []
        if run:
            if self.fmt == "argos":
                attrs.append("bash='{}'".format(" ".join(_shell_word(w) for w in run)))
            else:
                attrs.append("bash=" + _swiftbar_value(run[0]))
                for i, arg in enumerate(run[1:], start=1):
                    attrs.append("param{}={}".format(i, _swiftbar_value(arg)))
            params.setdefault("terminal", "false")
        for key, value in params.items():
            if value is None:
                continue
            if self.fmt == "argos" and key == "sfimage":
                continue
            if isinstance(value, bool):
                value = "true" if value else "false"
            attrs.append("{}={}".format(key, _swiftbar_value(value)))
        line = self.text(text)
        if attrs:
            line += " | " + " ".join(attrs)
        self.lines.append(line)

    def separator(self) -> None:
        self.lines.append("---")


def _render_error(w: _Writer, state: dict, cmd: List[str]) -> None:
    w.item("⚠ gh", color=WARN_COLOR, sfimage="exclamationmark.triangle")
    w.separator()
    w.item(state["error"] or "Unknown error", color=WARN_COLOR)
    kind = state.get("error_kind")
    if kind == "gh-missing":
        w.item("Install GitHub CLI…", href="https://cli.github.com")
    elif kind == "not-logged-in":
        gh = core.find_gh()
        if gh:
            w.item("Log in with gh auth login…", run=[gh, "auth", "login"], terminal="true")
    w.separator()
    w.item("Refresh", refresh=True)
    w.item("Open config", run=cmd + ["open-config"])


def render(fmt: str, state: Optional[dict] = None) -> str:
    if fmt not in FORMATS:
        raise ValueError("Unknown menu format: {}".format(fmt))
    state = state if state is not None else build_state()
    if fmt == "json":
        return json.dumps(state, indent=2, ensure_ascii=False)

    cmd = command_args()
    w = _Writer(fmt)
    if state.get("error"):
        _render_error(w, state, cmd)
        return "\n".join(w.lines)

    accounts = state["accounts"]
    active = next((a for a in accounts if a["active"]), None)
    if active:
        w.item(
            "{} {}".format(active["icon"], active["login"]),
            color=active.get("color"),
            sfimage=active.get("sfimage"),
        )
    else:
        w.item("○ gh: no active account", color=WARN_COLOR)
    w.separator()
    w.item("GitHub accounts on {}".format(state["host"]), size=12)
    for account in accounts:
        name = account["login"]
        if account["label"] != account["login"]:
            name = "{} — {}".format(account["label"], account["login"])
        mark = "✓" if account["active"] else "    "
        w.item(
            "{} {} {}".format(mark, account["icon"], name),
            run=cmd + ["switch", account["login"]],
            refresh=True,
            color=account.get("color") if account["active"] else None,
        )
    if len(accounts) > 1:
        w.item("Toggle to next account", run=cmd + ["toggle"], refresh=True)
    if state.get("config_error"):
        w.item("⚠ " + state["config_error"], color=WARN_COLOR)
    w.separator()
    w.item("Refresh", refresh=True)
    if active:
        w.item(
            "Open {}/{}".format(state["host"], active["login"]),
            href="https://{}/{}".format(state["host"], active["login"]),
        )
    w.item("Open config", run=cmd + ["open-config"])
    w.item("gh-control {}".format(state.get("version", __version__)), size=11)
    return "\n".join(w.lines)


def safe_render(fmt: str) -> str:
    """Render, falling back to a minimal warning menu on any failure."""
    try:
        return render(fmt)
    except Exception as exc:  # last line of defence for the bar
        if fmt == "json":
            return json.dumps({"error": str(exc) or exc.__class__.__name__})
        return "⚠ gh\n---\ngh-control error: {}".format(_clean(str(exc) or exc.__class__.__name__))
