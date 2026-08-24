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

## 2. Build the wheel

Build into a scratch directory outside the repository. The tracked `dist/` folder collects older artifacts, and mixing versions there is how the wrong file gets attached to a message.

```bash
export PMCP_RELEASE_DIR=/tmp/proactive-mcp-alpha-$(date +%Y%m%d)
rm -rf "$PMCP_RELEASE_DIR"
uv build --out-dir "$PMCP_RELEASE_DIR"
ls -l "$PMCP_RELEASE_DIR"
```

`uv build` produces two files: a source distribution and a wheel. Note what it actually did, because it matters for trust:

```text
Building source distribution...
Building wheel from source distribution...
Successfully built .../proactive_mcp-<version>.tar.gz
Successfully built .../proactive_mcp-<version>-py3-none-any.whl
```

The wheel is built *from* the sdist, not straight from the working directory, so anything the sdist misses is missing from the wheel too. The filename carries `py3-none-any`, meaning pure Python with no platform or ABI pin, so one wheel serves Linux, macOS, and Windows testers. The version in the filename comes from `pyproject.toml` (`0.1.0` for the alpha). Hand over the `.whl` only. The tester has no use for the tarball, and shipping both invites them to install the wrong one.

## 3. Inspect the artifact before it leaves the machine

Never hand over a wheel you haven't opened. A wheel is a zip file, so listing it needs nothing but the standard library.

```bash
cd "$PMCP_RELEASE_DIR"
WHEEL=$(ls proactive_mcp-*-py3-none-any.whl)
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
rm -rf "$PMCP_RELEASE_DIR/wheel-unpacked"
python3 -m zipfile -e "$WHEEL" "$PMCP_RELEASE_DIR/wheel-unpacked"
gitleaks git --redact --log-opts="origin/main..HEAD"
gitleaks dir "$PMCP_RELEASE_DIR/wheel-unpacked" --redact
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

`entry_points.txt` must map `proactive-mcp` to `proactive_mcp.cli:entrypoint`, and `METADATA` must show `Requires-Python: >=3.11` plus the runtime dependencies from `pyproject.toml`. The `licenses/LICENSE` entry should be present as well.

## 4. Checksum

Compute the digest once, at the end of the build, and treat it as the artifact's identity from then on.

```bash
cd "$PMCP_RELEASE_DIR"
sha256sum proactive_mcp-*-py3-none-any.whl | tee SHA256SUMS
```

On macOS use `shasum -a 256`; on Windows PowerShell, `Get-FileHash -Algorithm SHA256`. Keep `SHA256SUMS` next to the wheel and record the same line in your delivery log (section 9). Send the digest over a different channel from the wheel, since a checksum that travels with the file it's supposed to protect proves nothing about tampering in transit.

## 5. Private handoff

The handoff has three separately delivered items when the Owner OAuth client is
used, and two when the tester is validating BYO.

**Package one, the wheel.** A single `.whl` file, sent over a private channel you both already use with real authentication behind it: a direct message, an encrypted chat, or a signed link that expires. No public link, no shared folder with guessable listing, no attachment on a GitHub issue.

**Package two, the checksum.** Send the SHA-256 digest through a different
authenticated channel from the wheel. A checksum that arrives beside the file
does not provide an independent transit-integrity check.

**Package three, the OAuth client JSON.** Per §12, alpha testers use the Owner's published-but-unverified OAuth client, so you deliver that installed-app client JSON as its own message, separate from the wheel and digest. Say plainly in that message:

- the file goes to `~/.proactive-mcp/client_secret.json` on Linux and macOS, or `%USERPROFILE%\.proactive-mcp\client_secret.json` on Windows,
- the directory should be mode `0700` and the file `0600` on POSIX,
- tokens and synced data stay on the tester's machine and are never sent to you,
- the consent screen will show an unverified-app warning, and the way through it is Advanced, then Continue,
- the file must not be committed, forwarded, or pasted anywhere.

One or two testers should instead be told to bring their own OAuth client,
following [`SETUP_GOOGLE.md`](SETUP_GOOGLE.md) from Step 1. That's how the new
OAuth onboarding guide gets validated rather than bypassed. Those testers get
the wheel and digest, but no JSON.

**The message itself.** Tell the tester the wheel filename, the commit you
built from, the target Python and uv versions, which separate channel carries
the digest, a pointer to section 6 below, and the one thing you want them to
report back. Do not repeat the digest on the wheel channel. Keep it short. §12
sets the bar at clean install to finished onboarding in fifteen minutes, and a
wall of text is the fastest way to miss it.

## 6. Tester: clean install

Paste this section to the tester. It needs no repository access, no `git`, and no package index.

Prerequisites are Python ≥3.11 and [uv](https://docs.astral.sh/uv/). Verify the file you received before you install it:

```bash
cd /path/to/downloads
sha256sum proactive_mcp-<version>-py3-none-any.whl
```

Compare that against the digest the Owner sent separately. If they differ, stop and say so; don't install it.

Create a dedicated virtual environment and install the wheel into it. A dedicated venv is what makes the rollback in section 8 trivial.

```bash
uv venv --python 3.11 ~/venvs/proactive
uv pip install --python ~/venvs/proactive/bin/python \
  /path/to/proactive_mcp-<version>-py3-none-any.whl
