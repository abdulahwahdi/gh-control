import contextlib
import io
import json
import os
import tarfile
import time
import unittest

from helpers import GhTestCase

from gh_control import cli, core, updater


class VersionTest(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(updater.parse_version("v0.2.0"), (0, 2, 0))
        self.assertEqual(updater.parse_version("0.1"), updater.parse_version("0.1.0"))
        self.assertIsNone(updater.parse_version("1.0.0-rc1"))
        self.assertIsNone(updater.parse_version("garbage"))

    def test_is_newer(self):
        self.assertTrue(updater.is_newer("v0.2.0", "0.1.0"))
        self.assertFalse(updater.is_newer("0.1", "0.1.0"))
        self.assertFalse(updater.is_newer("v0.0.9", "0.1.0"))
        self.assertFalse(updater.is_newer("garbage", "0.1.0"))
        self.assertFalse(updater.is_newer("v1.0.0-rc1", "0.1.0"))


class UpdateCliTest(GhTestCase):
    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def cache(self):
        with open(os.path.join(os.path.dirname(self.config), "update-check.json")) as fh:
            return json.load(fh)

    def test_check_reports_update_and_caches(self):
        os.environ["FAKE_GH_RELEASE"] = "v9.9.9"
        code, out, _ = self.run_main("update", "--check")
        self.assertEqual(code, 0)
        self.assertIn("Update available:", out)
        self.assertIn("v9.9.9", out)
        self.assertIn("Run: gh-control update", out)
        self.assertEqual(self.cache()["latest"], "v9.9.9")
        self.assertIsNone(self.cache()["error"])
        self.assertIn(
            "api repos/abdulahwahdi/gh-control/releases/latest --hostname github.com".split(),
            self.gh_calls(),
        )

    def test_check_json(self):
        code, out, _ = self.run_main("update", "--check", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["latest"], "v9.9.9")
        self.assertTrue(data["newer"])
        self.assertIn("current", data)
        self.assertIn("method", data)

    def test_check_up_to_date(self):
        os.environ["FAKE_GH_RELEASE"] = "v0.0.1"
        code, out, _ = self.run_main("update", "--check")
        self.assertEqual(code, 0)
        self.assertIn("is up to date", out)

    def test_check_no_release(self):
        os.environ["FAKE_GH_RELEASE"] = "none"
        code, out, _ = self.run_main("update", "--check")
        self.assertEqual(code, 0)
        self.assertIn("No release published yet", out)
        self.assertIsNone(self.cache()["latest"])

    def test_check_failure_records_error(self):
        os.environ["FAKE_GH_RELEASE_FAIL"] = "1"
        code, out, err = self.run_main("update", "--check")
        self.assertEqual(code, 1)
        self.assertIn("Cannot check for updates", err)
        self.assertIn("HTTP 500", self.cache()["error"])

    def test_check_failure_quiet_prints_nothing(self):
        os.environ["FAKE_GH_RELEASE_FAIL"] = "1"
        code, out, err = self.run_main("update", "--check", "--quiet")
        self.assertEqual((code, out, err), (1, "", ""))

    def test_failed_check_keeps_previous_tag(self):
        updater.save_cached(updater.ReleaseInfo("v9.9.9", "u", 0))
        os.environ["FAKE_GH_RELEASE_FAIL"] = "1"
        with self.assertRaises(core.GhControlError):
            updater.check(force=True)
        self.assertEqual(self.cache()["latest"], "v9.9.9")

    def test_check_uses_fresh_cache(self):
        updater.save_cached(updater.ReleaseInfo("v1.2.3", "u", int(time.time())))
        info = updater.check(force=False)
        self.assertEqual(info.tag, "v1.2.3")
        self.assertEqual(self.gh_calls(), [])
        self.assertFalse(updater.cache_is_stale())

    def test_check_refetches_stale_cache(self):
        updater.save_cached(updater.ReleaseInfo("v1.2.3", "u", 0))
        self.assertTrue(updater.cache_is_stale())
        self.assertEqual(updater.check(force=False).tag, "v9.9.9")

    def test_checkout_only_prints_hint(self):
        os.environ["GH_CONTROL_INSTALL_METHOD"] = "checkout"
        code, out, _ = self.run_main("update")
        self.assertEqual(code, 1)
        self.assertIn("git -C", out)

    def test_unknown_prints_installer(self):
        os.environ["GH_CONTROL_INSTALL_METHOD"] = "unknown"
        code, out, _ = self.run_main("update")
        self.assertEqual(code, 1)
        self.assertIn("install.sh | sh -s -- --ref v9.9.9", out)

    def test_pip_dry_run(self):
        os.environ["GH_CONTROL_INSTALL_METHOD"] = "pip"
        code, out, _ = self.run_main("update", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("[dry-run]", out)
        self.assertIn("pip install --upgrade", out)
        self.assertIn("archive/refs/tags/v9.9.9.tar.gz", out)

    def test_up_to_date_does_nothing(self):
        os.environ["FAKE_GH_RELEASE"] = "v0.0.1"
        os.environ["GH_CONTROL_INSTALL_METHOD"] = "pip"
        code, out, _ = self.run_main("update")
        self.assertEqual(code, 0)
        self.assertIn("is up to date (latest: v0.0.1)", out)

    def test_missing_gh_is_clean_error(self):
        os.environ["GH_CONTROL_GH"] = "/nonexistent"
        code, out, err = self.run_main("update", "--check")
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("gh-control: "), err)
        self.assertNotIn("Traceback", err)


class ReplaceSourceInstallTest(GhTestCase):
    def setUp(self):
        super().setUp()
        self.dest = os.path.join(self.tmp, "share", "gh-control")
        self.write(os.path.join(self.dest, "gh_control", "__init__.py"), '__version__ = "0.1.0"\n')
        self.write(os.path.join(self.dest, "bin", "gh-control"), "old\n")

    def make_tarball(self, files):
        src = os.path.join(self.tmp, "src")
        for name, content in files.items():
            self.write(os.path.join(src, name), content)
        path = os.path.join(self.tmp, "release.tar.gz")
        with tarfile.open(path, "w:gz") as tar:
            for name in files:
                tar.add(os.path.join(src, name), arcname=name)
        os.environ["FAKE_GH_TARBALL"] = path
        return path

    def read(self, *parts):
        with open(os.path.join(self.dest, *parts)) as fh:
            return fh.read()

    def test_replaces_copy(self):
        self.make_tarball({
            "gh-control-9.9.9/gh_control/__init__.py": '__version__ = "9.9.9"\n',
            "gh-control-9.9.9/bin/gh-control": "new\n",
        })
        updater.replace_source_install("v9.9.9", dest=self.dest)
        self.assertIn("9.9.9", self.read("gh_control", "__init__.py"))
        self.assertTrue(os.access(os.path.join(self.dest, "bin", "gh-control"), os.X_OK))
        self.assertFalse(os.path.exists(self.dest + ".old"))
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.dest))), ["gh-control"])
        self.assertIn(
            "api repos/abdulahwahdi/gh-control/tarball/v9.9.9 --hostname github.com".split(),
            self.gh_calls(),
        )

    def test_rejects_path_traversal(self):
        path = self.make_tarball({
            "gh-control-9.9.9/gh_control/__init__.py": '__version__ = "9.9.9"\n',
            "gh-control-9.9.9/bin/gh-control": "new\n",
        })
        evil = os.path.join(self.tmp, "evil")
        self.write(evil, "pwned\n")
        with tarfile.open(path, "w:gz") as tar:
            tar.add(evil, arcname="gh-control-9.9.9/../evil")
        with self.assertRaises(core.GhControlError):
            updater.replace_source_install("v9.9.9", dest=self.dest)
        self.assertIn("0.1.0", self.read("gh_control", "__init__.py"))
        self.assertEqual(self.read("bin", "gh-control"), "old\n")
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.dest))), ["gh-control"])

    def test_rejects_tarball_without_gh_control(self):
        self.make_tarball({"gh-control-9.9.9/README.md": "hi\n"})
        with self.assertRaises(core.GhControlError):
            updater.replace_source_install("v9.9.9", dest=self.dest)
        self.assertIn("0.1.0", self.read("gh_control", "__init__.py"))

    def test_source_update_end_to_end(self):
        self.make_tarball({
            "gh-control-9.9.9/gh_control/__init__.py": '__version__ = "9.9.9"\n',
            "gh-control-9.9.9/bin/gh-control": "new\n",
        })
        os.environ["GH_CONTROL_INSTALL_METHOD"] = "source"
        saved = updater.ROOT_DIR
        updater.ROOT_DIR = self.dest
        self.addCleanup(setattr, updater, "ROOT_DIR", saved)
        # The default argument was bound at import; call through run_update's path.
        original = updater.replace_source_install
        self.addCleanup(setattr, updater, "replace_source_install", original)
        updater.replace_source_install = lambda tag: original(tag, dest=self.dest)
        out = io.StringIO()
        self.assertEqual(updater.run_update(out=out), 0)
        self.assertIn("✓ Updated to v9.9.9", out.getvalue())
        self.assertIn("9.9.9", self.read("gh_control", "__init__.py"))
        with open(updater.cache_path()) as fh:
            self.assertEqual(json.load(fh)["latest"], "v9.9.9")


if __name__ == "__main__":
    unittest.main()
