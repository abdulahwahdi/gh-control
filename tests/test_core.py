import json
import os
import shutil
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
        self.assertEqual([c for c in self.gh_calls() if c[:1] == ["auth"]], [])

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


@unittest.skipUnless(shutil.which("git"), "git not installed")
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

    def identities_file(self):
        return os.path.join(os.path.dirname(self.config), "identities.json")

    def api_calls(self):
        return [c for c in self.gh_calls() if c[:1] == ["api"]]

    def test_git_identity_applied_from_config_without_fetch(self):
        self.write_config(SAMPLE_CONFIG)
        account = core.switch("alice")
        self.assertEqual(account.git_source, "config")
        self.assertEqual(self.api_calls(), [])

    def test_identity_from_github_without_config(self):
        account = core.switch("alice")
        self.assertEqual(self.git_get("user.name"), "alice")
        self.assertEqual(self.git_get("user.email"), "102+alice@users.noreply.github.com")
        self.assertEqual(account.git_source, "github")
        self.assertIsNone(account.git_warning)
        self.assertIn(["api", "users/alice", "--hostname", "github.com"], self.gh_calls())
        with open(self.identities_file()) as fh:
            cached = json.load(fh)
        self.assertEqual(
            cached["github.com"]["alice"],
            {"name": "alice", "email": "102+alice@users.noreply.github.com", "id": 102},
        )

    def test_github_name_used_when_set(self):
        core.switch("alice")
        core.switch("alice-corp")
        self.assertEqual(self.git_get("user.name"), "Alice Corp")
        self.assertEqual(self.git_get("user.email"), "101+alice-corp@users.noreply.github.com")

    def test_auto_identity_disabled(self):
        self.write_config({"auto_git_identity": False})
        account = core.switch("alice")
        self.assertFalse(os.path.exists(self.gitconfig))
        self.assertEqual(self.api_calls(), [])
        self.assertIsNone(account.git_source)

    def test_config_overrides_github_per_field(self):
        self.write_config({"accounts": {"alice": {"git_email": "me@x.com"}}})
        account = core.switch("alice")
        self.assertEqual(self.git_get("user.name"), "alice")
        self.assertEqual(self.git_get("user.email"), "me@x.com")
        self.assertEqual(account.git_source, "mixed")
        self.assertEqual(account.to_dict()["git_source"], "mixed")

    def test_cache_used_without_fetch(self):
        self.write(
            self.identities_file(),
            json.dumps({"github.com": {
                "alice": {"name": "Cached Alice", "email": "102+alice@users.noreply.github.com", "id": 102},
            }}),
        )
        accounts = {a.login: a for a in core.list_accounts()}
        alice = accounts["alice"]
        self.assertEqual(
            (alice.git_name, alice.git_email, alice.git_source),
            ("Cached Alice", "102+alice@users.noreply.github.com", "github"),
        )
        self.assertTrue(alice.has_git_identity())
        self.assertIsNone(accounts["alice-corp"].git_source)
        self.assertFalse(accounts["alice-corp"].has_git_identity())
        self.assertEqual(self.gh_calls(), [])

    def test_bad_cache_is_ignored(self):
        self.write(self.identities_file(), "[not a dict")
        self.assertEqual(core.load_identity_cache(), {})
        self.assertEqual(len(core.list_accounts()), 2)

    def test_fetch_failure_does_not_block_switch(self):
        os.environ["FAKE_GH_API_FAIL"] = "1"
        account = core.switch("alice")
        self.assertEqual(core.active_account().login, "alice")
        self.assertIn("HTTP 404", account.git_warning)
        self.assertNotIn("git_warning", account.to_dict())
        self.assertFalse(os.path.exists(self.gitconfig))
        self.assertFalse(os.path.exists(self.identities_file()))

    def make_repo(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", repo], check=True)
        return repo

    def test_apply_local_identity(self):
        repo = self.make_repo()
        account = core.Account(login="alice", git_name="Alice", git_email="local@x.com")
        changes = core.apply_git_identity(account, scope="local", cwd=repo)
        self.assertEqual(changes, ["user.name=Alice", "user.email=local@x.com"])
        proc = subprocess.run(
            ["git", "config", "--local", "user.email"],
            cwd=repo,
            stdout=subprocess.PIPE,
            universal_newlines=True,
        )
        self.assertEqual(proc.stdout.strip(), "local@x.com")
        self.assertFalse(os.path.exists(self.gitconfig))
        self.assertEqual(
            core.read_git_identity("local", cwd=repo),
            {"name": "Alice", "email": "local@x.com"},
        )
        self.assertEqual(core.read_git_identity("global", cwd=repo), {"name": None, "email": None})
        self.assertEqual(core.read_git_identity(cwd=repo)["email"], "local@x.com")

    def test_apply_local_outside_repo_raises(self):
        outside = os.path.join(self.tmp, "not-a-repo")
        os.makedirs(outside)
        os.environ["GIT_CEILING_DIRECTORIES"] = self.tmp
        account = core.Account(login="alice", git_name="Alice", git_email="a@x.com")
        with self.assertRaises(core.GhControlError) as ctx:
            core.apply_git_identity(account, scope="local", cwd=outside)
        self.assertIn("Not inside a git repository", str(ctx.exception))


class NoreplyEmailTest(unittest.TestCase):
    def test_github_com(self):
        self.assertEqual(
            core.noreply_email("alice", 102, "github.com"),
            "102+alice@users.noreply.github.com",
        )

    def test_enterprise_host(self):
        self.assertEqual(
            core.noreply_email("alice", 7, "ghe.corp.com"),
            "7+alice@users.noreply.ghe.corp.com",
        )


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
