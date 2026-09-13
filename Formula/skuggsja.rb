# frozen_string_literal: true

# Generated from the release checksums; do not edit download values manually.
# Skuggsja release version: 0.2.1
class Skuggsja < Formula
  desc "Local history retrospective for AI coding agents"
  homepage "https://github.com/0merUfuk/skuggsja"
  license "MIT"

  on_macos do
    on_arm do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.2.1/skuggsja_0.2.1_darwin_arm64.tar.gz"
      sha256 "f4bf3da168362968fd73e4e63d46afc919b49d1fc0123a2b45e9e61356099995"
    end
    on_intel do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.2.1/skuggsja_0.2.1_darwin_amd64.tar.gz"
      sha256 "ee5debe98e34b0998ccc672c289f7f2b4c01e3057e73b7753738d5c61b3c4f8a"
    end
  end

  on_linux do
    on_arm do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.2.1/skuggsja_0.2.1_linux_arm64.tar.gz"
      sha256 "f622ef1029fe6b8b6ba6f4579d887ce026c9762f3d2446827fe9c9a0b256c935"
    end
    on_intel do
      url "https://github.com/0merUfuk/skuggsja/releases/download/v0.2.1/skuggsja_0.2.1_linux_amd64.tar.gz"
      sha256 "e6f40d1e3210c0f220b758b123a1cfffb094b5ef2ba6da1ffa723ff69c643c8b"
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
