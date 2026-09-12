# frozen_string_literal: true

# Generated from the release checksums; do not edit download values manually.
# Skuggsja release version: 0.2.0
class Skuggsja < Formula
  desc "Local history retrospective for AI coding agents"
  homepage "https://github.com/0merUfuk/skuggsja"
  license "MIT"

  on_macos do
    on_arm do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.2.0/skuggsja_0.2.0_darwin_arm64.tar.gz"
      sha256 "4f59f493e2dbe3a837c695f4137b4492ef9d65fa5c4b663c532d818f83e1528c"
    end
    on_intel do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.2.0/skuggsja_0.2.0_darwin_amd64.tar.gz"
      sha256 "4943a6f1a628f886c072f3a2fb2c757ff2fc913093b0d57138520cd2bcc2ad1a"
    end
  end

  on_linux do
    on_arm do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.2.0/skuggsja_0.2.0_linux_arm64.tar.gz"
      sha256 "b37e897a199517c81d81b76852bd2f8d35c110a2253dfadbaab292c40febeeb2"
    end
    on_intel do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.2.0/skuggsja_0.2.0_linux_amd64.tar.gz"
      sha256 "a8b5ff53563ccd531f45919d20882a9a38848442ac8c581cb0f84ad753e8f5ea"
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
