# Skuggsja Homebrew tap

The official, dedicated Homebrew distribution for
[Skuggsja](https://github.com/0merUfuk/skuggsja), a local retrospective for AI
coding-agent histories. This tap contains only Skuggsja packaging.

## Install

On macOS or Linux with Homebrew installed:

```sh
brew install 0merUfuk/skuggsja/skuggsja
skuggsja version
skuggsja
```

On current Homebrew, trust the formula before adding the tap explicitly:

```sh
brew trust --formula 0merUfuk/skuggsja/skuggsja
brew tap 0merUfuk/skuggsja
brew install 0merUfuk/skuggsja/skuggsja
```

The formula installs the upstream binary and Bash, Zsh and Fish completions.
It does not require Go, Python or Node.js. macOS and Linux have separate arm64
and amd64 downloads with SHA-256 checksums. Intel macOS remains subject to
[Homebrew's support policy](https://docs.brew.sh/Support-Tiers).
Windows binaries and manual installation are available from the
[source releases](https://github.com/0merUfuk/skuggsja/releases).

## Update and remove

```sh
brew update
brew upgrade 0merUfuk/skuggsja/skuggsja
brew uninstall 0merUfuk/skuggsja/skuggsja
```

Uninstall removes the executable, retaining generated reports. Removing a report
is a separate application action. Homebrew installation and updates use the
network; Skuggsja's runtime remains local with no outbound requests.

## Moving from The Matrix tap

The formula and executable keep the same name. Existing installations do not
need to be uninstalled, and users of Matrix tools should retain their Matrix tap.
Homebrew can update installation receipts through the old tap's migration entry.
If the destination is not yet trusted or the receipt remains on the old tap:

```sh
brew update
brew trust --formula 0merUfuk/skuggsja/skuggsja
brew tap 0merUfuk/skuggsja
brew reinstall 0merUfuk/skuggsja/skuggsja
brew info --json=v2 0merUfuk/skuggsja/skuggsja
```

The `brew trust` command applies to current Homebrew versions with explicit tap
trust. Honor the installed Homebrew version's trust instructions. Reinstall
replaces the keg while retaining application data; it is not a package rename.
Pinned installations should remain pinned unless their owner chooses to upgrade.

## Release updates

The source repository publishes versioned archives, `checksums.txt` and
`skuggsja.rb`, with GitHub build-provenance attestations. This tap keeps no binary
copies. Its hourly importer checks stable releases and opens a candidate PR using
this repository's automatic `GITHUB_TOKEN`; it does not push formulas directly
to `main` and needs no cross-repository secret.

An authorized maintainer can request a specific published stable release:

```sh
gh workflow run update-skuggsja.yml --repo 0merUfuk/homebrew-skuggsja \
  --ref main -f tag=v0.1.1
```

The importer rejects failed provenance, rollback and modified contents under an
existing version. Candidate CI verifies the actual PR formula and checks native
macOS/Linux arm64/amd64 installation, upgrade, reinstall, removal, completions and
synthetic CLI use before protected-branch merge. The current release remains in
place if a candidate fails.

Bot-created PRs may require a maintainer to select **Approve workflows to run**.
The maintainer then reviews and merges the exact tested candidate. Formula
preparation is automated; approval and publication are deliberate gates.

Report packaging failures in this tap's issues. For application behavior, source
coverage and privacy details, use the
[Skuggsja repository](https://github.com/0merUfuk/skuggsja).
