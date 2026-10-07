# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Automatic git identity for every account: the GitHub name (or login) and
  the `ID+login@users.noreply.github.com` email, fetched with
  `gh api users/<login>` and cached in `identities.json`. Config
  `git_name` / `git_email` still override each field.
- `gh-control identity show|sync|set|apply [--local]` to view, fetch, save
  and apply identities, globally or for a single repository.
- `auto_git_identity` config key (default `true`).
- Menu and tray show the active account's git identity, with one-click
  fixes when it's missing or git's global email belongs to another account.
- `gh-control doctor` checks for missing identities and a mismatched global
  git email (offline, warnings only).
- `gh-control update [--check] [--dry-run]`: self-update to the latest
  release, using the same method gh-control was installed with (pipx, pip,
  plain copy or Homebrew; a git checkout gets a `git pull` hint). The
  release is looked up with `gh api repos/<repo>/releases/latest`.
- Menu and tray rows **Update now** (when a newer release is known, with a
  release-notes link) and **Check for updates**.
- A daily background release check started by the menu (detached, never
  blocking; off with `GH_CONTROL_NO_UPDATE_CHECK=1`).
- `check_updates` config key (default `true`).
- `gh-control doctor` reports when a newer release is available (from the
  cached check, no network).

### Changed

- Switching now writes a git identity even when the account has no
  `git_name` / `git_email` in the config. Set `"auto_git_identity": false`
  to restore the old behaviour.

## [0.1.0] - 2026-10-05

### Added

- Core CLI `gh-control` with `list`, `current`, `switch`, `toggle`, `menu`
  and `open-config`. Python standard library only.
- Fast, offline account discovery from gh's `hosts.yml` (honours
  `GH_CONFIG_DIR` / `XDG_CONFIG_HOME`), falling back to `gh auth status`
  (JSON or text).
- Optional config `~/.config/gh-control/config.json` with per-account
  `label`, `icon`, `color`, `sfimage`, `git_name` and `git_email`, plus
  `host`, `notify` and `set_git_identity`.
- Optional global git identity switching and desktop notifications on switch.
- Top-bar frontends: SwiftBar/xbar plugin (macOS), Argos plugin (GNOME) and
  AppIndicator tray (KDE, XFCE and others).
- `gh-control install`, `uninstall` and `doctor`: idempotent plugin setup
  without root, and a setup checker with suggested fixes.
- `install.sh` one-line installer (pipx, pip or plain copy) with
  `--dry-run`, `--uninstall`, `--ref`, `--method` and `--frontend`.
- `pyproject.toml` for pip/pipx and a Homebrew formula template.
- Public README, license, contributing guide, code of conduct, security
  policy, CI, and issue and pull request templates.

[Unreleased]: https://github.com/abdulahwahdi/gh-control/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/abdulahwahdi/gh-control/releases/tag/v0.1.0
