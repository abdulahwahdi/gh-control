# Homebrew formula

`gh-control.rb` is a template for a Homebrew tap. It installs the package
into `libexec` (no virtualenv, since gh-control only uses the Python standard
library), links `gh-control` into Homebrew's `bin`, and depends on `gh` and
Homebrew's Python.

## Publishing to a tap

1. Create a public repository named **`homebrew-tap`** under the same
   GitHub owner (for example `OWNER/homebrew-tap`).
2. Tag a release in this repository, e.g. `v0.1.0`, and compute the
   tarball checksum:

   ```sh
   curl -fsSL https://github.com/OWNER/gh-control/archive/refs/tags/v0.1.0.tar.gz | shasum -a 256
   ```

3. Copy `gh-control.rb` to `Formula/gh-control.rb` in the tap. Replace
   `OWNER`, and set `url` and `sha256` to match the tag.
4. Check it locally:

   ```sh
   brew install --build-from-source ./Formula/gh-control.rb
   brew test gh-control
   brew audit --strict --new gh-control
   ```

5. Commit and push the tap. Users can then install with:

   ```sh
   brew install OWNER/tap/gh-control
   gh-control install
   ```

For each new release, update `url` and `sha256`. `version` comes from the
tag in the URL.

## Notes

- `gh-control install` links the SwiftBar/xbar plugin through Homebrew's
  version-independent `opt/` path, so upgrades don't break the plugin.
  Running it again after an upgrade is safe (it's idempotent).
- The formula pins `python@3.13`. When Homebrew moves its default Python
  forward, bump it to match.
