import os
import subprocess
import sys
import unittest

from helpers import ROOT, SAMPLE_CONFIG, GhTestCase

from gh_control import __version__, core


class HostsParsingTest(GhTestCase):
    def test_two_accounts_parsed(self):
        accounts = core.list_accounts()
        self.assertEqual([a.login for a in accounts], ["alice-corp", "alice"])

    def test_active_detection(self):
        self.assertEqual(core.active_account().login, "alice-corp")
        self.assertEqual(core.next_account().login, "alice")

    def test_defaults_without_config(self):
        account = core.active_account()
        self.assertEqual(account.label, "alice-corp")
        self.assertEqual(account.icon, core.DEFAULT_ICON)
        self.assertIsNone(account.color)

    def test_no_gh_call_when_hosts_file_present(self):
        core.list_accounts()
        self.assertEqual(self.gh_calls(), [])

    def test_tokens_not_exposed(self):
        for account in core.list_accounts():
            self.assertNotIn("gho_", repr(account.to_dict()))

    def test_parse_simple_yaml_variants(self):
        data = core.parse_simple_yaml(
            "# comment\n"
            '"github.com":\n'
            "  users:\n"
            "    one: {}\n"
            "    two:\n"
            "  user: 'two'  \n"
            "  git_protocol: ssh # inline\n"
        )
        self.assertEqual(data["github.com"]["users"], {"one": {}, "two": {}})
        self.assertEqual(data["github.com"]["user"], "two")
        self.assertEqual(data["github.com"]["git_protocol"], "ssh")

    def test_legacy_single_account_layout(self):
        self.write(
            os.path.join(self.gh_dir, "hosts.yml"),
            "github.com:\n    oauth_token: x\n    user: solo\n",
        )
        accounts = core.list_accounts()
        self.assertEqual([(a.login, a.active) for a in accounts], [("solo", True)])

    def test_xdg_config_home_used_without_gh_config_dir(self):
        del os.environ["GH_CONFIG_DIR"]
        self.assertEqual(
            core.hosts_file(),
            os.path.join(os.environ["XDG_CONFIG_HOME"], "gh", "hosts.yml"),
        )


class AuthStatusFallbackTest(GhTestCase):
    hosts_yml = None

    def test_text_fallback(self):
        os.environ["FAKE_GH_STATUS"] = (
            "github.com\n"
            "  ✓ Logged in to github.com account alice-corp (keyring)\n"
            "  - Active account: false\n"
            "  - Token: gho_****\n"
            "  ✓ Logged in to github.com account alice (keyring)\n"
            "  - Active account: true\n"
            "ghe.corp.com\n"
            "  ✓ Logged in to ghe.corp.com account other (keyring)\n"
            "  - Active account: true\n"
        )
        accounts = core.list_accounts()
        self.assertEqual(
            [(a.login, a.active) for a in accounts],
            [("alice-corp", False), ("alice", True)],
        )
        calls = self.gh_calls()
        self.assertIn("--json", calls[0])
        self.assertEqual(calls[1], ["auth", "status", "--hostname", "github.com"])

    def test_json_parse(self):
        accounts = core.parse_auth_status_json(
            '{"hosts":{"github.com":[{"login":"a","active":false},{"login":"b","active":true}]}}',
            "github.com",
        )
        self.assertEqual([(a.login, a.active) for a in accounts], [("a", False), ("b", True)])

    def test_not_logged_in(self):
        os.environ["FAKE_GH_STATUS"] = "You are not logged into any GitHub hosts.\n"
        self.assertEqual(core.list_accounts(), [])
        self.assertIsNone(core.active_account())


