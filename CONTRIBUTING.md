# Contributing to gh-control

Thanks for helping out! Bug reports, docs fixes and new frontends are all
welcome. By taking part you agree to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## Ground rules

- **Standard library only.** The core must run on a stock macOS or Linux
  Python 3.8+ with no `pip install`. The tray frontend is the one exception:
  it uses PyGObject, which comes from the system package manager.
- **The bar never breaks.** Menu rendering and plugins must always print a
  valid menu and exit 0, even if `gh` is missing or the config is broken.
  Errors become a `⚠ gh` menu, never a traceback.
- **Never print tokens and never touch SSH keys.**
- **Installs are idempotent and need no root.** Never overwrite a file that
  gh-control didn't create.

## Dev setup

```sh
git clone https://github.com/OWNER/gh-control.git
cd gh-control
./bin/gh-control --help       # runs straight from the checkout
./bin/gh-control doctor
```

There is nothing to install for development. To try the packaged version:

```sh
python3 -m pip install --user -e .     # or: pipx install --editable .
```

## Project layout

| Path | What lives there |
| --- | --- |
| `gh_control/core.py` | Account discovery (`hosts.yml`, `gh auth status`), config, `switch()` |
| `gh_control/menu.py` | Menu renderer for SwiftBar/xbar, Argos and JSON |
| `gh_control/cli.py` | Argument parsing; one `cmd_<name>()` per subcommand |
| `gh_control/installer.py` | `install`, `uninstall` and `doctor` |
| `gh_control/plugins/` | The frontends (`swiftbar/`, `argos/`, `tray/`) |
| `plugins/` | Symlinks to `gh_control/plugins/` for people browsing the repo |
| `bin/gh-control` | Launcher used from a checkout |
| `install.sh` | The curl one-liner installer |
| `packaging/homebrew/` | Homebrew formula template |
| `tests/` | unittest suite with a fake `gh` |

## Running the tests

```sh
python3 -m unittest discover -s tests -v
```

The tests never touch your real gh setup. `tests/helpers.py` puts a fake
`gh` on `PATH` and points `HOME`, `GH_CONFIG_DIR` and the config path at a
temporary folder. Use `GhTestCase` for new tests that need `gh`.

Also check before opening a PR:

```sh
sh -n install.sh
shellcheck install.sh gh_control/plugins/argos/gh-control.30s.sh   # if installed
python3 -m py_compile gh_control/*.py gh_control/plugins/*/*.py
```

To see the menu with fake accounts, render it against a throwaway gh config
dir (`GH_CONTROL_GH` just has to point at any executable):

```sh
mkdir -p /tmp/fake-gh
printf 'github.com:\n    users:\n        alice-corp:\n        alice:\n    user: alice-corp\n' > /tmp/fake-gh/hosts.yml
GH_CONTROL_GH=/bin/true GH_CONFIG_DIR=/tmp/fake-gh ./bin/gh-control menu --format swiftbar
```

CI runs the tests on Ubuntu and macOS with Python 3.8 and 3.12, plus the
shell checks and a `python -m build` smoke test.

## Adding a frontend

A frontend is a small adapter that turns gh-control's state into a menu
on some bar.

1. **Text-based bars** (like SwiftBar or Argos) should reuse the renderer.
   Add a format to `FORMATS` in `gh_control/menu.py` and handle it in
   `_Writer`. Then add a plugin script in `gh_control/plugins/<name>/` that
   calls `menu.safe_render("<name>")` and always exits 0.
2. **GUI toolkits** (like the AppIndicator tray) should call
   `menu.build_state()` (it never raises) and `core.switch()`. See
   `gh_control/plugins/tray/gh_control_tray.py`.
3. Teach the installer about it in `gh_control/installer.py`: add it to
   `FRONTENDS`, `PLUGIN_FILES` and the per-OS lists, plus detection,
   install location and next steps. Then add the CLI `--frontend` choice in
   `gh_control/cli.py`.
4. Add a symlink under `plugins/<name>/` and make the plugin executable.
5. Add tests (rendering, plus install/uninstall in a temp `HOME`) and a
   section in the README's *Platform setup*.

## Pull requests

- Keep PRs focused. Add or update tests for behaviour changes.
- Update `README.md` when you change commands, flags or config keys, and
  add a line under **Unreleased** in `CHANGELOG.md`.
- Describe how you tested, especially for frontends, since CI can't
  check real menu bars and trays.

## Releasing (maintainers)

1. Bump `__version__` in `gh_control/__init__.py` and the `xbar.version`
   header in the SwiftBar plugin.
2. Move **Unreleased** in `CHANGELOG.md` to the new version.
3. Tag `vX.Y.Z` and push the tag.
4. Update `url` and `sha256` in the Homebrew tap
   (see `packaging/homebrew/README.md`).
