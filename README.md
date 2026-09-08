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

Keep The Matrix tap for its other tools. Prepare the new destination before
Homebrew processes the migration:

```sh
brew trust --formula 0merUfuk/skuggsja/skuggsja
brew tap 0merUfuk/skuggsja
brew update
```

Check every installed receipt, including retained older kegs:

```sh
for receipt in "$(brew --cellar skuggsja)"/*/INSTALL_RECEIPT.json; do
  printf '%s\n' "$receipt"
  cat "$receipt"
done
```

Automatic migration changes each receipt's `source.tap` to `0merufuk/skuggsja`
without replacing binaries or clearing pins. If an earlier update processed the
move before the destination was trusted, another update may have nothing to
replay. Qualified reinstall alone can retain the old receipt on current Homebrew.

For that case, use the dedicated receipt-recovery command:

```sh
brew trust --command 0merUfuk/skuggsja/skuggsja-migrate
HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_FROM_API=1 HOMEBREW_NO_ANALYTICS=1 \
  brew skuggsja-migrate --check
HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_FROM_API=1 HOMEBREW_NO_ANALYTICS=1 \
  brew skuggsja-migrate --apply
```

The environment flags suppress Homebrew startup updates, API refresh and analytics
for this offline recovery invocation. This one-time Homebrew command verifies
the original released executables before
changing only the old Matrix receipt's tap association. It preserves retained
kegs, binaries, completions and pins, backs up the original receipts, and refuses
unknown legacy binaries or concurrent Homebrew changes. It does not uninstall
Skuggsja, change application reports, read harness histories or make network
requests. Its writes are confined to Homebrew metadata and receipt backups;
the Skuggsja application remains read-only on source paths.

The command supports the v0.1.0 and v0.1.1 binaries distributed by the former tap.
A check leaves receipts and installed files unchanged; `--apply` is explicit
and safe to repeat after success.
Inspect the reported counts before considering all retained kegs migrated.
Normal future updates use `brew upgrade 0merUfuk/skuggsja/skuggsja`.

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

For tap maintenance, push a review branch, then request a bot-authored PR using
this same workflow with an empty tag:

```sh
gh workflow run update-skuggsja.yml --repo 0merUfuk/homebrew-skuggsja \
  --ref YOUR_REVIEW_BRANCH
```

The job checks the dispatched branch head and opens or reuses its PR. It does not
approve or merge it; the same checks and review requirements apply.

Report packaging failures in this tap's issues. For application behavior, source
coverage and privacy details, use the
[Skuggsja repository](https://github.com/0merUfuk/skuggsja).
