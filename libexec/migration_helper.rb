# frozen_string_literal: true

require "digest"
require "fileutils"
require "json"
require "pathname"
require "tmpdir"

module SkuggsjaMigration
  OLD_TAP = "0merufuk/thematrix"
  NEW_TAP = "0merufuk/skuggsja"
  STABLE = /\A(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\z/
  class Error < StandardError; end

  class Repair
    attr_reader :changed_versions, :backup_directory

    def initialize(cellar:, old_tap:, new_tap:, manifest:, backup_root:, platform:,
                   lock:, tab_factory:, destination_check:)
      @rack = Pathname(cellar)/"skuggsja"
      @old_tap = Pathname(old_tap)
      @new_tap = Pathname(new_tap)
      @manifest_path = Pathname(manifest)
      @backup_root = Pathname(backup_root)
      @platform = platform
      @lock = lock
      @tab_factory = tab_factory
      @destination_check = destination_check
      @changed_versions = []
      @attempted_versions = []
      @backup_directory = nil
    end

    def run(apply: false)
      @changed_versions = []
      @attempted_versions = []
      @backup_directory = nil
      if apply
        @lock.with_lock { perform(apply: true) }
      else
        perform(apply: false)
      end
    end

    def failure(error)
      { status: "failed", error: error.message, changed_versions: @changed_versions,
        backup_directory: @backup_directory,
        attempted_versions: @attempted_versions,
        application_may_have_started: !@attempted_versions.empty?,
        recovery: "Inspect the reported versions and backups; rerun after resolving the failure. Receipts are atomic individually, not as a batch." }
    end

    private

    def perform(apply:)
      context = inspect_context
      kegs = inspect_kegs(context[:releases])
      changing = kegs.select { |keg| keg[:old] }
      recheck(context, kegs)
      if apply && !changing.empty?
        backup(kegs)
        recheck(context, kegs)
        changing.each do |keg|
          recheck_context(context)
          assert_membership(kegs)
          assert_unchanged(keg[:receipt])
          assert_unchanged(keg[:binary])
          tab = prepared_tab(keg)
          # Match Homebrew's automatic tap migration: only the tap is reassigned.
          @attempted_versions << keg[:version]
          tab.write
          @changed_versions << keg[:version]
          after = snapshot(keg[:receipt][:path], bytes: true, limit: 262144)
          unless parse_json(after[:bytes], "written receipt") == keg[:expected_json]
            raise Error, "Written receipt changed fields other than source.tap: #{keg[:version]}"
          end
          unless after[:stat][2] == keg[:receipt][:stat][2]
            raise Error, "Receipt mode changed during application: #{keg[:version]}"
          end
          keg[:receipt] = after
        end
        recheck(context, kegs)
      end
      {
        status: changing.empty? ? "up_to_date" : (apply ? "migrated" : "ready"),
        installed_versions: kegs.map { |keg| keg[:version] },
        would_change_versions: changing.map { |keg| keg[:version] },
        changed_versions: @changed_versions,
        backup_directory: @backup_directory,
        verified_known_versions: kegs.select { |keg| keg[:known] }.map { |keg| keg[:version] },
        preserved_unverified_versions: kegs.reject { |keg| keg[:known] }.map { |keg| keg[:version] },
        local_mapping_validated: true,
        scope: "Local receipt tap metadata only. No download, reinstall, binary replacement, pin change, or release-publication verification.",
      }
    end

    def inspect_context
      directory!(@old_tap)
      directory!(@new_tap)
      mapping = snapshot(@old_tap/"tap_migrations.json", bytes: true, limit: 65536)
      map = parse_json(mapping[:bytes], "local tap migration map")
      unless map.is_a?(Hash) && map["skuggsja"]&.downcase == NEW_TAP
        raise Error, "Installed old tap must map skuggsja exactly to #{NEW_TAP}"
      end
      manifest = snapshot(@manifest_path, bytes: true, limit: 65536)
      records = release_records(parse_json(manifest[:bytes], "release hash manifest"))
      destination = @destination_check.call
      { mapping: mapping, manifest: manifest, destination: destination, releases: records }
    end

    def release_records(manifest)
      unless manifest.is_a?(Hash) && manifest["repository"] == "0merUfuk/skuggsja" &&
             manifest["legacy_tap"] == OLD_TAP && manifest["destination_tap"] == NEW_TAP
        raise Error, "Unexpected release hash manifest identity"
      end
      rows = manifest["releases"]
      unless rows.is_a?(Array) && rows.length == 8
        raise Error, "Release hash manifest must contain eight platform records"
      end
      records = {}
      rows.each do |row|
        unless row.is_a?(Hash) && %w[0.1.0 0.1.1].include?(row["version"]) &&
               %w[darwin linux].include?(row["os"]) && %w[amd64 arm64].include?(row["arch"]) &&
               /\A[0-9a-f]{64}\z/.match?(row["binary_sha256"].to_s) &&
               /\A[0-9a-f]{64}\z/.match?(row["archive_sha256"].to_s) &&
               /\A[0-9a-f]{40}\z/.match?(row["source_sha"].to_s) &&
               row["archive"] == "skuggsja_#{row['version']}_#{row['os']}_#{row['arch']}.tar.gz"
          raise Error, "Invalid release hash record"
        end
        key = [row["version"], row["os"], row["arch"]]
        raise Error, "Duplicate release hash record" if records.key?(key)
        records[key] = row
      end
      records
    end

    def versions
      return [] unless @rack.exist? || @rack.symlink?
      directory!(@rack)
      @rack.children.map do |path|
        version = path.basename.to_s
        raise Error, "Unexpected installed keg name: #{version}" unless STABLE.match?(version)
        directory!(path)
        version
      end.sort_by { |version| version.split(".").map(&:to_i) }
    end

    def inspect_kegs(records)
      versions.map do |version|
        receipt = snapshot(@rack/version/"INSTALL_RECEIPT.json", bytes: true, limit: 262144)
        data = parse_json(receipt[:bytes], "receipt for #{version}")
        source = data.is_a?(Hash) ? data["source"] : nil
        unless source.is_a?(Hash) && [OLD_TAP, NEW_TAP].include?(source["tap"]) &&
               source.dig("versions", "stable") == version
          raise Error, "Receipt has an unexpected tap or version: #{version}"
        end
        binary = snapshot(@rack/version/"bin/skuggsja")
        known = records[[version, *@platform]]
        if source["tap"] == OLD_TAP && known.nil?
          raise Error, "Old-origin keg has no verified legacy release hash: #{version}"
        end
        if known && binary[:sha256] != known["binary_sha256"]
          raise Error, "Installed binary does not match its attested platform hash: #{version}"
        end
        if known.nil? && (version.split(".").map(&:to_i) <=> [0, 1, 1]) != 1
          raise Error, "Unrecognized destination-origin keg version: #{version}"
        end
        expected = Marshal.load(Marshal.dump(data))
        expected["source"]["tap"] = NEW_TAP
        keg = { version: version, receipt: receipt, binary: binary, expected_json: expected,
                old: source["tap"] == OLD_TAP, known: !known.nil? }
        prepared_tab(keg) if keg[:old]
        keg
      end
    end

    def prepared_tab(keg)
      tab = @tab_factory.call(keg[:receipt][:bytes], keg[:receipt][:path])
      tab.tap = NEW_TAP
      unless parse_json(tab.to_json, "Homebrew Tab serialization") == keg[:expected_json]
        raise Error, "Homebrew Tab would change fields other than source.tap: #{keg[:version]}"
      end
      tab
    end

    def parse_json(bytes, label)
      JSON.parse(bytes)
    rescue JSON::ParserError, EncodingError
      raise Error, "Invalid JSON in #{label}"
    end

    def directory!(path)
      path = Pathname(path).expand_path
      path.ascend do |part|
        stat = part.lstat
        raise Error, "Directory path contains a symlink or non-directory: #{part}" unless stat.directory? && !stat.symlink?
      end
      path
    end

    def snapshot(path, bytes: false, limit: nil)
      path = Pathname(path)
      directory!(path.dirname)
      before = path.lstat
      raise Error, "Expected an ordinary file: #{path}" unless before.file? && !before.symlink?
      raise Error, "Metadata file is too large: #{path}" if limit && before.size > limit
      digest = Digest::SHA256.new
      content = bytes ? +"".b : nil
      flags = File::RDONLY | File::NOFOLLOW
      File.open(path, flags) do |stream|
        raise Error, "File changed while opening: #{path}" unless fingerprint(stream.stat) == fingerprint(before)
        while (chunk = stream.read(1024 * 1024))
          digest.update(chunk)
          content << chunk if bytes
          raise Error, "Metadata grew during read: #{path}" if limit && content.bytesize > limit
        end
        unless fingerprint(stream.stat) == fingerprint(before) && fingerprint(path.lstat) == fingerprint(before)
          raise Error, "File changed during read: #{path}"
        end
      end
      { path: path, stat: fingerprint(before), sha256: digest.hexdigest, bytes: content }
    end

    def fingerprint(stat)
      [stat.dev, stat.ino, stat.mode, stat.size, stat.mtime.to_r, stat.ctime.to_r]
    end

    def assert_unchanged(before)
      after = snapshot(before[:path])
      unless after[:stat] == before[:stat] && after[:sha256] == before[:sha256]
        raise Error, "File changed since preflight: #{before[:path]}"
      end
    end

    def assert_membership(kegs)
      raise Error, "Installed keg set changed during migration" unless versions == kegs.map { |keg| keg[:version] }
    end

    def recheck_context(context)
      assert_unchanged(context[:mapping])
      assert_unchanged(context[:manifest])
      raise Error, "Destination formula or trust changed during migration" unless @destination_check.call == context[:destination]
    end

    def recheck(context, kegs)
      recheck_context(context)
      assert_membership(kegs)
      kegs.each do |keg|
        assert_unchanged(keg[:receipt])
        assert_unchanged(keg[:binary])
      end
    end

    def backup(kegs)
      root = @backup_root.expand_path
      cellar = @rack.dirname.expand_path
      if root == cellar || root.to_s.start_with?(cellar.to_s + File::SEPARATOR)
        raise Error, "Receipt backups must be outside the Cellar"
      end
      directory!(root.parent)
      root.mkdir(0o700) unless root.exist? || root.symlink?
      directory!(root)
      raise Error, "Backup root must be private to its owner" unless (root.stat.mode & 0o077).zero?
      directory = Pathname(Dir.mktmpdir("receipts-", root))
      @backup_directory = directory.to_s
      rows = kegs.map do |keg|
        path = directory/"#{keg[:version]}.json"
        File.open(path, File::WRONLY | File::CREAT | File::EXCL, 0o600) do |stream|
          stream.write(keg[:receipt][:bytes])
          stream.flush
          stream.fsync
        end
        raise Error, "Receipt backup digest mismatch" unless Digest::SHA256.file(path).hexdigest == keg[:receipt][:sha256]
        { version: keg[:version], sha256: keg[:receipt][:sha256], original_path: keg[:receipt][:path].to_s }
      end
      File.open(directory/"manifest.json", File::WRONLY | File::CREAT | File::EXCL, 0o600) do |stream|
        stream.write(JSON.pretty_generate(rows) + "\n")
        stream.flush
        stream.fsync
      end
    end
  end
end
