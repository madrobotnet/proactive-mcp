# Closed-alpha release: wheel build and tester handoff

This is the Owner's procedure for turning the current source tree into a wheel, checking that wheel, handing it to a named tester over a private channel, and getting a clean install or a clean rollback on the tester's machine. It assumes the tester has no access to the repository and no way to `pip install proactive-mcp` from an index.

Two audiences share the page. Sections 1 through 5 are yours, the Owner, on the build machine. Sections 6 through 8 are what you paste to the tester. Section 9 is the evidence you keep so a failed alpha install can be diagnosed later.

## Ground rules

Three rules come straight from [`PRODUCT_PLAN.md`](PRODUCT_PLAN.md) §10 and §12 and from [Issue #7](https://github.com/madrobotnet/proactive-mcp/issues/7). Breaking any of them ends the closed alpha early.

**No PyPI.** Publishing to PyPI makes the package public even while the repository is private, so the alpha ships as a file you hand over directly. Public release and PyPI publication happen together, later, and only on an explicit Owner decision. There is no `uv publish` step anywhere in this document, and there shouldn't be one in your shell history either.

**The OAuth client JSON never enters the repository.** Not in `docs/`, not in `dist/`, not pasted into an issue or a PR comment. It travels to the tester separately from the wheel, on a different channel, and it lands only in the tester's own state directory. Check `git status` before every commit during the alpha.

**One tester, one delivery record.** Each handoff is to a person you named in advance, with a recorded checksum. If you can't say who has a given wheel, you've lost control of the alpha.

## 1. Prepare the build machine

The build has to be reproducible in the sense that matters here: the wheel you hand over contains exactly the committed source, and you can say which commit. That means a clean tree.

```bash
cd /path/to/proactive-mcp
git status --porcelain      # must print nothing
git rev-parse HEAD          # record this commit
git log -1 --oneline
```

If `git status --porcelain` prints anything, stop and either commit it or stash it. A wheel built from a dirty tree can't be traced back to a commit, and when the tester reports a bug you won't know what they ran.

Pin the interpreter to the project floor rather than whatever the machine happens to have. `pyproject.toml` requires Python ≥3.11, so build and test against 3.11 to catch anything that only works on a newer runtime.

```bash
uv python install 3.11
uv --version
uv sync --locked
```

`uv sync --locked` is the check that `uv.lock` still matches `pyproject.toml`. It fails loudly if the lock is stale, which is exactly what you want before cutting an artifact. Run the test suite and the linters here too; the wheel isn't the place to discover a regression.

```bash
uv run pytest
uv run ruff check
uv run basedpyright
```

## 2. Build the Linux bundle

For a Linux tester, build the one supported offline bundle: CPython 3.11 on Linux aarch64. The bundle contains the project wheel, its resolved Linux aarch64 dependency wheels, and an internal `SHA256SUMS` manifest. Build it into an empty scratch directory outside the repository. The tracked `dist/` folder collects older artifacts, and mixing versions there is how the wrong file gets attached to a message.

```bash
export PMCP_RELEASE_DIR=/tmp/proactive-mcp-alpha-$(date +%Y%m%d)
rm -rf "$PMCP_RELEASE_DIR"
uv run scripts/build_alpha_bundle.py "$PMCP_RELEASE_DIR"
ls -l "$PMCP_RELEASE_DIR/proactive-mcp-alpha-linux-aarch64-py311.tar.gz"
```

The output is exactly `proactive-mcp-alpha-linux-aarch64-py311.tar.gz`. When extracted into `~/Downloads`, it creates the canonical tester directory `~/Downloads/proactive-mcp-alpha/`. Do not send an individual wheel, a separate wheelhouse, or a staging directory to a Linux tester.

The builder creates the project wheel from the sdist, resolves only locked CPython 3.11 manylinux aarch64 wheels, verifies every downloaded wheel against the lock, and writes `SHA256SUMS` for every wheel in the bundle. Inspect the archive before it leaves the machine:

```bash
tar -tzf "$PMCP_RELEASE_DIR/proactive-mcp-alpha-linux-aarch64-py311.tar.gz"
```

Expect only `proactive-mcp-alpha/`, its `wheels/` directory, `SHA256SUMS`, and `bundle-metadata.json`. The project wheel remains pure Python, but the dependency wheelhouse is intentionally Linux aarch64 and must not be sent to macOS or Windows testers.

## 3. Inspect the artifact before it leaves the machine

Never hand over a wheel you haven't opened. A wheel is a zip file, so listing it needs nothing but the standard library.

```bash
export PMCP_BUNDLE_INSPECT_DIR=$(mktemp -d)
tar -xzf "$PMCP_RELEASE_DIR/proactive-mcp-alpha-linux-aarch64-py311.tar.gz" \
  -C "$PMCP_BUNDLE_INSPECT_DIR"
export PMCP_BUNDLE_DIR="$PMCP_BUNDLE_INSPECT_DIR/proactive-mcp-alpha"
WHEEL=$(ls "$PMCP_BUNDLE_DIR"/wheels/proactive_mcp-*-py3-none-any.whl)
python3 -m zipfile -l "$WHEEL"
```

Four things to confirm in that listing.

*The package tree is there and nothing else is.* Every path should start with `proactive_mcp/` or `proactive_mcp-<version>.dist-info/`. `[tool.hatch.build.targets.wheel]` limits packaging to `src/proactive_mcp`, so `tests/`, `typings/`, and `docs/` should be absent.

*Non-Python runtime assets survived.* The delivery layer ships helper scripts and the store ships SQL, and a packaging mistake silently drops them because they aren't `.py` files:

```bash
python3 -m zipfile -l "$WHEEL" | grep -E '\.(sql|ps1|applescript)'
python3 -m zipfile -l "$WHEEL" | grep -c 'store/migrations/.*\.sql'
```

You should see the migration set, the Windows toast script, and the macOS notification script. Note that `python3 -m zipfile -l` pads each row with a timestamp and size after the path, so don't anchor these patterns with `$`. Zero migrations means a broken install on first run, not a build warning.

*No secrets and no local state.* Start with a filename scan:

```bash
python3 -m zipfile -l "$WHEEL" | grep -Ei 'secret|token|credential|\.json|\.db|\.env' \
  | grep -v '\.py '
```

Expect no output. The trailing filter drops source modules whose names
legitimately contain those words, such as the credentials handling module.
This is only a filename check; it cannot find a secret embedded in an ordinary
source file. Extract the wheel and run the same approved secret scanner used
for the release commit range:

```bash
rm -rf "$PMCP_BUNDLE_INSPECT_DIR/wheel-unpacked"
python3 -m zipfile -e "$WHEEL" "$PMCP_BUNDLE_INSPECT_DIR/wheel-unpacked"
gitleaks git --redact --log-opts="origin/main..HEAD"
gitleaks dir "$PMCP_BUNDLE_INSPECT_DIR/wheel-unpacked" --redact
```

Record the gitleaks version and configuration with the build evidence. Any
unreviewed finding from either scan is a stop-the-line event: delete the
artifact, remove the offending value from source, and start over from section
1. Synthetic fixtures may be allowlisted only after inspecting the exact hit.

*Metadata and entry point are intact.* The console script is what the tester actually runs.

```bash
python3 -m zipfile -l "$WHEEL" | grep dist-info
unzip -p "$WHEEL" 'proactive_mcp-*.dist-info/entry_points.txt'
unzip -p "$WHEEL" 'proactive_mcp-*.dist-info/METADATA' | head -20
```

`entry_points.txt` must map `proactive-mcp` to `proactive_mcp.cli:entrypoint`, and `METADATA` must show `Requires-Python: >=3.11` plus the runtime dependencies from `pyproject.toml`. The `licenses/LICENSE` entry should be present as well. Remove the inspection directory when this section is complete:

```bash
rm -rf "$PMCP_BUNDLE_INSPECT_DIR"
unset PMCP_BUNDLE_DIR PMCP_BUNDLE_INSPECT_DIR WHEEL
```

## 4. Two-channel integrity checks

The Linux bundle has two separate integrity checks. `SHA256SUMS` is inside the archive and verifies each staged wheel after extraction. The archive digest is sent separately and verifies the archive before extraction. Neither replaces the other. Extraction must start with no `~/Downloads/proactive-mcp-alpha/` path present; an agent stops instead of deleting or overlaying an existing directory. After extraction it also compares the files in `wheels/` with the paths in `SHA256SUMS` and stops if either side has an extra entry.

```bash
cd "$PMCP_RELEASE_DIR"
sha256sum proactive-mcp-alpha-linux-aarch64-py311.tar.gz
```

Record that archive digest in the delivery log. Send it through a different authenticated channel from the archive. The tester compares it before extracting, confirms the canonical extraction path does not already exist, then runs `sha256sum --check SHA256SUMS` and verifies manifest-to-directory parity inside the extracted bundle before any virtual-environment mutation. The agent reads `project_wheel` from `bundle-metadata.json` and installs that exact `wheels/<filename>` path, never the unpinned package name. `SHA256SUMS` stays inside the archive and is never the independently delivered archive digest.

## 5. Private handoff

The handoff has three separately delivered items when the Owner OAuth client is
used, and two when the tester is validating BYO.

**Package one, the Linux archive.** Send `proactive-mcp-alpha-linux-aarch64-py311.tar.gz` over a private channel you both already use with real authentication behind it: a direct message, an encrypted chat, or a signed link that expires. The Linux tester saves it as `~/Downloads/proactive-mcp-alpha-linux-aarch64-py311.tar.gz` and extracts it to a new `~/Downloads/proactive-mcp-alpha/`. If that path already exists, the agent stops and reports it instead of deleting or overlaying anything. No public link, no shared folder with guessable listing, no attachment on a GitHub issue.

**Package two, the archive checksum.** Send the archive SHA-256 digest through a different authenticated channel from the archive. A checksum that arrives beside the file does not provide an independent transit-integrity check. The bundle's `SHA256SUMS` is a second, internal check for its wheels.

**Package three, the OAuth client JSON.** Per §12, alpha testers use the Owner's published-but-unverified OAuth client, so you deliver that installed-app client JSON as its own message, separate from the package and archive digest. Say plainly in that message:

- the file goes to `~/.proactive-mcp/client_secret.json` on Linux and macOS, or `%USERPROFILE%\.proactive-mcp\client_secret.json` on Windows,
- the directory should be mode `0700` and the file `0600` on POSIX,
- tokens and synced data stay on the tester's machine and are never sent to you,
- the consent screen will show an unverified-app warning, and the way through it is Advanced, then Continue,
- the file must not be committed, forwarded, or pasted anywhere.

One or two testers should instead be told to bring their own OAuth client,
following [`SETUP_GOOGLE.md`](SETUP_GOOGLE.md) from Step 1. That's how the new
OAuth onboarding guide gets validated rather than bypassed. Those testers get
the wheel and digest, but no JSON. Send them `docs/SETUP_GOOGLE.md` alongside
the matching OS sheet so their agent has the guide without repository access.

**The message itself.** Tell the tester the wheel filename, the commit you
built from, the target Python and uv versions, which separate channel carries
the digest, a pointer to section 6 below, and the one thing you want them to
report back. Do not repeat the digest on the wheel channel. Keep it short. §12
sets the bar at clean install to finished onboarding in fifteen minutes, and a
wall of text is the fastest way to miss it.

## 6. Tester handoff sheet

Privately deliver the Linux archive and `docs/testers/linux.md` to a Linux aarch64 tester. The archive extracts to `~/Downloads/proactive-mcp-alpha/`. For Windows or macOS, privately deliver the wheel and the exact matching sheet from `docs/testers/windows.md` or `docs/testers/macos.md`.
The sheet is delivered alongside the Linux archive or the matching OS wheel as a separate document. It is not packaged into either artifact. Send the Linux archive checksum or matching wheel checksum over a separate authenticated channel, and send the Owner OAuth client JSON as its own separate message. BYO testers receive no Owner JSON. Send them `docs/SETUP_GOOGLE.md` alongside the matching OS sheet, outside the package artifact, so their agent can follow the guide while creating and placing their own client JSON.

The tester opens their existing agent and pastes that sheet's single text block
into the agent. They do not paste shell or PowerShell into a terminal. They do
not need a repository, `git`, a package index, `mcp add`, or manual MCP JSON.
The agent carries out the OS-specific handoff: checksum verification, Python and
uv checks, virtual environment creation, wheel installation, OAuth JSON
placement and permissions, MCP registration, Google read-only linking, a
confirmed real-account read, one-shot watch, and status verification. The
tester paste names outcomes, not CLI verbs; the agent looks those up from
`--help`, including the real-account confirm flag. It then installs and
verifies the host-specific session-start rule, a scheduled agent invocation
through `serve-scheduled`, and the platform-appropriate continuous watcher: Windows
Task Scheduler, a Linux user service, or a macOS LaunchAgent. A scheduler must
launch the agent so it can call `proactive_check` and conditionally
`confirm_delivery`; invoking the proactive-mcp CLI alone does not deliver a
situation.

If the checksum differs, the agent stops before installation and reports the
mismatch. The expected pre-setup status is `"overall":"degraded"` with sources
`not_configured`. The sheet directs the agent to use headless setup where the
machine has no browser. On Linux and macOS it also creates
`~/.proactive-mcp` with mode `0700`, installs `client_secret.json` with mode
`0600`, and verifies both permissions before setup.

## 7. Tester report

Within the fifteen-minute onboarding window, report success or failure, elapsed
onboarding time, and the blocked step, if any. Success includes a verified
session-start call, one scheduled agent delivery invocation, and a running
continuous watcher. If the watcher cannot be registered, the tester must first
receive an explanation that periodic sync and OS-notification fallback will be
unavailable and explicitly consent to degraded mode. Include only the redacted
status fields named in section 9 before and after setup. Do not send complete
status JSON, OAuth JSON, tokens, database paths, PIDs, or timestamps. The agent
must report a checksum mismatch without installing the wheel.

## 8. Rollback

The tester asks their existing agent to roll back using the matching OS sheet.
The agent first stops any watcher daemon or scheduled job, then deletes the
stored credential and confirms that deletion succeeded. Only after successful
credential deletion may it remove MCP registration, the state directory, the
virtual environment, or the wheel installation. This order is mandatory because
a keyring credential can outlive the state directory and appear to be a legacy
credential on reinstall.

For the supported Linux alpha bundle, the agent runs this credential-first
sequence. `set -e` makes credential deletion a hard stop: if `disconnect`
cannot delete the keyring or fallback credential, the state directory and its
tombstone remain intact.

```bash
set -euo pipefail
PROACTIVE_BIN="$HOME/venvs/proactive/bin/proactive-mcp"
"$PROACTIVE_BIN" service remove
"$PROACTIVE_BIN" disconnect
```

Only after the command prints `{"google":"disconnected"}` may the agent remove
the active MCP registrations for the client in use and any legacy Grok user-scope entries:

```bash
# Grok CLI: remove the project registrations from the two trusted directories.
if [ -d "$HOME/.proactive-mcp/grok-interactive" ]; then
  (cd "$HOME/.proactive-mcp/grok-interactive" && grok mcp remove --scope project proactive 2>/dev/null || true)
fi
if [ -d "$HOME/.proactive-mcp/grok-scheduled" ]; then
  (cd "$HOME/.proactive-mcp/grok-scheduled" && grok mcp remove --scope project proactive_scheduled 2>/dev/null || true)
fi
# Also clean legacy user-scope entries if they exist; absence is harmless here.
grok mcp remove --scope user proactive 2>/dev/null || true
grok mcp remove --scope user proactive_scheduled 2>/dev/null || true
# Restore any pre-install Claude/user registration from its backup rather than
# deleting unrelated settings.
```

```bash
# Codex CLI
codex mcp remove proactive
codex mcp remove proactive_scheduled
```

Then, and only then, it removes proactive-mcp-owned local state and install
artifacts. It must preserve every unrelated MCP profile and configuration.

```bash
rm -rf \
  "$HOME/.proactive-mcp" \
  "$HOME/venvs/proactive" \
  "$HOME/Downloads/proactive-mcp-alpha"
```

If credential storage is unavailable, the agent leaves the state directory and
its tombstone in place, revokes access in Google Account permissions, and
reports the failure to the Owner. For a downgrade, the agent uninstalls before
installing the older wheel. Migrations only move forward, so it copies the state
directory aside before a downgrade that must retain data.

## 9. Evidence capture

Keep this outside the repository. It contains delivery records, and none of it belongs in git.

For each Linux build, record: the commit SHA and `git log -1 --oneline`, the `uv --version` and Python version, the archive filename, its separately sent SHA-256 digest, the internal `SHA256SUMS` contents, the archive listing from section 2, and the results of `pytest`, `ruff`, and `basedpyright`. A transcript is enough:

```bash
mkdir -p ~/alpha-records
{
  git rev-parse HEAD
  uv --version
  uv run python -V
  sha256sum "$PMCP_RELEASE_DIR"/proactive-mcp-alpha-linux-aarch64-py311.tar.gz
  tar -xOf "$PMCP_RELEASE_DIR"/proactive-mcp-alpha-linux-aarch64-py311.tar.gz proactive-mcp-alpha/SHA256SUMS
  tar -tzf "$PMCP_RELEASE_DIR"/proactive-mcp-alpha-linux-aarch64-py311.tar.gz
} > ~/alpha-records/build-$(date +%Y%m%d-%H%M).log
```

For each handoff, record the tester's name, the date, the wheel digest they got, which channel carried the wheel and which carried the JSON, whether they're on the Owner-client path or BYO, and the date access was revoked when the alpha ends.

For each tester report, record success or failure, elapsed onboarding time
against the fifteen-minute bar from §12, and the blocked step, if any. Retain
only these redacted status fields before and after setup: `overall`,
`database.status`, `database.migration_version`, each Google source `status`
and `error_code`, and warning strings. Do not retain or ask for
`database.path`, PID, or timestamps. Record whether the session-start call,
scheduled agent invocation, and continuous watcher verification succeeded, or
whether the tester explicitly consented to degraded mode after the missing
periodic sync and OS-notification fallback were explained. The blocked steps
are the valuable part; they're what the onboarding docs get fixed from.

## Owner checklist

- [ ] Clean tree, commit SHA recorded
- [ ] `uv sync --locked` clean, tests and linters green on Python 3.11
- [ ] `uv run scripts/build_alpha_bundle.py "$PMCP_RELEASE_DIR"` completed into an empty scratch directory outside the repo
- [ ] Wheel listing shows only `proactive_mcp/` and `dist-info`, with migrations and platform scripts present
- [ ] Filename scan, commit-range secret scan, and extracted-wheel content scan are clean
- [ ] `entry_points.txt` and `METADATA` correct
- [ ] Archive SHA-256 recorded in the delivery log; internal `SHA256SUMS` covers every bundled wheel
- [ ] Linux aarch64 archive sent privately to a named tester; archive digest sent on a separate channel
- [ ] OAuth client JSON sent as its own message, with placement, permissions, and no-commit warning
- [ ] `git status` clean, no OAuth JSON anywhere in the tree
- [ ] No PyPI publication, and no `uv publish` in the build history
- [ ] POSIX OAuth directory/file permissions verified as `0700`/`0600`
- [ ] Session-start call and scheduled agent delivery invocation verified
- [ ] Continuous watcher verified, or degraded mode limitations explained and explicitly accepted
- [ ] Tester report records success or failure, elapsed onboarding time, and any blocked step
- [ ] Rollback steps delivered with the install steps, not after the tester asks
