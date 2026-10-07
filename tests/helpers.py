"""Shared test fixtures: a fake `gh` on PATH and temp config dirs."""

import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

HOSTS_YML = textwrap.dedent(
    """\
    github.com:
        users:
            alice-corp:
                oauth_token: gho_corp_secret
            alice:
                oauth_token: gho_personal_secret
        git_protocol: ssh
        user: alice-corp
        oauth_token: gho_corp_secret
    """
)

# A tiny stand-in for gh: logs its arguments, answers `auth status`, and
# implements `auth switch` by rewriting the `user:` line of hosts.yml, and
# answers `api users/<login>` (fails when FAKE_GH_API_FAIL is set).
FAKE_GH = textwrap.dedent(
    """\
    #!{python}
    import json, os, re, sys
    args = sys.argv[1:]
    log = os.environ.get("FAKE_GH_LOG")
    if log:
        with open(log, "a") as fh:
            fh.write(" ".join(args) + "\\n")
    hosts = os.path.join(os.environ["GH_CONFIG_DIR"], "hosts.yml")
    if args[:1] == ["--version"]:
        sys.stdout.write("gh version " + os.environ.get("FAKE_GH_VERSION", "2.62.0") + " (2024-11-14)\\n")
        sys.exit(0)
    if args[:2] == ["auth", "status"]:
        if "--json" in args:
            sys.stderr.write("unknown flag: --json\\n")
            sys.exit(1)
        sys.stdout.write(os.environ.get("FAKE_GH_STATUS", ""))
        sys.exit(0)
    if args[:2] == ["auth", "switch"]:
        user = args[args.index("--user") + 1]
        text = open(hosts).read()
        if not re.search(r"^ +" + re.escape(user) + r":", text, re.M):
            sys.stderr.write("not logged in to github.com account " + user + "\\n")
            sys.exit(1)
        text = re.sub(r"^(    user: ).*$", r"\\g<1>" + user, text, flags=re.M)
        open(hosts, "w").write(text)
        sys.exit(0)
    if args[:1] == ["api"] and len(args) > 1 and args[1].startswith("users/"):
        login = args[1].split("/", 1)[1]
        if os.environ.get("FAKE_GH_API_FAIL"):
            sys.stderr.write("HTTP 404\\n")
            sys.exit(1)
        ids = {{"alice-corp": 101, "alice": 102}}
        names = {{"alice-corp": "Alice Corp", "alice": None}}
        sys.stdout.write(json.dumps({{"login": login, "id": ids.get(login, 999), "name": names.get(login)}}))
        sys.exit(0)
    sys.stderr.write("fake gh: unsupported " + " ".join(args) + "\\n")
    sys.exit(2)
    """
)


class GhTestCase(unittest.TestCase):
    """Sets up an isolated HOME, GH_CONFIG_DIR, config path and fake gh."""

    install_gh = True
    hosts_yml = HOSTS_YML

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gh-control-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, "bin")
        self.gh_dir = os.path.join(self.tmp, "gh")
        os.makedirs(self.bin)
        os.makedirs(self.gh_dir)
        self.log = os.path.join(self.tmp, "gh.log")
        self.config = os.path.join(self.tmp, "gh-control", "config.json")
        self.gitconfig = os.path.join(self.tmp, "gitconfig")
        if self.hosts_yml is not None:
            self.write(os.path.join(self.gh_dir, "hosts.yml"), self.hosts_yml)
        if self.install_gh:
            gh = os.path.join(self.bin, "gh")
            self.write(gh, FAKE_GH.format(python=sys.executable))
            os.chmod(gh, 0o755)

        # Only the fake gh (and git, for identity tests) are on PATH, so a
        # real gh elsewhere on the machine cannot leak into the tests.
        git = shutil.which("git")
        if git:
            os.symlink(git, os.path.join(self.bin, "git"))
        env = {
            "HOME": self.tmp,
            "PATH": self.bin,
            "GH_CONFIG_DIR": self.gh_dir,
            "XDG_CONFIG_HOME": os.path.join(self.tmp, "xdg"),
            "GH_CONTROL_CONFIG": self.config,
            "GH_CONTROL_SEARCH_PATH": "",
            "GH_CONTROL_NO_NOTIFY": "1",
            "GIT_CONFIG_GLOBAL": self.gitconfig,
            "GIT_CONFIG_NOSYSTEM": "1",
            "FAKE_GH_LOG": self.log,
        }
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        for key in ("GH_CONTROL_GH", "GH_TOKEN", "GITHUB_TOKEN"):
            os.environ.pop(key, None)
        os.environ.update(env)

    def write(self, path, content):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)

    def write_config(self, data):
        self.write(self.config, json.dumps(data))

    def gh_calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as fh:
            return [line.split() for line in fh.read().splitlines()]

    def read_hosts(self):
        with open(os.path.join(self.gh_dir, "hosts.yml")) as fh:
            return fh.read()


SAMPLE_CONFIG = {
    "host": "github.com",
    "accounts": {
        "alice-corp": {
            "label": "Work",
            "icon": "💼",
            "color": "#e5534b",
            "git_name": "Alice Corp",
            "git_email": "alice@corp.com",
        },
        "alice": {
            "label": "Personal",
            "icon": "🏠",
            "color": "#3fb950",
            "git_name": "Alice",
            "git_email": "alice@gmail.com",
        },
    },
}
