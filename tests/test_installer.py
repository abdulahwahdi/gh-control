import io
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
import unittest

from helpers import ROOT, SAMPLE_CONFIG, GhTestCase

from gh_control import installer

LAUNCHER = os.path.join(ROOT, "bin", "gh-control")
INSTALL_SH = os.path.join(ROOT, "install.sh")
SWIFTBAR_SRC = os.path.realpath(os.path.join(ROOT, "gh_control", "plugins", "swiftbar", "gh-control.30s.py"))
ARGOS_SRC = os.path.realpath(os.path.join(ROOT, "gh_control", "plugins", "argos", "gh-control.30s.sh"))


class InstallerTestCase(GhTestCase):
    def setUp(self):
        super().setUp()
        os.environ.pop("XDG_DATA_HOME", None)
        self.xdg = os.environ["XDG_CONFIG_HOME"]
        self.argos = os.path.join(self.xdg, "argos")
        self.autostart = os.path.join(self.xdg, "autostart", installer.AUTOSTART_FILE)
        self.swiftbar = os.path.join(self.tmp, "Library", "Application Support", "SwiftBar", "Plugins")

    def call(self, func, **kwargs):
        out = io.StringIO()
        code = func(out=out, **kwargs)
        return code, out.getvalue()

    def snapshot(self):
        result = []
        for dirpath, dirnames, filenames in os.walk(self.tmp):
            for name in dirnames + filenames:
                if name != "gh.log":  # written by the fake gh
                    result.append(os.path.join(dirpath, name))
        return sorted(result)


class LinuxInstallTest(InstallerTestCase):
    def test_argos_install_is_idempotent(self):
        code, out = self.call(installer.install, frontend="argos", platform="linux")
        self.assertEqual(code, 0, out)
        link = os.path.join(self.argos, "gh-control.30s.sh")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.path.realpath(link), ARGOS_SRC)
        self.assertIn("Next steps", out)

        code, out = self.call(installer.install, frontend="argos", platform="linux")
        self.assertEqual(code, 0, out)
        self.assertIn("already installed", out)
        self.assertEqual(os.listdir(self.argos), ["gh-control.30s.sh"])

    def test_auto_picks_argos_when_folder_exists(self):
        os.makedirs(self.argos)
        code, out = self.call(installer.install, platform="linux")
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.islink(os.path.join(self.argos, "gh-control.30s.sh")))

    def test_auto_falls_back_to_tray_autostart(self):
        if installer.argos_extension_installed():
            self.skipTest("Argos is installed system-wide on this machine")
        code, out = self.call(installer.install, platform="linux")
        self.assertEqual(code, 0, out)
        with open(self.autostart) as fh:
            entry = fh.read()
        self.assertIn(installer.AUTOSTART_MARKER, entry)
        self.assertIn("gh_control_tray.py", entry)
        code, out = self.call(installer.install, frontend="tray", platform="linux")
        self.assertIn("already installed", out)

    def test_dry_run_writes_nothing(self):
        before = self.snapshot()
        for frontend in ("argos", "tray"):
            code, out = self.call(installer.install, dry_run=True, frontend=frontend, platform="linux")
            self.assertEqual(code, 0, out)
            self.assertIn("[dry-run] would", out)
        self.assertEqual(self.snapshot(), before)

    def test_does_not_overwrite_foreign_file(self):
        dest = os.path.join(self.argos, "gh-control.30s.sh")
        self.write(dest, "#!/bin/sh\necho mine\n")
        code, out = self.call(installer.install, frontend="argos", platform="linux")
        self.assertEqual(code, 1)
        self.assertIn("not created by gh-control", out)
        with open(dest) as fh:
            self.assertEqual(fh.read(), "#!/bin/sh\necho mine\n")

        self.write(self.autostart, "[Desktop Entry]\nExec=something-else\n")
        code, out = self.call(installer.install, frontend="tray", platform="linux")
        self.assertEqual(code, 1)
        self.assertIn("not created by gh-control", out)

    def test_stale_link_is_repointed_keeping_its_name(self):
        os.makedirs(self.argos)
        stale = os.path.join(self.argos, "gh-control.1m.sh")
        os.symlink("/nonexistent/old/gh-control.30s.sh", stale)
        code, out = self.call(installer.install, frontend="argos", platform="linux")
        self.assertEqual(code, 0, out)
        self.assertEqual(os.listdir(self.argos), ["gh-control.1m.sh"])
        self.assertEqual(os.path.realpath(stale), ARGOS_SRC)

    def test_uninstall_removes_only_our_files(self):
        self.call(installer.install, frontend="argos", platform="linux")
        self.call(installer.install, frontend="tray", platform="linux")
        other = os.path.join(self.argos, "weather.1m.sh")
        self.write(other, "#!/bin/sh\n")

        code, out = self.call(installer.uninstall, dry_run=True, platform="linux")
        self.assertEqual(code, 0)
        self.assertTrue(os.path.islink(os.path.join(self.argos, "gh-control.30s.sh")))

        code, out = self.call(installer.uninstall, platform="linux")
        self.assertEqual(code, 0, out)
        self.assertEqual(os.listdir(self.argos), ["weather.1m.sh"])
        self.assertFalse(os.path.exists(self.autostart))

        code, out = self.call(installer.uninstall, platform="linux")
        self.assertEqual(code, 0)
        self.assertIn("nothing to remove", out)

    def test_mac_frontend_rejected_on_linux(self):
        code, out = self.call(installer.install, frontend="swiftbar", platform="linux")
        self.assertEqual(code, 1)
        self.assertIn("not available", out)


