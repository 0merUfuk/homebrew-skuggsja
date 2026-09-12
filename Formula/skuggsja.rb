# frozen_string_literal: true

# Generated from the release checksums; do not edit download values manually.
# Skuggsja release version: 0.1.2
class Skuggsja < Formula
  desc "Local history retrospective for AI coding agents"
  homepage "https://github.com/0merUfuk/skuggsja"
  license "MIT"

  on_macos do
    on_arm do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.1.2/skuggsja_0.1.2_darwin_arm64.tar.gz"
      sha256 "e33a5bc92d7e4ea26f8a428af2686b9a6e32ed3c80c3f63c6a2aff7f5e83c3e6"
    end
    on_intel do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.1.2/skuggsja_0.1.2_darwin_amd64.tar.gz"
      sha256 "597936e70f8f6ce8920b5c3d80fb003ae49ceffb72d076e22575e3615a3c7ebe"
    end
  end

  on_linux do
    on_arm do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.1.2/skuggsja_0.1.2_linux_arm64.tar.gz"
      sha256 "566d2096e20d1e13ff5b4aafa1b3cb79cdb31e94ba9b4631f38675357fc65f5a"
    end
    on_intel do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.1.2/skuggsja_0.1.2_linux_amd64.tar.gz"
      sha256 "f29df983315b3e966b57ba4828c3a9637ad4cde881a2e6b16e9a489d49fc36a0"
    end
  end

  def install
    bin.install "skuggsja"
    generate_completions_from_executable(bin/"skuggsja", "completion")
  end

  test do
    assert_equal "skuggsja #{version}", shell_output("#{bin}/skuggsja version").strip
    assert_match "--no-open", shell_output("#{bin}/skuggsja --help")
  end
end
