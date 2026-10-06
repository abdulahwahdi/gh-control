# Security policy

## Supported versions

Security fixes go into the latest release only.

| Version | Supported |
| --- | --- |
| 0.1.x | ✅ |

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Report them
privately through GitHub's
[private vulnerability reporting](https://github.com/abdulahwahdi/gh-control/security/advisories/new)
(**Security → Report a vulnerability** on the repository page).

Please include:

- what the problem is and what an attacker could do with it
- steps to reproduce, and your OS, Python and `gh` versions
- `gh-control doctor` output if it helps. **Remove any tokens first**
  (gh-control never prints them, but other tools might).

You should get a reply within a week. Once a fix is released, you'll be
credited in the changelog unless you'd rather not be.

## What gh-control does with your credentials

- It uses only the account names from gh's `hosts.yml`. It never prints,
  logs, stores or transmits OAuth tokens.
- It switches accounts only through `gh auth switch`.
- It never reads or modifies SSH keys, SSH config or the SSH agent.
- It writes only to its own config file, `git config --global
  user.name/user.email` (when configured), and the plugin links and
  autostart entry it creates in your home directory.