class MacInstallTest(InstallerTestCase):
    def test_swiftbar_default_folder(self):
        code, out = self.call(installer.install, platform="darwin")
        self.assertEqual(code, 0, out)
        link = os.path.join(self.swiftbar, "gh-control.30s.py")
        self.assertEqual(os.path.realpath(link), SWIFTBAR_SRC)

        code, out = self.call(installer.uninstall, platform="darwin")
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.lexists(link))

    def test_swiftbar_folder_from_preferences(self):
        custom = os.path.join(self.tmp, "my-plugins")
        defaults = os.path.join(self.bin, "defaults")
        self.write(defaults, "#!/bin/sh\necho '{}'\n".format(custom))
        os.chmod(defaults, 0o755)
        code, out = self.call(installer.install, platform="darwin")
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.islink(os.path.join(custom, "gh-control.30s.py")))

    def test_xbar_when_only_xbar_present(self):
        if installer._mac_app("SwiftBar"):
            self.skipTest("SwiftBar is installed on this machine")
        xbar = os.path.join(self.tmp, "Library", "Application Support", "xbar", "plugins")
        os.makedirs(xbar)
        code, out = self.call(installer.install, platform="darwin")
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.islink(os.path.join(xbar, "gh-control.30s.py")))

    def test_plugin_dir_override(self):
        target = os.path.join(self.tmp, "elsewhere")
        code, out = self.call(installer.install, frontend="xbar", target_dir=target, platform="darwin")
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.islink(os.path.join(target, "gh-control.30s.py")))
        code, out = self.call(installer.uninstall, target_dir=target, platform="darwin")
        self.assertEqual(os.listdir(target), [])

    def test_homebrew_cellar_path_prefers_opt(self):
        cellar = os.path.join(self.tmp, "brew", "Cellar", "gh-control", "0.1.0", "libexec", "x.py")
        self.write(cellar, "")
        opt = os.path.join(self.tmp, "brew", "opt", "gh-control")
        os.makedirs(os.path.dirname(opt))
        os.symlink(os.path.join("..", "Cellar", "gh-control", "0.1.0"), opt)
        self.assertEqual(installer._stable_path(cellar), os.path.join(opt, "libexec", "x.py"))
        other = os.path.join(self.tmp, "x.py")
        self.assertEqual(installer._stable_path(other), other)


class DoctorTest(InstallerTestCase):
    def test_ready(self):
        self.call(installer.install, frontend="argos", platform="linux")
        code, out = self.call(installer.doctor, platform="linux")
        self.assertEqual(code, 0, out)
        self.assertIn("✓ gh 2.62.0", out)
        self.assertIn("2 accounts on github.com", out)
        self.assertIn("Plugin installed", out)
        self.assertNotIn("gho_", out)

    def test_plugin_missing_is_a_warning_only(self):
        code, out = self.call(installer.doctor, platform="linux")
        self.assertEqual(code, 0, out)
        self.assertIn("! Top-bar plugin not installed", out)

    def test_old_gh_is_a_blocker(self):
        os.environ["FAKE_GH_VERSION"] = "2.39.1"
        code, out = self.call(installer.doctor, platform="linux")
        self.assertEqual(code, 1, out)
        self.assertIn("✗ gh 2.39.1 is too old", out)

    def test_single_account_warns(self):
        self.write(
            os.path.join(self.gh_dir, "hosts.yml"),
            "github.com:\n    users:\n        alice:\n    user: alice\n",
        )
        code, out = self.call(installer.doctor, platform="darwin")
        self.assertEqual(code, 0, out)
        self.assertIn("! Only one account", out)
        self.assertIn("gh auth login", out)

    def test_invalid_config_warns(self):
        self.write(self.config, "{not json")
        code, out = self.call(installer.doctor, platform="linux")
        self.assertIn("! Config problem", out)

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_missing_git_identity_warns_without_network(self):
        code, out = self.call(installer.doctor, platform="linux")
        self.assertEqual(code, 0, out)
        self.assertIn("! No git identity for: alice-corp, alice", out)
        self.assertIn("gh-control identity sync", out)
        self.assertNotIn(["api"], [c[:1] for c in self.gh_calls()])

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_git_identity_from_config(self):
        self.write_config(SAMPLE_CONFIG)
        self.write(self.gitconfig, "[user]\n\temail = alice@corp.com\n")
        code, out = self.call(installer.doctor, platform="linux")
        self.assertEqual(code, 0, out)
        self.assertIn("✓ Git identity set for all 2 account(s)", out)
        self.assertNotIn("Global git email", out)

    @unittest.skipUnless(shutil.which("git"), "git not available")
    def test_global_git_email_mismatch_warns(self):
        self.write_config(SAMPLE_CONFIG)
        self.write(self.gitconfig, "[user]\n\temail = other@x.com\n")
        code, out = self.call(installer.doctor, platform="linux")
        self.assertEqual(code, 0, out)
        self.assertIn("! Global git email is other@x.com but the active account alice-corp uses alice@corp.com", out)
        self.assertIn("gh-control identity apply", out)


    def write_update_cache(self, latest):
        self.write(
            os.path.join(os.path.dirname(self.config), "update-check.json"),
            json.dumps({"checked_at": int(time.time()), "latest": latest,
                        "url": "https://example.com/r", "error": None}),
        )

    def test_newer_release_warns(self):
        self.write_update_cache("v9.9.9")
        code, out = self.call(installer.doctor, platform="linux")
        self.assertEqual(code, 0, out)
        self.assertIn("! gh-control v9.9.9 is available", out)
        self.assertIn("gh-control update", out)
        self.assertNotIn(["api"], [c[:1] for c in self.gh_calls()])

    def test_up_to_date(self):
        self.write_update_cache("v0.0.1")
        code, out = self.call(installer.doctor, platform="linux")
        self.assertEqual(code, 0, out)
        self.assertIn("✓ gh-control is up to date (latest v0.0.1)", out)

    def test_no_update_cache(self):
        code, out = self.call(installer.doctor, platform="linux")
        self.assertIn("gh-control update --check", out)
        self.assertNotIn(["api"], [c[:1] for c in self.gh_calls()])

    def test_newer_release_hidden_when_checks_disabled(self):
        self.write_config({"check_updates": False})
        self.write_update_cache("v9.9.9")
        code, out = self.call(installer.doctor, platform="linux")
        self.assertNotIn("is available", out)


