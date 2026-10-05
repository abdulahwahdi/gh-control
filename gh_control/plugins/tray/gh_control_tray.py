#!/usr/bin/env python3
"""System tray (AppIndicator) frontend for gh-control on Linux.

Works on desktops with AppIndicator / StatusNotifier support (KDE, XFCE,
Cinnamon, MATE, GNOME with the AppIndicator extension, ...).

Needs PyGObject and an AppIndicator typelib, e.g. on Debian/Ubuntu:
    sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1
On Fedora:
    sudo dnf install python3-gobject libayatana-appindicator-gtk3
"""

import os
import sys

# <root>/gh_control/plugins/tray/<this file>
root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
if os.path.isdir(os.path.join(root, "gh_control")):
    sys.path.insert(0, root)

from gh_control import core, menu  # noqa: E402

REFRESH_SECONDS = 30
INSTALL_HINT = (
    "gh-control tray needs PyGObject and AppIndicator.\n"
    "  Debian/Ubuntu: sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1\n"
    "  Fedora:        sudo dnf install python3-gobject libayatana-appindicator-gtk3\n"
    "  Arch:          sudo pacman -S python-gobject libayatana-appindicator\n"
    "On GNOME you can use the Argos plugin instead: gh-control install --frontend argos"
)


def _load_gi():
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib, Gtk
    except (ImportError, ValueError) as exc:
        raise RuntimeError("{}\n({})".format(INSTALL_HINT, exc))
    for name in ("AyatanaAppIndicator3", "AppIndicator3"):
        try:
            gi.require_version(name, "0.1")
            module = __import__("gi.repository", fromlist=[name])
            return GLib, Gtk, getattr(module, name)
        except (ImportError, ValueError, AttributeError):
            continue
    raise RuntimeError(INSTALL_HINT)


class Tray:
    def __init__(self, GLib, Gtk, AppIndicator):
        self.GLib, self.Gtk, self.AppIndicator = GLib, Gtk, AppIndicator
        self.indicator = AppIndicator.Indicator.new(
            "gh-control",
            "avatar-default-symbolic",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.refresh()
        GLib.timeout_add_seconds(REFRESH_SECONDS, self._tick)

    def _tick(self):
        self.refresh()
        return True

    def _add(self, gtk_menu, text, callback=None, sensitive=True):
        item = self.Gtk.MenuItem(label=text)
        item.set_sensitive(sensitive and callback is not None)
        if callback is not None:
            item.connect("activate", lambda _item: callback())
        gtk_menu.append(item)
        return item

    def _switch(self, login):
        try:
            core.switch(login)
        except core.GhControlError as exc:
            core.notify("gh-control", str(exc))
        self.refresh()

    def _open(self, target):
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        try:
            import subprocess

            subprocess.Popen([opener, target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def _open_config(self):
        from gh_control import cli

        cli.main(["open-config"])

    def refresh(self):
        Gtk = self.Gtk
        state = menu.build_state()
        gtk_menu = Gtk.Menu()
        if state["error"]:
            self.indicator.set_label("⚠ gh", "")
            self._add(gtk_menu, state["error"], sensitive=False)
            if state["error_kind"] == "gh-missing":
                self._add(gtk_menu, "Install GitHub CLI…", lambda: self._open("https://cli.github.com"))
        else:
            active = next((a for a in state["accounts"] if a["active"]), None)
            title = "{} {}".format(active["icon"], active["login"]) if active else "○ gh"
            self.indicator.set_label(title, "")
            self._add(gtk_menu, "GitHub accounts on {}".format(state["host"]), sensitive=False)
            for account in state["accounts"]:
                name = account["login"]
                if account["label"] != account["login"]:
                    name = "{} — {}".format(account["label"], account["login"])
                item = Gtk.CheckMenuItem(label="{} {}".format(account["icon"], name))
                item.set_active(account["active"])
                item.set_draw_as_radio(True)
                login = account["login"]
                item.connect(
                    "activate",
                    lambda _item, login=login, was_active=account["active"]: (
                        None if was_active else self._switch(login)
                    ),
                )
                gtk_menu.append(item)
            if state["config_error"]:
                self._add(gtk_menu, "⚠ " + state["config_error"], sensitive=False)
            gtk_menu.append(Gtk.SeparatorMenuItem())
            if active:
                url = "https://{}/{}".format(state["host"], active["login"])
                self._add(gtk_menu, "Open {}/{}".format(state["host"], active["login"]), lambda: self._open(url))
        gtk_menu.append(Gtk.SeparatorMenuItem())
        self._add(gtk_menu, "Refresh", self.refresh)
        self._add(gtk_menu, "Open config", self._open_config)
        self._add(gtk_menu, "Quit", Gtk.main_quit)
        gtk_menu.show_all()
        self.indicator.set_menu(gtk_menu)
        self._menu = gtk_menu  # keep a reference alive


def main():
    try:
        GLib, Gtk, AppIndicator = _load_gi()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    Tray(GLib, Gtk, AppIndicator)
    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
