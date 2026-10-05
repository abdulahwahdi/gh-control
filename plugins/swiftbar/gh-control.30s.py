#!/usr/bin/env python3
# <xbar.title>gh-control</xbar.title>
# <xbar.version>v0.1.0</xbar.version>
# <xbar.desc>Show and switch the active GitHub CLI (gh) account.</xbar.desc>
# <xbar.dependencies>python3,gh</xbar.dependencies>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>false</swiftbar.hideLastUpdated>
"""SwiftBar / xbar plugin. Symlink this file into your plugin folder."""

import os
import sys


def warn(message):
    print("⚠ gh | color=#d29922")
    print("---")
    print("{} | color=#d29922".format(message.replace("|", "¦")))
    print("Refresh | refresh=true")


try:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    if os.path.isdir(os.path.join(root, "gh_control")):
        sys.path.insert(0, root)
    from gh_control import menu
    print(menu.safe_render("swiftbar"))
except Exception as exc:  # never break the menu bar
    warn("gh-control not found or failed: {}".format(exc))
sys.exit(0)
