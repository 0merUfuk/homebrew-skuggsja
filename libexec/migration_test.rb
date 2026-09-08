# frozen_string_literal: true

# Standalone offline tests: synthetic bytes, a fake Tab, no Homebrew subprocess.
require "json"
require "tmpdir"
require_relative "migration_helper"

module MigrationTests
  class FakeLock
    attr_accessor :refuse
    attr_reader :calls
    def initialize
      @calls = 0
    end
    def with_lock
      @calls += 1
      raise SkuggsjaMigration::Error, "lock held" if refuse
      yield
    end
  end

  class FakeTab
    def initialize(bytes, path, fixture)
      @json = JSON.parse(bytes)
      @path = path
      @fixture = fixture
    end
    def tap=(value)
      @json["source"]["tap"] = value
    end
    def to_json
      value = Marshal.load(Marshal.dump(@json))
      value.delete("extra") if @fixture.drop_field
      JSON.pretty_generate(value) + "\n"
    end
    def write
      @fixture.write_hook&.call(@path)
      mode = @path.stat.mode & 0o777
      staged = @path.dirname/"receipt-test-tmp"
      File.write(staged, to_json)
      staged.chmod(mode)
      staged.rename(@path)
    end
  end

  class Fixture
    attr_accessor :drop_field, :write_hook, :destination_hook
    attr_reader :root, :cellar, :rack, :old_tap, :new_tap, :manifest, :lock, :backup_root
    def initialize(root)
      @root = Pathname(root).realpath
      @cellar = @root/"Cellar"
      @rack = @cellar/"skuggsja"
      @old_tap = @root/"old-tap"
      @new_tap = @root/"new-tap"
      [@rack, @old_tap, @new_tap].each(&:mkpath)
      @backup_root = @root/"backups"
      @manifest = @root/"releases.json"
      File.write(@old_tap/"tap_migrations.json", JSON.generate("skuggsja" => "0merUfuk/skuggsja"))
      rows = %w[0.1.0 0.1.1].product(%w[darwin linux], %w[amd64 arm64]).map do |version, os, arch|
        { version: version, os: os, arch: arch, source_sha: "a" * 40,
          archive: "skuggsja_#{version}_#{os}_#{arch}.tar.gz", archive_sha256: "b" * 64,
          binary_sha256: Digest::SHA256.hexdigest(binary(version, os, arch)) }
      end
      File.write(@manifest, JSON.generate(repository: "0merUfuk/skuggsja", legacy_tap: SkuggsjaMigration::OLD_TAP,
                                          destination_tap: SkuggsjaMigration::NEW_TAP, releases: rows))
      @lock = FakeLock.new
      @destination = { head: "c" * 40, version: "0.1.1" }
      @destination_calls = 0
      File.write(@root/"report-canary", "synthetic report remains untouched\n")
      File.write(@root/"completion-canary", "completion remains untouched\n")
      (@root/"pin-canary").make_symlink("Cellar/skuggsja/0.1.0")
    end
    def binary(version, os = "linux", arch = "amd64")
      "synthetic test binary #{version} #{os} #{arch}\n"
    end
    def add(version, tap = SkuggsjaMigration::OLD_TAP)
      path = @rack/version
      (path/"bin").mkpath
      File.write(path/"bin/skuggsja", binary(version))
      (path/"bin/skuggsja").chmod(0o755)
      data = { "source" => { "tap" => tap, "path" => "historical/path.rb", "tap_git_head" => "d" * 40,
                              "versions" => { "stable" => version, "head" => nil } },
               "time" => 1234, "installed_on_request" => true, "extra" => { "keep" => [1, false, nil] } }
      File.write(path/"INSTALL_RECEIPT.json", JSON.pretty_generate(data) + "\n")
      data
    end
    def repair
      SkuggsjaMigration::Repair.new(cellar: @cellar, old_tap: @old_tap, new_tap: @new_tap,
        manifest: @manifest, backup_root: @backup_root, platform: %w[linux amd64], lock: @lock,
        tab_factory: ->(bytes, path) { FakeTab.new(bytes, path, self) },
        destination_check: lambda {
          @destination_calls += 1
          @destination_hook&.call(@destination_calls)
          @destination
        })
    end
    def receipt(version)
      @rack/version/"INSTALL_RECEIPT.json"
    end
    def snapshots
      @root.glob("**/*", File::FNM_DOTMATCH).reject { |p| p.directory? && !p.symlink? }.to_h do |p|
        s = p.lstat
        [p.relative_path_from(@root).to_s, [s.mode, s.size, s.mtime.to_r,
          p.symlink? ? p.readlink.to_s : Digest::SHA256.file(p).hexdigest]]
      end
    end
  end

  @passed = []
  def self.assert(value, message = "assertion failed")
    raise message unless value
  end
  def self.reject(pattern)
    begin
      yield
    rescue StandardError => error
      assert(pattern.match?(error.message), "unexpected error: #{error.message}")
      return
    end
    raise "expected rejection matching #{pattern}"
  end
  def self.test(name)
    Dir.mktmpdir("skuggsja-migration-test-") do |root|
      fixture = Fixture.new(root)
      yield fixture
    end
    @passed << name
  rescue StandardError => error
    warn JSON.pretty_generate(status: "FAIL", test: name, error: error.message, passed: @passed.length)
    raise
  end

  test("check is default, no backups, no lock, and no state writes") do |f|
    f.add("0.1.0"); f.add("0.1.1")
    before = f.snapshots
    result = f.repair.run
    assert(result[:status] == "ready" && result[:would_change_versions] == %w[0.1.0 0.1.1])
    assert(result[:changed_versions].empty? && result[:backup_directory].nil?)
    assert(f.lock.calls.zero? && f.snapshots == before)
  end

  test("apply changes only source.tap and keeps binaries, pin and canaries") do |f|
    old = %w[0.1.0 0.1.1].to_h { |v| [v, f.add(v)] }
    before = f.snapshots
    result = f.repair.run(apply: true)
    assert(result[:status] == "migrated" && result[:changed_versions] == old.keys && f.lock.calls == 1)
    old.each do |version, data|
      data["source"]["tap"] = SkuggsjaMigration::NEW_TAP
      assert(JSON.parse(f.receipt(version).read) == data)
      backup = Pathname(result[:backup_directory])/"#{version}.json"
      data["source"]["tap"] = SkuggsjaMigration::OLD_TAP
      assert(JSON.parse(backup.read) == data && (backup.stat.mode & 0o777) == 0o600)
    end
    after = f.snapshots
    before.each { |path, state| assert(after[path] == state, "non-receipt changed: #{path}") unless path.end_with?("INSTALL_RECEIPT.json") }
    assert((Pathname(result[:backup_directory]).stat.mode & 0o777) == 0o700)
  end

  test("mixed migrated and legacy receipts are supported") do |f|
    f.add("0.1.0"); f.add("0.1.1", SkuggsjaMigration::NEW_TAP)
    unchanged = f.receipt("0.1.1").binread
    result = f.repair.run(apply: true)
    assert(result[:changed_versions] == ["0.1.0"] && f.receipt("0.1.1").binread == unchanged)
  end

  test("repeated apply and check are idempotent without new backup") do |f|
    f.add("0.1.1"); f.repair.run(apply: true)
    before = f.snapshots
    %i[check apply].each do |mode|
      result = f.repair.run(apply: mode == :apply)
      assert(result[:status] == "up_to_date" && result[:changed_versions].empty? && result[:backup_directory].nil?)
    end
    assert(f.snapshots == before)
  end

  test("empty rack reports no installed versions without backups") do |f|
    result = f.repair.run(apply: true)
    assert(result[:status] == "up_to_date" && result[:installed_versions].empty? && !f.backup_root.exist?)
  end

  test("future already-new keg is preserved and labeled unverified") do |f|
    f.add("0.1.0"); f.add("0.1.2", SkuggsjaMigration::NEW_TAP)
    before = f.receipt("0.1.2").binread
    result = f.repair.run(apply: true)
    assert(result[:changed_versions] == ["0.1.0"] && result[:preserved_unverified_versions] == ["0.1.2"])
    assert(f.receipt("0.1.2").binread == before)
  end

  test("future-only installation remains idempotent") do |f|
    f.add("0.1.2", SkuggsjaMigration::NEW_TAP)
    before = f.snapshots
    result = f.repair.run(apply: true)
    assert(result[:status] == "up_to_date" && f.snapshots == before)
  end

  test("unknown legacy version rejects every write") do |f|
    f.add("0.1.0"); f.add("0.1.2")
    before = f.snapshots
    reject(/no verified legacy/) { f.repair.run(apply: true) }
    assert(f.snapshots == before)
  end

  test("known destination binary mismatch also rejects") do |f|
    f.add("0.1.1", SkuggsjaMigration::NEW_TAP)
    File.write(f.rack/"0.1.1/bin/skuggsja", "tampered")
    reject(/attested platform hash/) { f.repair.run(apply: true) }
    assert(!f.backup_root.exist?)
  end

  test("one tampered binary prevents all eligible writes") do |f|
    f.add("0.1.0"); f.add("0.1.1")
    File.write(f.rack/"0.1.1/bin/skuggsja", "tampered")
    before = f.snapshots
    reject(/attested platform hash/) { f.repair.run(apply: true) }
    assert(f.snapshots == before)
  end

  test("wrong source tap rejects instead of relabeling") do |f|
    f.add("0.1.1", "other/tap")
    reject(/unexpected tap or version/) { f.repair.run(apply: true) }
  end

  test("receipt version must match actual keg directory") do |f|
    f.add("0.1.1")
    data = JSON.parse(f.receipt("0.1.1").read); data["source"]["versions"]["stable"] = "0.1.0"
    f.receipt("0.1.1").write(JSON.generate(data))
    reject(/unexpected tap or version/) { f.repair.run(apply: true) }
  end

  test("symlinked receipt is not followed") do |f|
    f.add("0.1.1"); f.receipt("0.1.1").unlink
    f.receipt("0.1.1").make_symlink(f.root/"report-canary")
    reject(/ordinary file/) { f.repair.run(apply: true) }
  end

  test("symlinked binary is not followed") do |f|
    f.add("0.1.1"); p = f.rack/"0.1.1/bin/skuggsja"; p.rename(f.root/"outside-binary"); p.make_symlink(f.root/"outside-binary")
    reject(/ordinary file/) { f.repair.run(apply: true) }
  end

  test("symlinked keg is not followed") do |f|
    f.add("0.1.1"); p = f.rack/"0.1.1"; p.rename(f.root/"outside-keg"); p.make_symlink(f.root/"outside-keg")
    reject(/symlink or non-directory/) { f.repair.run(apply: true) }
  end

  test("invalid local mapping rejects") do |f|
    f.add("0.1.1"); (f.old_tap/"tap_migrations.json").write(JSON.generate("skuggsja" => "another/tap"))
    reject(/map skuggsja exactly/) { f.repair.run(apply: true) }
  end

  test("missing installed destination rejects") do |f|
    f.new_tap.rmdir
    reject(/No such file|non-directory/) { f.repair.run(apply: true) }
  end

  test("serialization changes to other fields reject before backup") do |f|
    f.add("0.1.1"); f.drop_field = true
    before = f.snapshots
    reject(/change fields other/) { f.repair.run(apply: true) }
    assert(f.snapshots == before)
  end

  test("advisory lock refusal prevents preflight writes") do |f|
    f.add("0.1.1"); f.lock.refuse = true; before = f.snapshots
    reject(/lock held/) { f.repair.run(apply: true) }
    assert(f.snapshots == before)
  end

  test("receipt mutation after preflight is not overwritten") do |f|
    f.add("0.1.1")
    f.destination_hook = ->(count) { f.receipt("0.1.1").write("external modification\n") if count == 2 }
    reject(/changed since preflight/) { f.repair.run(apply: true) }
    assert(f.receipt("0.1.1").read == "external modification\n" && !f.backup_root.exist?)
  end

  test("new keg membership prevents application") do |f|
    f.add("0.1.1")
    f.destination_hook = ->(count) { f.add("0.1.0") if count == 2 }
    reject(/keg set changed/) { f.repair.run(apply: true) }
    assert(!f.backup_root.exist?)
  end

  test("destination identity changes prevent application") do |f|
    f.add("0.1.1")
    f.destination_hook = ->(count) { raise SkuggsjaMigration::Error, "destination changed" if count == 2 }
    reject(/destination changed/) { f.repair.run(apply: true) }
    assert(!f.backup_root.exist?)
  end

  test("all backups exist before first atomic receipt write") do |f|
    f.add("0.1.0"); f.add("0.1.1")
    f.write_hook = lambda do |_path|
      assert(f.backup_root.glob("receipts-*/*.json").length == 3)
    end
    assert(f.repair.run(apply: true)[:changed_versions].length == 2)
  end

  test("partial failure reports exact completed and attempted versions") do |f|
    f.add("0.1.0"); f.add("0.1.1")
    f.write_hook = ->(path) { raise SkuggsjaMigration::Error, "simulated disk failure" if path.parent.basename.to_s == "0.1.1" }
    repair = f.repair
    begin
      repair.run(apply: true)
      raise "expected write failure"
    rescue SkuggsjaMigration::Error => error
      result = repair.failure(error)
      assert(result[:changed_versions] == ["0.1.0"] && result[:attempted_versions] == %w[0.1.0 0.1.1])
      assert(result[:application_may_have_started] && Pathname(result[:backup_directory]).directory?)
    end
    f.write_hook = nil
    assert(f.repair.run(apply: true)[:changed_versions] == ["0.1.1"])
  end

  test("malformed or duplicate manifest records reject") do |f|
    f.add("0.1.1"); data = JSON.parse(f.manifest.read); data["releases"][0] = data["releases"][1]
    f.manifest.write(JSON.generate(data))
    reject(/Duplicate release/) { f.repair.run(apply: true) }
  end

  puts JSON.pretty_generate(status: "PASS", tests: @passed.length, passed: @passed.length,
                            cases: @passed, network_calls: 0, real_homebrew_commands: 0)
end
