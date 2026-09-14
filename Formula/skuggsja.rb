# frozen_string_literal: true

# Generated from the release checksums; do not edit download values manually.
# Skuggsja release version: 0.3.1
class Skuggsja < Formula
  desc "Local history retrospective for AI coding agents"
  homepage "https://github.com/0merUfuk/skuggsja"
  license "MIT"

  on_macos do
    on_arm do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.3.1/skuggsja_0.3.1_darwin_arm64.tar.gz"
      sha256 "05a50cd00554ef4c973897c9925e3ded95a374ca775e905a756968d97c1f2b96"
    end
    on_intel do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.3.1/skuggsja_0.3.1_darwin_amd64.tar.gz"
      sha256 "6f5334bfa24051601b3d7f8e379c22ecc9a25d81b0342c3f2323316525788644"
    end
  end

  on_linux do
    on_arm do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.3.1/skuggsja_0.3.1_linux_arm64.tar.gz"
      sha256 "645c5f538f918594b9f0277ec73395663ed97e4e66ea1e128b962c102582d51e"
    end
    on_intel do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.3.1/skuggsja_0.3.1_linux_amd64.tar.gz"
      sha256 "a3dbca9bacefded4a8639fb164716aafbdcf493144f062a589299b1bc3038f09"
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
