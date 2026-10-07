import json
import os
import shutil
import subprocess
import sys
import unittest

from helpers import ROOT, SAMPLE_CONFIG, GhTestCase

from gh_control import menu

LAUNCHER = os.path.realpath(os.path.join(ROOT, "bin", "gh-control"))


class MenuRenderTest(GhTestCase):
    def lines(self, fmt):
        return menu.render(fmt).splitlines()

    def test_swiftbar_menu(self):
        lines = self.lines("swiftbar")
        self.assertEqual(lines[0], "● alice-corp")
        self.assertEqual(lines[1], "---")
        row = next(l for l in lines if "param2=alice " in l)
        self.assertIn("bash=" + LAUNCHER, row)
        self.assertIn("param1=switch", row)
        self.assertIn("terminal=false", row)
        self.assertIn("refresh=true", row)
        active = next(l for l in lines if "param2=alice-corp" in l)
        self.assertTrue(active.startswith("✓"))
        self.assertTrue(any("href=https://github.com/alice-corp" in l for l in lines))
        self.assertTrue(any(l.startswith("Refresh") for l in lines))
        self.assertTrue(any("param1=open-config" in l for l in lines))

    def test_argos_menu(self):
        lines = self.lines("argos")
        self.assertEqual(lines[0], "● alice-corp")
        self.assertEqual(lines[1], "---")
        row = next(l for l in lines if " alice |" in l)
        self.assertIn("bash='{} switch alice'".format(LAUNCHER), row)
        self.assertIn("terminal=false", row)

    def test_config_styles_title(self):
        self.write_config(dict(SAMPLE_CONFIG, accounts=dict(
            SAMPLE_CONFIG["accounts"],
            **{"alice-corp": dict(SAMPLE_CONFIG["accounts"]["alice-corp"], sfimage="briefcase.fill")}
        )))
        swift = self.lines("swiftbar")
        self.assertEqual(swift[0], "💼 alice-corp | color=#e5534b sfimage=briefcase.fill")
        self.assertTrue(any("🏠 Personal — alice |" in l for l in swift))
        argos = self.lines("argos")
        self.assertEqual(argos[0], "💼 alice-corp | color=#e5534b")

    def test_json_menu(self):
        data = json.loads(menu.render("json"))
        self.assertEqual(data["active"], "alice-corp")
        self.assertEqual(len(data["accounts"]), 2)
        self.assertIsNone(data["error"])

    def test_pipe_in_label_is_escaped(self):
        self.write_config({"accounts": {"alice": {"label": "a|b"}}})
        row = next(l for l in self.lines("swiftbar") if "param2=alice " in l)
        self.assertEqual(row.count("|"), 1)

    def test_invalid_config_shows_warning_row(self):
        self.write(self.config, "{oops")
        self.assertTrue(any(l.startswith("⚠ Cannot read") for l in self.lines("swiftbar")))


@unittest.skipUnless(shutil.which("git"), "git not available")
class MenuGitIdentityTest(GhTestCase):
    def lines(self, fmt):
        return menu.render(fmt).splitlines()

    def test_identity_row_from_config(self):
        self.write_config(SAMPLE_CONFIG)
        lines = self.lines("swiftbar")
        self.assertTrue(any(l.startswith("Git: Alice Corp <alice@corp.com>") for l in lines), lines)
        self.assertFalse(any("No git identity" in l for l in lines))
        self.assertFalse(any("git uses" in l for l in lines))

    def test_missing_identity_offers_sync(self):
        lines = self.lines("swiftbar")
        row = next(l for l in lines if "No git identity for alice-corp" in l)
        self.assertIn("param1=identity", row)
        self.assertIn("param2=sync", row)
        self.assertIn("param4=alice-corp", row)
        self.assertNotIn(["api"], [c[:1] for c in self.gh_calls()])

    def test_mismatch_offers_apply(self):
        self.write_config(SAMPLE_CONFIG)
        self.write(self.gitconfig, "[user]\n\temail = other@x.com\n")
        row = next(l for l in self.lines("swiftbar") if l.startswith("⚠ git uses other@x.com"))
        self.assertIn("param1=identity", row)
        self.assertIn("param2=apply", row)

    def test_no_mismatch_warning_when_switching_disabled(self):
        self.write_config(dict(SAMPLE_CONFIG, set_git_identity=False))
        self.write(self.gitconfig, "[user]\n\temail = other@x.com\n")
        self.assertFalse(any("git uses" in l for l in self.lines("swiftbar")))

    def test_json_has_git_global(self):
        self.write(self.gitconfig, "[user]\n\tname = Other\n\temail = other@x.com\n")
        data = json.loads(menu.render("json"))
        self.assertEqual(data["git_global"], {"name": "Other", "email": "other@x.com"})
        self.assertTrue(data["set_git_identity"])

    def test_argos_escapes_email(self):
        self.write_config(SAMPLE_CONFIG)
        lines = self.lines("argos")
        self.assertTrue(any(l.startswith("Git: Alice Corp &lt;alice@corp.com&gt;") for l in lines), lines)


class NotLoggedInMenuTest(GhTestCase):
    hosts_yml = None

    def test_warning_menu(self):
        os.environ["FAKE_GH_STATUS"] = "You are not logged into any GitHub hosts.\n"
        lines = menu.render("swiftbar").splitlines()
        self.assertTrue(lines[0].startswith("⚠ gh"))
        self.assertTrue(any("gh auth login" in l for l in lines))


class NoGhMenuTest(GhTestCase):
    install_gh = False

    def test_warning_menu_in_process(self):
        lines = menu.render("swiftbar").splitlines()
        self.assertTrue(lines[0].startswith("⚠ gh"))
        self.assertIn("not found", lines[2])

    def run_plugin(self, cmd):
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("Traceback", proc.stdout + proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertTrue(lines[0].startswith("⚠ gh"), proc.stdout)
        self.assertEqual(lines[1], "---")
        return lines

    def test_cli_menu_exits_zero(self):
        self.run_plugin([sys.executable, LAUNCHER, "menu", "--format", "argos"])

    def test_swiftbar_plugin(self):
        self.run_plugin([sys.executable, os.path.join(ROOT, "plugins", "swiftbar", "gh-control.30s.py")])

    @unittest.skipUnless(os.path.exists("/bin/bash"), "bash not available")
    def test_argos_plugin(self):
        # The plugin needs python3 on PATH; expose only the interpreter.
        link = os.path.join(self.bin, "python3")
        os.symlink(sys.executable, link)
        os.environ["PATH"] = self.bin + os.pathsep + "/bin"
        # /bin may hold a real gh (CI runners); force "gh not found".
        os.environ["GH_CONTROL_GH"] = "/nonexistent"
        self.run_plugin(["/bin/bash", os.path.join(ROOT, "plugins", "argos", "gh-control.30s.sh")])


class PluginWithGhTest(GhTestCase):
    def test_swiftbar_plugin_via_symlink(self):
        link = os.path.join(self.tmp, "gh-control.30s.py")
        os.symlink(os.path.join(ROOT, "plugins", "swiftbar", "gh-control.30s.py"), link)
        proc = subprocess.run([sys.executable, link], stdout=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(proc.returncode, 0)
        lines = proc.stdout.splitlines()
        self.assertEqual(lines[0], "● alice-corp")
        self.assertTrue(any("param1=switch" in l and "bash=" + LAUNCHER in l for l in lines))


if __name__ == "__main__":
    unittest.main()
