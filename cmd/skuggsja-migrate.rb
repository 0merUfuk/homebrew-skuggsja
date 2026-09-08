#!/usr/bin/env ruby
# frozen_string_literal: true

require "abstract_command"
require "json"
require "open3"
require "tab"
require "tap"
require "trust"
require "lock_file"
require_relative "../libexec/migration_helper"

module Homebrew
  module Cmd
    class SkuggsjaMigrate < AbstractCommand
      cmd_args do
        description "Check or explicitly repair Skuggsja receipts after the dedicated-tap move, without uninstalling."
        switch "--check", description: "Verify installed kegs and report proposed receipt changes (the default)."
        switch "--apply", description: "Back up receipts and change only verified old-origin source.tap fields."
        conflicts "--check", "--apply"
        named_args :none
      end

      def run
        old_tap = Tap.fetch(SkuggsjaMigration::OLD_TAP)
        new_tap = Tap.fetch(SkuggsjaMigration::NEW_TAP)
        unless old_tap.installed? && new_tap.installed?
          raise SkuggsjaMigration::Error, "Both canonical taps must already be installed; this command never downloads them"
        end
        arch = { "arm64" => "arm64", "aarch64" => "arm64", "x86_64" => "amd64", "amd64" => "amd64" }.fetch(Hardware::CPU.arch.to_s)
        system = OS.mac? ? "darwin" : (OS.linux? ? "linux" : nil)
        raise SkuggsjaMigration::Error, "Unsupported native platform" unless system
        repair = SkuggsjaMigration::Repair.new(
          cellar: HOMEBREW_CELLAR.realpath, old_tap: old_tap.path, new_tap: new_tap.path,
          manifest: Pathname(__dir__).parent/"libexec/migration-releases.json",
          backup_root: HOMEBREW_CACHE/"skuggsja-tap-migration",
          platform: [system, arch], lock: FormulaLock.new("skuggsja"),
          tab_factory: ->(content, path) { Tab.from_file_content(content, path) },
          destination_check: -> { destination_identity(new_tap) },
        )
        puts JSON.pretty_generate(repair.run(apply: args.apply?))
      rescue StandardError => error
        result = repair ? repair.failure(error) : { status: "failed", error: error.message,
                                                    changed_versions: [], backup_directory: nil }
        puts JSON.pretty_generate(result)
        Homebrew.failed = true
      end

      private

      def local_git(tap, *args)
        output, _error, status = Open3.capture3({ "GIT_NO_REPLACE_OBJECTS" => "1" },
                                              "git", "-C", tap.path.to_s, *args)
        raise SkuggsjaMigration::Error, "Cannot verify destination tap's committed local formula" unless status.success?
        output
      end

      def destination_identity(tap)
        unless Homebrew::Trust.trusted?(:formula, "#{SkuggsjaMigration::NEW_TAP}/skuggsja")
          raise SkuggsjaMigration::Error, "Trust the destination formula separately: brew trust --formula 0merUfuk/skuggsja/skuggsja"
        end
        path = tap.path/"Formula/skuggsja.rb"
        [path, path.parent, tap.path].each do |part|
          raise SkuggsjaMigration::Error, "Destination formula path must not contain symlinks" if part.symlink?
        end
        unless path.file? && path.size <= 65536
          raise SkuggsjaMigration::Error, "Destination formula must be an ordinary small file"
        end
        head = local_git(tap, "rev-parse", "HEAD").strip
        entry = local_git(tap, "ls-tree", "-z", head, "--", "Formula/skuggsja.rb")
        match = /\A100644 blob ([0-9a-f]{40}|[0-9a-f]{64})\tFormula\/skuggsja\.rb\x00\z/.match(entry)
        raise SkuggsjaMigration::Error, "Destination formula is not a committed regular file" unless match
        content = path.binread
        unless content == local_git(tap, "cat-file", "blob", match[1])
          raise SkuggsjaMigration::Error, "Destination formula differs from its committed local HEAD"
        end
        markers = content.scan(/^# Skuggsja release version: (.+)$/).flatten
        unless markers.length == 1 && SkuggsjaMigration::STABLE.match?(markers[0]) &&
               (markers[0].split(".").map(&:to_i) <=> [0, 1, 1]) >= 0
          raise SkuggsjaMigration::Error, "Destination formula must identify a stable Skuggsja release >=0.1.1"
        end
        version = markers[0]
        urls = content.scan(/^\s*url "([^"\r\n]+)"$/).flatten
        expected = %w[darwin linux].product(%w[amd64 arm64]).map do |os, architecture|
          "https://github.com/0merUfuk/skuggsja/releases/download/v#{version}/skuggsja_#{version}_#{os}_#{architecture}.tar.gz"
        end
        unless urls.length == 4 && urls.sort == expected.sort
          raise SkuggsjaMigration::Error, "Destination formula download identities do not match its stable release"
        end
        unless local_git(tap, "rev-parse", "HEAD").strip == head && path.binread == content
          raise SkuggsjaMigration::Error, "Destination formula changed during its local identity check"
        end
        { head: head, formula_sha256: Digest::SHA256.hexdigest(content), version: version }
      end
    end
  end
end
