import json
import os
import shutil
import subprocess
import sys
import unittest

from helpers import ROOT, SAMPLE_CONFIG, GhTestCase

from gh_control import core

HAS_GIT = shutil.which("git") is not None


class IdentityCliTest(GhTestCase):
    def run_cli(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, os.path.join(ROOT, "bin", "gh-control")] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            cwd=cwd or self.tmp,
        )

    def show_json(self):
        proc = self.run_cli("identity", "show", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        return {a["login"]: a for a in data["accounts"]}, data["git"]

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git"] + list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            cwd=cwd,
        )

    def make_repo(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        self.assertEqual(self.git("init", "-q", repo).returncode, 0)
        return repo

    def test_sync_fills_cache_and_show_reports_it(self):
        proc = self.run_cli("identity", "sync")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("✓ alice: alice <102+alice@users.noreply.github.com>", proc.stdout)
        with open(core.identities_path()) as fh:
            cache = json.load(fh)
        self.assertEqual(set(cache["github.com"]), {"alice", "alice-corp"})
        accounts, _ = self.show_json()
        self.assertEqual(accounts["alice"]["git_email"], "102+alice@users.noreply.github.com")
        self.assertEqual(accounts["alice"]["git_source"], "github")

    def test_sync_runs_when_auto_identity_disabled(self):
        self.write_config({"auto_git_identity": False})
        proc = self.run_cli("identity", "sync", "--user", "alice")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.exists(core.identities_path()))

    def test_sync_all_failures_exit_1(self):
        os.environ["FAKE_GH_API_FAIL"] = "1"
        proc = self.run_cli("identity", "sync")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("! alice:", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)

    def test_show_without_identity_hints_sync(self):
        proc = self.run_cli("identity")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no git identity", proc.stdout)
        self.assertIn("git global:", proc.stdout)

    def test_set_keeps_other_keys(self):
        self.write_config(SAMPLE_CONFIG)
        proc = self.run_cli("identity", "set", "alice", "--email", "a@b.c")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Saved to " + self.config, proc.stdout)
        with open(self.config) as fh:
            data = json.load(fh)
        self.assertEqual(list(data["accounts"]), ["alice-corp", "alice"])
        self.assertEqual(data["accounts"]["alice"]["label"], "Personal")
        self.assertEqual(data["accounts"]["alice"]["git_email"], "a@b.c")
        accounts, _ = self.show_json()
        self.assertEqual(accounts["alice"]["git_email"], "a@b.c")
        self.assertIn(accounts["alice"]["git_source"], ("config", "mixed"))

    def test_set_without_config_and_removal(self):
        self.run_cli("identity", "sync")
        self.assertEqual(self.run_cli("identity", "set", "alice", "--name", "Al").returncode, 0)
        accounts, _ = self.show_json()
        self.assertEqual(accounts["alice"]["git_name"], "Al")
        self.assertEqual(accounts["alice"]["git_source"], "mixed")
        self.assertEqual(self.run_cli("identity", "set", "alice", "--name", "").returncode, 0)
        accounts, _ = self.show_json()
        self.assertEqual(accounts["alice"]["git_source"], "github")

    def test_set_unknown_account(self):
        proc = self.run_cli("identity", "set", "mallory", "--name", "x")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Unknown account", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_set_requires_option_and_valid_email(self):
        proc = self.run_cli("identity", "set", "alice")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("--name", proc.stderr)
        proc = self.run_cli("identity", "set", "alice", "--email", "nope")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Invalid email", proc.stderr)
        self.assertFalse(os.path.exists(self.config))

    def test_set_refuses_to_overwrite_broken_config(self):
        self.write(self.config, "{not json")
        proc = self.run_cli("identity", "set", "alice", "--name", "x")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(self.config, proc.stderr)
        with open(self.config) as fh:
            self.assertEqual(fh.read(), "{not json")

    @unittest.skipUnless(HAS_GIT, "git not installed")
    def test_set_active_account_applies_globally(self):
        proc = self.run_cli("identity", "set", "alice-corp", "--name", "AC", "--email", "ac@corp.com")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(core.read_git_identity("global"), {"name": "AC", "email": "ac@corp.com"})

    @unittest.skipUnless(HAS_GIT, "git not installed")
    def test_apply_local_sets_repo_only(self):
        repo = self.make_repo()
        proc = self.run_cli("identity", "apply", "--local", "--path", repo)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Set local git identity for " + repo, proc.stdout)
        email = self.git("config", "--local", "user.email", cwd=repo).stdout.strip()
        self.assertEqual(email, "101+alice-corp@users.noreply.github.com")
        self.assertFalse(os.path.exists(self.gitconfig))

    @unittest.skipUnless(HAS_GIT, "git not installed")
    def test_apply_local_outside_repo(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        proc = self.run_cli("identity", "apply", "--local", "--path", plain)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Not inside a git repository", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    @unittest.skipUnless(HAS_GIT, "git not installed")
    def test_apply_global_for_user(self):
        proc = self.run_cli("identity", "apply", "--user", "alice")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(core.read_git_identity("global")["email"],
                         "102+alice@users.noreply.github.com")

    def test_apply_without_any_identity(self):
        os.environ["FAKE_GH_API_FAIL"] = "1"
        proc = self.run_cli("identity", "apply")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("No git identity for alice-corp", proc.stderr)

    @unittest.skipUnless(HAS_GIT, "git not installed")
    def test_show_warns_on_mismatch(self):
        self.run_cli("identity", "sync")
        self.git("config", "--global", "user.email", "other@example.com")
        proc = self.run_cli("identity", "show")
        self.assertIn("! git commits here use <other@example.com>", proc.stdout)
        self.assertIn("gh-control identity apply", proc.stdout)

    @unittest.skipUnless(HAS_GIT, "git not installed")
    def test_switch_reports_identity(self):
        proc = self.run_cli("switch", "alice")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Git identity: alice <102+alice@users.noreply.github.com>", proc.stdout)

    def test_switch_fetch_failure_warns_but_succeeds(self):
        os.environ["FAKE_GH_API_FAIL"] = "1"
        proc = self.run_cli("switch", "alice")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("could not fetch git identity", proc.stderr)

    def test_switch_quiet_when_git_identity_disabled(self):
        self.write_config({"set_git_identity": False})
        proc = self.run_cli("switch", "alice")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("Git identity", proc.stdout)

    def test_open_config_prefills_identity(self):
        self.run_cli("identity", "sync")
        proc = self.run_cli("open-config", "--print-path")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        with open(self.config) as fh:
            data = json.load(fh)
        self.assertEqual(data["accounts"]["alice"]["git_email"],
                         "102+alice@users.noreply.github.com")
        self.assertTrue(data["auto_git_identity"])
        self.assertTrue(data["set_git_identity"])


if __name__ == "__main__":
    unittest.main()
