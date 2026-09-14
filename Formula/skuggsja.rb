# frozen_string_literal: true

# Generated from the release checksums; do not edit download values manually.
# Skuggsja release version: 0.3.2
class Skuggsja < Formula
  desc "Local history retrospective for AI coding agents"
  homepage "https://github.com/0merUfuk/skuggsja"
  license "MIT"

  on_macos do
    on_arm do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.3.2/skuggsja_0.3.2_darwin_arm64.tar.gz"
      sha256 "ea3418a1199f1fdc2b60f1a06b5bcd4ad710d26cb1e232c7dd0dd0c92092b2cd"
    end
    on_intel do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.3.2/skuggsja_0.3.2_darwin_amd64.tar.gz"
      sha256 "7ed862fb4167287ddc084fce5e0414e4eaf5ea9978a22aa46b6febf62502d3df"
    end
  end

  on_linux do
    on_arm do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.3.2/skuggsja_0.3.2_linux_arm64.tar.gz"
      sha256 "6b11ba3e67474da2875f80599dd8e93fb23a86e8378bcf73068560d872209f80"
    end
    on_intel do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.3.2/skuggsja_0.3.2_linux_amd64.tar.gz"
      sha256 "c71e83ad88260fab474a430ea644da7740bcff9a3e75cb01c1b1f45465447295"
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