class DoctorNoGhTest(InstallerTestCase):
    install_gh = False
    hosts_yml = None

    def test_missing_gh_is_a_blocker(self):
        code, out = self.call(installer.doctor, platform="linux")
        self.assertEqual(code, 1, out)
        self.assertIn("✗ GitHub CLI (gh) not found", out)


class CliTest(InstallerTestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, LAUNCHER] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )

    def test_install_uninstall_doctor_commands(self):
        frontend = "xbar" if sys.platform == "darwin" else "argos"
        before = self.snapshot()
        proc = self.run_cli("install", "--dry-run", "--frontend", frontend)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.snapshot(), before)

        proc = self.run_cli("install", "--frontend", frontend)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        proc = self.run_cli("doctor")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Plugin installed", proc.stdout)
        proc = self.run_cli("uninstall")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Removed 1 item", proc.stdout)

    def test_installed_plugin_runs(self):
        self.call(installer.install, frontend="argos", platform="linux")
        link = os.path.join(self.argos, "gh-control.30s.sh")
        os.symlink(sys.executable, os.path.join(self.bin, "python3"))
        os.environ["PATH"] = self.bin + os.pathsep + "/bin" + os.pathsep + "/usr/bin"
        if not os.path.exists("/bin/bash"):
            self.skipTest("bash not available")
        proc = subprocess.run(["/bin/bash", link], stdout=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.splitlines()[0], "● alice-corp")


@unittest.skipUnless(os.path.exists("/bin/sh"), "sh not available")
class InstallScriptTest(InstallerTestCase):
    def setUp(self):
        super().setUp()
        os.symlink(sys.executable, os.path.join(self.bin, "python3"))
        os.environ["PATH"] = self.bin + os.pathsep + "/usr/bin" + os.pathsep + "/bin"

    def run_sh(self, *args):
        return subprocess.run(
            ["/bin/sh", INSTALL_SH, "--method", "source", "--frontend", "tray"] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )

    def test_syntax(self):
        proc = subprocess.run(["/bin/sh", "-n", INSTALL_SH])
        self.assertEqual(proc.returncode, 0)

    def test_dry_run_install_and_uninstall(self):
        before = self.snapshot()
        proc = self.run_sh("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("[dry-run]", proc.stdout)
        self.assertEqual(self.snapshot(), before)

        proc = self.run_sh()
        self.assertEqual(proc.returncode, 0, proc.stdout)
        cli = os.path.join(self.tmp, ".local", "bin", "gh-control")
        self.assertTrue(os.path.islink(cli))
        if sys.platform != "darwin":  # the tray frontend is Linux-only
            self.assertTrue(os.path.exists(self.autostart))
        self.assertIn("Ready to go", proc.stdout)
        self.assertNotIn("\033[", proc.stdout)  # no colours when not a TTY

        proc = self.run_sh()  # idempotent re-run
        self.assertEqual(proc.returncode, 0, proc.stdout)
        if sys.platform != "darwin":  # no tray plugin to find on macOS
            self.assertIn("already installed", proc.stdout)

        proc = self.run_sh("--uninstall")
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertFalse(os.path.lexists(cli))
        self.assertFalse(os.path.exists(self.autostart))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, ".local", "share", "gh-control")))


if __name__ == "__main__":
    unittest.main()
