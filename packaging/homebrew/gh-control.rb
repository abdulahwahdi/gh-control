# Homebrew formula template for gh-control.
# Set url/sha256 for each release
# (see packaging/homebrew/README.md).
class GhControl < Formula
  include Language::Python::Shebang

  desc "Show and switch the active GitHub CLI account from the menu bar"
  homepage "https://github.com/abdulahwahdi/gh-control"
  url "https://github.com/abdulahwahdi/gh-control/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"
  head "https://github.com/abdulahwahdi/gh-control.git", branch: "main"

  depends_on "gh"
  # Standard library only: no virtualenv or resources needed.
  depends_on "python@3.13"

  def install
    libexec.install "bin", "gh_control"
    rewrite_shebang detected_python_shebang,
                    libexec/"bin/gh-control",
                    libexec/"gh_control/plugins/swiftbar/gh-control.30s.py"
    bin.install_symlink libexec/"bin/gh-control"
  end

  def caveats
    <<~EOS
      To add gh-control to your menu bar, install SwiftBar (or xbar) and run:
        brew install --cask swiftbar
        gh-control install

      Log in to each GitHub account once with:
        gh auth login

      Check your setup at any time with:
        gh-control doctor

      The plugin links to #{opt_libexec}, so it keeps working after upgrades.
      Before `brew uninstall gh-control`, run `gh-control uninstall`.
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/gh-control --version")
    assert_match "swiftbar", shell_output("#{bin}/gh-control install --dry-run --frontend swiftbar")
  end
end
