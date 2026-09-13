# frozen_string_literal: true

# Generated from the release checksums; do not edit download values manually.
# Skuggsja release version: 0.3.0
class Skuggsja < Formula
  desc "Local history retrospective for AI coding agents"
  homepage "https://github.com/0merUfuk/skuggsja"
  license "MIT"

  on_macos do
    on_arm do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.3.0/skuggsja_0.3.0_darwin_arm64.tar.gz"
      sha256 "f9bee0b4a24ac4798d52e2d3855455cd2da8d77025a851f18f0865e7185c003e"
    end
    on_intel do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.3.0/skuggsja_0.3.0_darwin_amd64.tar.gz"
      sha256 "e9295f7abc2ed6f944bdeb42314e52f974d4bd9893f8fa7c90fc12295f9c0011"
    end
  end

  on_linux do
    on_arm do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.3.0/skuggsja_0.3.0_linux_arm64.tar.gz"
      sha256 "2a4a6e871537497ea713e1a3feaf220f5158889b71cec25d88b10722b2b97d56"
    end
    on_intel do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.3.0/skuggsja_0.3.0_linux_amd64.tar.gz"
      sha256 "6c13a1e03aeaed15aea92b63abcd76fb63b9a20aba06a3789e65b1017d7cfc77"
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