```

The installed console script lives at `~/venvs/proactive/bin/proactive-mcp` (Windows: `%USERPROFILE%\venvs\proactive\Scripts\proactive-mcp.exe`). Use that absolute path everywhere. Schedulers and agent config files get a minimal environment and can't expand `~` or find things on `PATH`.

## 7. Tester: verify the install

Five commands, and all five must exit `0`. Run them before touching any Google credential, because they prove the package landed correctly and separate packaging problems from OAuth problems.

```bash
BIN=~/venvs/proactive/bin/proactive-mcp
for cmd in "--help" "setup --help" "status --help" "serve --help" "daemon --help"; do
  $BIN $cmd >/dev/null 2>&1
  echo "$cmd -> $?"
done
```

Every line should end in `-> 0`. Confirm the version too:

```bash
~/venvs/proactive/bin/python -c \
  "import importlib.metadata as m; print(m.version('proactive-mcp'))"
```

Then the first real run. `status` prints JSON and works before any Google setup:

```bash
$BIN status
```

On a fresh machine it reports `"overall":"degraded"` with sources `not_configured`. That's the expected starting state, not a failure. From here, place the OAuth client JSON as instructed, run `$BIN setup` (add `--headless` on a box with no browser), and register the server with your agent following [`INTEGRATIONS.md`](INTEGRATIONS.md). Wheel installs skip `uv run` and `--directory` entirely: wherever that guide says `uv run --directory <checkout> proactive-mcp serve`, you run `~/venvs/proactive/bin/proactive-mcp serve`.

Report back: whether all five help commands exited `0`, the version string, the
redacted status fields listed in section 9 before and after `setup`, and how
long the whole thing took. Do not send the complete JSON document.

## 8. Rollback

Rollback has two levels, and which one you want depends on whether the local database should survive.

**Uninstall the package, keep your data.** Removes the code and the console script, leaves `~/.proactive-mcp/` untouched, so reinstalling picks up where you left off.

```bash
uv pip uninstall --python ~/venvs/proactive/bin/python proactive-mcp
```

If you registered a watcher daemon or a scheduled job, stop and remove that first, otherwise the scheduler keeps firing at a script that no longer exists. Also remove the `proactive` entry from your agent's MCP config.

**Full removal.** Remove the stored credential before deleting its authority
marker. A keyring-backed credential lives outside the state directory, and
deleting `~/.proactive-mcp/` first can make that stale keyring value look like a
legacy credential on reinstall.

```bash
~/venvs/proactive/bin/python -c \
  'from pathlib import Path; from proactive_mcp.sources.credentials import CredentialStore; CredentialStore(Path.home() / ".proactive-mcp").delete()'
rm -rf ~/.proactive-mcp
rm -rf ~/venvs/proactive
```

The credential-deletion command must exit `0` before you remove the state
directory. If it reports unavailable storage, leave the state directory and
its tombstone in place, revoke access in your Google Account permissions page,
and report the failure to the Owner. Deleting the venv while leaving
`~/.proactive-mcp/` in place is the useful middle ground when you want to
reinstall a newer wheel against the same database.

**Going back to a previous wheel.** Uninstall, then install the older `.whl` you kept. Migrations only move forward, so a database written by a newer build may not be readable by an older one. If you need to downgrade with data intact, copy `~/.proactive-mcp/` aside first and tell the Owner what you're doing.

## 9. Evidence capture

Keep this outside the repository. It contains delivery records, and none of it belongs in git.

For each build, record: the commit SHA and `git log -1 --oneline`, the `uv --version` and Python version, the wheel filename, the `SHA256SUMS` line, the file listing from section 3, and the results of `pytest`, `ruff`, and `basedpyright`. A transcript is enough:

```bash
mkdir -p ~/alpha-records
{
  git rev-parse HEAD
  uv --version
  uv run python -V
  sha256sum "$PMCP_RELEASE_DIR"/proactive_mcp-*.whl
  python3 -m zipfile -l "$PMCP_RELEASE_DIR"/proactive_mcp-*.whl
} > ~/alpha-records/build-$(date +%Y%m%d-%H%M).log
```

For each handoff, record the tester's name, the date, the wheel digest they got, which channel carried the wheel and which carried the JSON, whether they're on the Owner-client path or BYO, and the date access was revoked when the alpha ends.

For each tester report, record the five help exit codes, the version string,
and only these redacted status fields before and after setup: `overall`,
`database.status`, `database.migration_version`, each Google source `status`
and `error_code`, `daemon.status`, and warning strings. Do not retain or ask
for `database.path`, PID, or timestamps. Also record elapsed onboarding time
against the fifteen-minute bar from §12 and anything they got stuck on. The
stuck points are the valuable part; they're what the onboarding docs get fixed
from.

## Owner checklist

- [ ] Clean tree, commit SHA recorded
- [ ] `uv sync --locked` clean, tests and linters green on Python 3.11
- [ ] `uv build --out-dir` into a scratch directory outside the repo
- [ ] Wheel listing shows only `proactive_mcp/` and `dist-info`, with migrations and platform scripts present
- [ ] Filename scan, commit-range secret scan, and extracted-wheel content scan are clean
- [ ] `entry_points.txt` and `METADATA` correct
- [ ] `sha256sum` recorded in `SHA256SUMS` and in the delivery log
- [ ] Wheel sent privately to a named tester; digest sent on a separate channel
- [ ] OAuth client JSON sent as its own message, with placement, permissions, and no-commit warning
- [ ] `git status` clean, no OAuth JSON anywhere in the tree
- [ ] No PyPI publication, and no `uv publish` in the build history
- [ ] Tester confirmed five help commands at exit `0` plus a `status` run
- [ ] Rollback steps delivered with the install steps, not after the tester asks
