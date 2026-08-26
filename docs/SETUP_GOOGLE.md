# Bring your own Google OAuth client

This guide takes you from an empty Google Cloud account to a working read-only
Gmail and Calendar connection on your own machine. You don't need to know
anything about this repository to follow it, and you never send a token or a
message to anyone else. Everything you create here lives in your Google
account and on your disk.

Budget about 15 minutes. Most of it is clicking through the Google Cloud
console; the local part is two commands.

**BYO is the default.** You create your own Desktop OAuth client. The PyPI
package never ships a project `client_secret.json`. Give the JSON path to
the agent you already use; the agent runs the local commands. You only
handle Google consent.

## Contents

- [What you're about to create](#what-youre-about-to-create)
- [Before you start](#before-you-start)
- [Step 1: create a Google Cloud project](#step-1-create-a-google-cloud-project)
- [Step 2: enable the two APIs](#step-2-enable-the-two-apis)
- [Step 3: configure the consent screen and add yourself as a test user](#step-3-configure-the-consent-screen-and-add-yourself-as-a-test-user)
- [Step 4: create the Desktop app OAuth client](#step-4-create-the-desktop-app-oauth-client)
- [Step 5: put the file where setup will look](#step-5-put-the-file-where-setup-will-look)
- [Step 6: run setup and grant consent](#step-6-run-setup-and-grant-consent)
- [Step 7: confirm it worked with status](#step-7-confirm-it-worked-with-status)
- [Optional: prove a real read with google-smoke](#optional-prove-a-real-read-with-google-smoke)
- [Keeping the client secret somewhere else](#keeping-the-client-secret-somewhere-else)
- [Machines with no browser](#machines-with-no-browser)
- [Reauthorizing](#reauthorizing)
- [Safety invariants](#safety-invariants)
- [Troubleshooting](#troubleshooting)

## What you're about to create

An **OAuth client** is the identity your local copy of proactive-mcp presents
to Google when it asks you for permission. You create one of type **Desktop
app**. Google issues a client ID and a client secret in a small JSON file, you
save that file locally, and `proactive-mcp setup` uses it to run a browser
consent flow against your own Google account.

Two scopes get requested, and only these two:

- `https://www.googleapis.com/auth/gmail.readonly`
- `https://www.googleapis.com/auth/calendar.readonly`

Both are read-only. V1 requests no write scope at all, and the credential
store refuses to save a credential whose scopes aren't exactly these two, so a
misconfigured client fails loudly instead of quietly getting more access than
it should.

One warning about vocabulary. A Desktop app client's "client secret" isn't a
server secret in the usual sense; Google expects it to ship inside installed
applications. Treat it as private anyway. It identifies your Cloud project,
and anyone holding it can burn through your project's API quota.

## Before you start

- A Google account. Use the one whose Gmail and Calendar you want watched.
- Access to a browser you control, on any machine.
- proactive-mcp installed. If you haven't done that yet, do
  [the installation command shapes in the integration guide](INTEGRATIONS.md#installation-command-shapes)
  first, then come back.

Every command below uses the public `uvx` shape from
[`docs/INTEGRATIONS.md`](INTEGRATIONS.md):

```bash
uvx proactive-mcp <command>
```

A development checkout substitutes
`uv run --directory /home/you/src/proactive-mcp proactive-mcp <command>`.
The
[installation command shapes in the integration guide](INTEGRATIONS.md#installation-command-shapes)
spell out that translation.

Verify your install answers before you touch the Google console:

```bash
uvx proactive-mcp --help
```

## Step 1: create a Google Cloud project

Go to <https://console.cloud.google.com/>, sign in with the account you want
watched, and create a new project from the project picker in the top bar.

Name it something you'll recognize in six months, like `proactive-mcp-personal`.
A fresh project is worth the extra minute: it keeps this integration's API
enablement, quota, and consent configuration away from anything else you've
built, and deleting the project later revokes everything in one action.

If Google asks you to pick an organization or a billing account, "No
organization" is fine and no billing account is needed. Gmail and Calendar
read access at one person's volume sits inside the free quota.

Make sure the project picker shows your new project before continuing. Every
remaining step applies to the selected project, and configuring the wrong one
is the single most common way this setup goes sideways.

## Step 2: enable the two APIs

Your project can only call APIs you've turned on. Enable exactly two.

1. Open **APIs & Services** then **Library**, or go straight to
   <https://console.cloud.google.com/apis/library>.
2. Search for **Gmail API**, open it, click **Enable**.
3. Search for **Google Calendar API**, open it, click **Enable**.

Don't enable anything else. If you skip one of them, `setup` still completes
and the consent screen still appears, because enablement is checked at read
time rather than at authorization time. The failure shows up later as a broken
sync, which is a confusing place to debug. Turn both on now.

## Step 3: configure the consent screen and add yourself as a test user

This is the fiddliest part, and it's where Google's own labels drift most.
Depending on when you read this, the pages live under **APIs & Services** then
**OAuth consent screen**, or under **Google Auth Platform** with the work split
across **Branding**, **Audience**, and **Data access**. The functions are the
same, so match by function rather than by heading.

1. **User type: External.** "Internal" only exists for Google Workspace
   organizations and is invisible on a personal account. External sounds
   alarming and isn't: publishing status, which you'll leave at Testing,
   controls who can actually authorize.
2. **App name and support email.** Pick a name you'll recognize on the consent
   screen, such as `proactive-mcp (local)`. Your own address for support and
   developer contact is fine. Nobody else will see any of it.
3. **Scopes.** You can add `gmail.readonly` and `calendar.readonly` here, or
   leave the list empty. Either works. `setup` requests both scopes explicitly
   at authorization time, and the scope list on this page doesn't restrict what
   an app in Testing may request. Adding them makes the consent screen's
   wording more accurate, so it's a small nicety, not a requirement.
4. **Test users: add your own address.** This one matters. While publishing
   status is **Testing**, only accounts on the test user list can authorize the
   app, and everyone else gets `Error 403: access_denied`. Add the exact
   account whose Gmail and Calendar you want watched. If you plan to connect
   two accounts, list both.
5. **Leave publishing status at Testing.** Don't click "Publish app" and don't
   request verification. Verification exists for apps serving strangers. You're
   the only user, and Google's flow for a personal read-only integration is
   exactly this one.

Testing mode has one real cost, so plan for it: **refresh tokens issued by an
app in Testing status expire after seven days.** When that happens, `status`
tells you the sources need reauthentication and you re-run `setup --reauth`.
See [Reauthorizing](#reauthorizing). It's a weekly nuisance while the app stays in Testing,
not a bug in this tool.

## Step 4: create the Desktop app OAuth client

1. Open **APIs & Services** then **Credentials**, or the **Clients** page under
   Google Auth Platform.
2. Click **Create credentials** then **OAuth client ID**.
3. Set **Application type** to **Desktop app**. This is not optional. `setup`
   parses an installed-app client file and rejects a Web application client
   outright, because a web client's redirect handling doesn't fit a local
   loopback flow.
4. Name it anything, `proactive-mcp desktop` for instance, and click **Create**.
5. In the dialog that follows, click **Download JSON**. You'll get a file named
   something like `client_secret_<long-string>.apps.googleusercontent.com.json`
   in your downloads folder.

You don't need to configure a redirect URI. Desktop app clients accept
loopback redirects automatically, and `setup` binds `http://127.0.0.1` on a
port the operating system picks per run.

Keep the browser tab open until the local flow succeeds. If you lose the file
you can download it again from the client's detail page; if you lose the
secret itself, delete the client and make a new one.

## Step 5: put the file where setup will look

`setup` resolves the client secret path in a fixed order, and stops at the
first one that's set:

1. `--client-secrets PATH`, when you pass it on the command line.
2. The `PROACTIVE_GOOGLE_CLIENT_SECRETS` environment variable.
3. Default: `client_secret.json` beside the state database.

The default is the simplest, so start there. On Linux and macOS:

```bash
mkdir -p ~/.proactive-mcp
chmod 700 ~/.proactive-mcp
mv ~/Downloads/client_secret_*.apps.googleusercontent.com.json \
  ~/.proactive-mcp/client_secret.json
chmod 600 ~/.proactive-mcp/client_secret.json
```

On Windows the equivalent location is
`%USERPROFILE%\.proactive-mcp\client_secret.json`. `setup` reads this input
file but does not harden it, so restrict it before setup. In PowerShell:

```powershell
$dir = Join-Path $env:USERPROFILE ".proactive-mcp"
$client = Join-Path $dir "client_secret.json"
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Move-Item (Join-Path $env:USERPROFILE "Downloads\client_secret.json") $client
icacls $client /inheritance:r /grant:r "*${sid}:(F)"
Get-Acl $client | Format-List Owner, AreAccessRulesProtected, Access
```

`AreAccessRulesProtected` must be `True`, and the only Allow identity must be
your current-user SID. Stop before setup if a broad identity such as
`Everyone`, `Users`, or `Authenticated Users` remains.

`0600` on the client secret and `0700` on the directory are the expectation
this project holds you to, but `setup` doesn't verify the mode and won't
complain if you skip it. Set it anyway. Anything else leaves your Cloud
project's credentials readable by other local accounts. The credential
proactive-mcp writes for itself is a different story: that one it creates with
owner-only permissions itself, and it refuses to write at all if it can't.

If you've pointed `PROACTIVE_DATABASE` at a non-default location, the default
client secret path moves with it, since it's always `client_secret.json` in the
database's own directory. Keep that directory private too.

## Step 6: run setup and grant consent

```bash
uvx proactive-mcp setup
```

Confirm the flags yourself first if you like, with
`proactive-mcp setup --help`:

```text
usage: proactive-mcp setup [-h] [--reauth] [--headless]
                           [--client-secrets PATH]

options:
  -h, --help            show this help message and exit
  --reauth              replace the current Google authorization
  --headless            do not launch a browser for loopback authorization
  --client-secrets PATH
                        path to an installed-app OAuth client secret file
```

A browser tab opens on Google's account chooser. Then:

1. **Pick the right account.** It has to be one you added as a test user in
   step 3. Picking a different account is the usual cause of `access_denied`.
2. **Get past the unverified app warning.** Because your app is in Testing and
   hasn't been verified, Google shows "Google hasn't verified this app". Click
   **Advanced**, then **Go to <your app name> (unsafe)**. That wording is
   Google telling you it can't vouch for a developer it doesn't know. Here the
   developer is you, the client is yours, and the code asking for access is on
   your own machine.
3. **Grant both permissions.** You'll see a request to view your email messages
   and settings, and one to view your calendars. Leave both checked and
   continue. Unchecking either makes the granted scopes differ from what's
   required, the credential is rejected, and you'll have to start over.
4. **Wait for the local handoff.** Your browser lands on a local page saying
   authentication is complete. That page is served by the short-lived loopback
   server `setup` started on `127.0.0.1`.

You have **five minutes** from the moment `setup` starts. Past that the
loopback server gives up with `Google authorization timed out; run setup
again`, and nothing is saved. Just run it again.

Success is one line on stdout:

```text
Google read-only sources configured.
```

The token now lives in your OS keyring. On a machine with no usable keyring,
typically a headless Linux box, it falls back to
`~/.proactive-mcp/credentials/google-readonly-oauth.json` at mode `0600`
inside a `0700` directory. Either way it never leaves your machine, and it
isn't in the SQLite database.

## Step 7: confirm it worked with status

`status` prints a JSON document and takes no flags:

```text
usage: proactive-mcp status [-h]

options:
  -h, --help  show this help message and exit
```

Run it:

```bash
uvx proactive-mcp status
```

Before `setup`, both sources report `not_configured` and the warnings tell you
what to do:

```json
{
  "google": {
    "gmail":    {"status": "not_configured", "error_code": null},
    "calendar": {"status": "not_configured", "error_code": null}
  },
  "warnings": [
    "Google Gmail is not configured; run proactive-mcp setup.",
    "Google Calendar is not configured; run proactive-mcp setup.",
    "Daemon has never run; OS notification fallback is unavailable."
  ]
}
```

After a successful `setup`, both flip to `never_synced`:

```json
{
  "google": {
    "gmail":    {"status": "never_synced", "error_code": null},
    "calendar": {"status": "never_synced", "error_code": null}
  },
  "warnings": [
    "Google Gmail has not completed a read sync.",
    "Google Calendar has not completed a read sync.",
    "Daemon has never run; OS notification fallback is unavailable."
  ]
}
```

**That `not_configured` to `never_synced` transition is the success signal for
this guide.** Both fields have to move. If only one did, something is wrong,
because a single credential backs both sources.

Don't be alarmed that `overall` still says `degraded`. `never_synced` is an
honest complaint: authorization is done, but nothing has read anything yet.
The watcher daemon is what performs routine local reads, and setting it up is a
separate task covered in
[the integration guide](INTEGRATIONS.md#watcher-daemon-and-degraded-mode). The
daemon performs local sync/evaluation/queue work only; it never launches an
agent/model or sends a prompt. Once it has run a pass, these statuses become
`ok` and `overall` goes to `ok` too, assuming nothing else is complaining.

If you'd rather read the JSON without squinting, pipe it through a formatter:

```bash
uvx proactive-mcp status \
  | python3 -m json.tool
```

## Optional: prove a real read with google-smoke

`status` proves you're authorized. It doesn't prove Google will actually serve
your reads, which is where a forgotten API enablement from step 2 shows up.
One command settles it, and it reads your real mailbox and calendar, so it
insists you say so out loud:

```text
usage: proactive-mcp google-smoke [-h] [--confirm-real-account-read]

options:
  -h, --help            show this help message and exit
  --confirm-real-account-read
                        confirm that this command may read the configured
                        Gmail and Calendar account
```

```bash
uvx proactive-mcp google-smoke \
  --confirm-real-account-read
```

Without that flag it refuses, exits `2`, and says `error: Google read smoke
requires explicit opt-in`. With it, you get counts and error codes and no
content whatsoever:

```json
{
  "gmail": {"count": 3, "error_code": null},
  "calendar": {"count": 1, "error_code": null},
  "credential_cleanup_failed": false
}
```

Non-null `error_code` values are where to look next. An error on one source and
not the other almost always means you enabled one API in step 2 and forgot the
second.

## Keeping the client secret somewhere else

Not everyone wants credentials in `~/.proactive-mcp`. Two overrides exist,
listed here in the same precedence order `setup` applies.

**Per run, with a flag.** Highest priority, and it wins over the environment
variable:

```bash
uvx proactive-mcp setup \
  --client-secrets /home/you/secrets/proactive-client.json
```

**Per shell or per service, with an environment variable.** Used only when
`--client-secrets` isn't passed:

```bash
export PROACTIVE_GOOGLE_CLIENT_SECRETS=/home/you/secrets/proactive-client.json
uvx proactive-mcp setup
```

In PowerShell:

```powershell
$env:PROACTIVE_GOOGLE_CLIENT_SECRETS = 'C:\Users\you\secrets\proactive-client.json'
```

Whichever you choose, keep the file at `0600` in a `0700` directory you own.
The path is only consulted during `setup`; once you're authorized, the client
secret isn't read again, so you can move it to offline storage between
authorizations if you're careful about being able to find it again before your
seven-day token expiry.

## Machines with no browser

`--headless` tells `setup` not to launch a browser. It changes nothing else:
the loopback server still runs on `127.0.0.1` on the host where you ran the
command, and the redirect still has to reach it. So `setup` prints the URL and
waits for you.

```bash
uvx proactive-mcp setup --headless
```

```text
oauth.authorization_url https://accounts.google.com/o/oauth2/auth?response_type=code&client_id=...&redirect_uri=http%3A%2F%2F127.0.0.1%3A55787%2F&scope=...
```

Opening that URL on your laptop and expecting it to work is the trap. The
redirect points at `127.0.0.1`, meaning your laptop's own loopback, where
nothing is listening. You need the port forwarded. Note the port in
`redirect_uri` (`55787` above, different every run), then in a second terminal
on your laptop:

```bash
ssh -N -L 55787:127.0.0.1:55787 you@remote-host
```

Now open the printed URL in your laptop's browser, complete consent, and the
redirect travels through the tunnel to the remote loopback server. You still
have only five minutes total from when `setup` started, so read the port
promptly. If you miss the window, `setup` reports the timeout and you retry.

For a remote host you reach through something other than SSH, any mechanism
that makes your browser's `127.0.0.1:<port>` land on that host's loopback will
do. Don't try to work around it by rewriting the redirect to a public
hostname: Desktop app clients only accept loopback redirects, and Google will
reject anything else.

## Reauthorizing

`--reauth` replaces the authorization you already have. It forces Google's
consent prompt again instead of silently reusing your prior grant, then
overwrites the stored credential.

```bash
uvx proactive-mcp setup --reauth
```

Reach for it when:

- **`status` says so.** `"status": "needs_reauth"` on either source comes with
  the warning `Google Gmail requires reauthentication; run proactive-mcp setup
  --reauth.` This is what a seven-day Testing-mode token expiry looks like from
  the outside.
- **You revoked access** at <https://myaccount.google.com/permissions>, whether
  deliberately or by clearing out old app grants.
- **You replaced the OAuth client**, for example after deleting and recreating
  it, or after moving to a different Cloud project.
- **You want to switch accounts.** `--reauth` shows the account chooser again.
  Make sure the new account is on your test user list first.

Everything else works the same as [step 6](#step-6-run-setup-and-grant-consent),
including the unverified app warning, and `--reauth` combines with `--headless`
and `--client-secrets` freely. Confirm with `status` afterward: both sources
should leave `needs_reauth` behind.

## Safety invariants

These hold for V1, and they're the reason this setup is narrow.

- **Read-only, exactly two scopes.** `gmail.readonly` and `calendar.readonly`,
  nothing more. No write scope is ever requested, and a credential with any
  other scope set is refused rather than stored.
- **Your credentials stay yours.** The token goes to your OS keyring, or to a
  `0600` file under `~/.proactive-mcp/credentials/` when no keyring is
  available. Nothing is uploaded, and with BYO the OAuth client is in your
  Cloud project, so no third party can even see the grant.
- **No secrets in this repository.** Real client IDs, client secrets, tokens,
  and personal data never go into the checkout, tests, or CI. Don't commit
  `client_secret.json`, and don't paste one into an issue or a chat log.
- **Errors don't leak.** Failure messages are deliberately vague about
  credential contents. `Google installed-app client configuration is invalid`
  is all you get about a bad file, precisely so a stack trace never carries
  your client secret into a log.
- **No PII in logs or error reports.** The local SQLite database intentionally
  stores the bounded situation evidence needed for deterministic detection.
  Logs and error reports carry only redacted structure, IDs, states, and
  counts. `google-smoke` reports counts and error codes and never a subject
  line, address, or event title.
- **Reads happen only when you ask.** `setup` authorizes and stores; it reads
  no mail. `google-smoke` reads your real account only with
  `--confirm-real-account-read`. Routine syncing is the watcher daemon's job,
  and you register that separately. It does not initiate a host conversation or
  agent delivery.

## Troubleshooting

Every message below is what the tool or Google actually prints. CLI errors go
to stderr and exit with status `2`.

| What you see | What it means and what to do |
| --- | --- |
| `error: Google installed-app client configuration is invalid` | The file at the resolved path is missing, unreadable, not JSON, or not a Desktop app client. Check that the path you expect is the one in effect, per the precedence in [step 5](#step-5-put-the-file-where-setup-will-look). A Web application client fails here too: recreate it as **Desktop app**. The message stays vague on purpose so nothing secret reaches your terminal history. |
| `error: Google authorization timed out; run setup again` | You didn't finish consent within five minutes, or a headless redirect never reached the loopback server. Nothing was saved. Run `setup` again, and for headless see [Machines with no browser](#machines-with-no-browser). |
| `error: Google authorization scopes are not the required read-only scopes` | A permission checkbox got unchecked on the consent screen, so the grant doesn't match `gmail.readonly` plus `calendar.readonly` exactly. Run `setup --reauth` and leave both checked. |
| `error: Google authorization did not provide a refresh token` | Google returned an access token with nothing durable behind it, which usually means an existing grant was reused. `setup --reauth` forces a fresh consent and a new refresh token. |
| `error: Google credential storage is unavailable` | The keyring is present but broken, or the fallback file can't be written. On a desktop Linux box, an unlocked login keyring is the usual fix. Otherwise check that `~/.proactive-mcp` exists, is yours, and is writable. |
| `error: Google credentials are missing; run proactive-mcp setup` | `google-smoke` found no stored credential. Complete [step 6](#step-6-run-setup-and-grant-consent) first. |
| `error: Google read smoke requires explicit opt-in` | You ran `google-smoke` without `--confirm-real-account-read`. That's the guard working. |
| `Error 403: access_denied` in the browser | The account you chose isn't on the test user list, or you picked a different account than you intended. Add the exact address under test users in [step 3](#step-3-configure-the-consent-screen-and-add-yourself-as-a-test-user), then retry. Changes there take effect immediately. |
| `Error 400: redirect_uri_mismatch` in the browser | Your client isn't a Desktop app client. Create a Desktop app client and point `setup` at the new JSON. |
| "Google hasn't verified this app" | Expected, and not an error. **Advanced**, then **Go to <your app> (unsafe)**. Your app is in Testing and unverified by design. |
| `status` still shows `not_configured` after a successful `setup` | Almost always two different state directories. `setup` and `status` both honor `PROACTIVE_DATABASE`, so run them with the same environment. Compare the `database.path` in the `status` JSON against where you expected state to live. |
| `status` shows `needs_reauth` out of nowhere | A Testing-mode refresh token hit its seven-day expiry, or you revoked access. Run `setup --reauth`. See [Reauthorizing](#reauthorizing). |
| `google-smoke` returns `count: 0` with `error_code: null` | Not a failure. The read worked and found nothing in range. An empty test mailbox does this. |
| `google-smoke` reports an error code for one source only | You enabled one API in [step 2](#step-2-enable-the-two-apis) and not the other. Enable both, then retry. |
| `credential_cleanup_failed: true` | A stale credential copy couldn't be removed. Re-run `setup --reauth` to write a clean one. |

Still stuck? Note the exact command, the exact error line, and the `status`
output, then report those. **Never** attach `client_secret.json`, the
credential file under `~/.proactive-mcp/credentials/`, or a full consent URL,
since the URL carries your client ID.

## Where to go next

You're authorized, but nothing is watching yet. Register proactive-mcp with
your agent and set up the local-only watcher daemon using
[`docs/INTEGRATIONS.md`](INTEGRATIONS.md). The product decisions
behind the read-only, BYO design are in
[`docs/PRODUCT_PLAN.md`](PRODUCT_PLAN.md) sections 3, 9, and 12, in Korean.