class SwitchTest(GhTestCase):
    def test_switch_calls_gh_with_expected_args(self):
        account = core.switch("alice")
        self.assertEqual(account.login, "alice")
        self.assertIn(
            ["auth", "switch", "--hostname", "github.com", "--user", "alice"],
            self.gh_calls(),
        )
        self.assertEqual(core.active_account().login, "alice")

    def test_switch_to_active_account_is_noop_for_gh(self):
        core.switch("alice-corp")
        self.assertEqual(self.gh_calls(), [])

    def test_unknown_user_raises(self):
        with self.assertRaises(core.UnknownAccountError) as ctx:
            core.switch("mallory")
        self.assertIn("alice-corp", str(ctx.exception))
        self.assertEqual(self.gh_calls(), [])

    def test_gh_failure_raises_switch_error(self):
        self.write(
            os.path.join(self.bin, "gh"),
            "#!{}\nimport sys\nsys.stderr.write('unknown command \"switch\"')\nsys.exit(1)\n".format(sys.executable),
        )
        with self.assertRaises(core.SwitchError) as ctx:
            core.switch("alice")
        self.assertIn("2.40", str(ctx.exception))

    def test_switch_without_gh(self):
        os.remove(os.path.join(self.bin, "gh"))
        with self.assertRaises(core.GhNotFoundError):
            core.switch("alice")

    def test_toggle_cycles(self):
        core.switch(core.next_account().login)
        self.assertEqual(core.active_account().login, "alice")
        core.switch(core.next_account().login)
        self.assertEqual(core.active_account().login, "alice-corp")


class ConfigTest(GhTestCase):
    def test_labels_and_icons(self):
        self.write_config(SAMPLE_CONFIG)
        account = core.active_account()
        self.assertEqual((account.label, account.icon, account.color), ("Work", "💼", "#e5534b"))

    def test_config_order_wins(self):
        self.write_config({"accounts": {"alice": {}, "alice-corp": {}}})
        self.assertEqual([a.login for a in core.list_accounts()], ["alice", "alice-corp"])

    def test_missing_keys_and_bad_types_do_not_error(self):
        self.write_config({"accounts": {"alice": None, "alice-corp": {"label": 3}}, "host": ""})
        cfg = core.load_config()
        self.assertIsNone(cfg.error)
        self.assertEqual(cfg.host, "github.com")
        accounts = {a.login: a for a in core.list_accounts(cfg)}
        self.assertEqual(accounts["alice-corp"].label, "alice-corp")

    def test_invalid_json_is_reported_not_raised(self):
        self.write(self.config, "{not json")
        cfg = core.load_config()
        self.assertIsNotNone(cfg.error)
        self.assertEqual(len(core.list_accounts(cfg)), 2)


@unittest.skipUnless(__import__("shutil").which("git"), "git not installed")
class GitIdentityTest(GhTestCase):
    def git_get(self, key):
        proc = subprocess.run(
            ["git", "config", "--global", key],
            stdout=subprocess.PIPE,
            universal_newlines=True,
        )
        return proc.stdout.strip()

    def test_git_identity_applied(self):
        self.write_config(SAMPLE_CONFIG)
        core.switch("alice")
        self.assertEqual(self.git_get("user.name"), "Alice")
        self.assertEqual(self.git_get("user.email"), "alice@gmail.com")
        with open(self.gitconfig) as fh:
            self.assertIn("alice@gmail.com", fh.read())

    def test_git_identity_disabled(self):
        cfg = dict(SAMPLE_CONFIG, set_git_identity=False)
        self.write_config(cfg)
        core.switch("alice")
        self.assertFalse(os.path.exists(self.gitconfig))

    def test_no_git_identity_without_config(self):
        core.switch("alice")
        self.assertFalse(os.path.exists(self.gitconfig))


class CliTest(GhTestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "gh-control")] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )

    def test_version(self):
        proc = self.run_cli("--version")
        self.assertEqual(proc.returncode, 0)
        self.assertIn(__version__, proc.stdout)

    def test_list_and_current(self):
        proc = self.run_cli("list")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("* ● alice-corp", proc.stdout)
        self.assertIn("alice", proc.stdout)
        self.assertEqual(self.run_cli("current").stdout.strip(), "alice-corp")

    def test_switch_and_toggle(self):
        proc = self.run_cli("switch", "alice")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.run_cli("current").stdout.strip(), "alice")
        self.run_cli("toggle")
        self.assertEqual(self.run_cli("current").stdout.strip(), "alice-corp")

    def test_unknown_user_clean_error(self):
        proc = self.run_cli("switch", "mallory")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Unknown account", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_open_config_print_path_creates_sample(self):
        proc = self.run_cli("open-config", "--print-path")
        self.assertEqual(proc.stdout.strip(), self.config)
        self.assertTrue(os.path.exists(self.config))
        self.assertIsNone(core.load_config().error)


if __name__ == "__main__":
    unittest.main()
