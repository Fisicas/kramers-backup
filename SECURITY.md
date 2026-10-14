# Security and privacy

## What Kramers copies

Kramers copies whatever is in the backup sets you enable. The built-in sets include private material:

- **Desktop, Downloads, Documents\EndNote:** your files.
- **Claude and Codex:** settings, skills, memory and **full conversation histories**.
- **Python environments:** installed packages, plus anything else stored inside the environment folders.

**Protect the backup drive.** Anyone holding an unencrypted drive can read all of it. On Windows, turn on BitLocker To Go: right-click the drive in Explorer and choose **Turn on BitLocker**. Kramers shows the backup drive's encryption status in **Options**, and can remind you in the main window (off by default).

## What Kramers never copies

- Credential files: `~/.claude/.credentials.json`, `~/.codex/auth.json`, `~/.codex/.sandbox-secrets`, `~/.claude-science/.oauth-tokens`, encryption keys, and EndNote's `login.txt`.
- Login-token keys inside settings files: for example `oauth:tokenCache` in the Claude desktop app's `config.json`. These files are written to the backup with those keys removed. A file that cannot be parsed is skipped, never copied as is.

After restoring onto a new PC, sign in to the apps again.

## What Kramers never does

- It never sends anything over a network. Kramers has no network code.
- It never writes to your source folders during a backup, and a restore never deletes anything.
- It never changes Windows security settings. The **BitLocker settings…** button only opens the Control Panel page.

## Reporting a problem

If you find a way Kramers could leak credentials, lose data, or write somewhere it should not, please open a GitHub issue. Leave secrets and personal paths out of the report.
