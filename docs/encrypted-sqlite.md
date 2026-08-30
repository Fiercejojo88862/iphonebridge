# Encrypted SQLite for Message Cache — Design Options

`BACKLOG.md:32` asks for encrypted storage for the message/contact cache.
Currently:

- `contacts.sqlite:1` (`src/iphonebridge/contacts.py:59`) — plain SQLite
- `events.jsonl:1` (`src/iphonebridge/sinks/jsonl.py:1`) — plain JSONL (append-only)

Both live under `~/.local/state/iphonebridge:1` (`config.py:68`) and are 0600
by default, but not encrypted at rest.

This doc captures the minimal design so we can pick a key-management
strategy before coding (this is the "stuck" point that needs your input).

## Option A — SQLCipher (recommended for beginners)

- **What:** Replace `sqlite3` with `pysqlcipher3` (or `sqlcipher` CLI). The DB
  file stays SQLite-compatible but every page is AES-256 encrypted. The daemon
  opens with `PRAGMA key='...'`; no other code changes.
- **Key:** Single 32-byte key stored at `~/.config/iphonebridge/.key` (0600,
  generated via `head -c 32 /dev/urandom | base64`). Optionally backed by
  `libsecret` (GNOME Keyring) via `secret-tool store --label=iphonebridge`.
- **Pros:** Drop-in, proven, no file-level encrypt/decrypt dance. Works for
  contacts + future messages SQLite.
- **Cons:** Extra dep (`sqlcipher`, `pysqlcipher3` not in `apt` by default;
  needs `pip` or `apt install sqlcipher` + rebuild).
- **Migration:** On first `IPHONEBRIDGE_ENCRYPT=1`, daemon creates key if
  missing, then `sqlcipher contacts.sqlite "PRAGMA rekey=..."` to encrypt
  existing plain DB in-place.

## Option B — File-level Fernet (no new DB engine)

- **What:** Keep `sqlite3`, but wrap the file at rest with `cryptography.fernet`.
  `src/iphonebridge/crypto.py:1` would `get_key()` → `Fernet(key)`, decrypt
  `contacts.sqlite.enc` → temp `contacts.sqlite` on open, re-encrypt on close.
- **Key:** Same `.key` file as A, but file is `contacts.sqlite.enc` on disk
  (so a stolen disk without the key yields nothing).
- **Pros:** No `sqlcipher` dep, pure Python (`cryptography`).
- **Cons:** Must handle crash-consistency (write to `*.tmp` then atomic rename);
  slightly more code in `contacts.py:_open_db:79`.
- **Migration:** `python -m iphonebridge.crypto --migrate` encrypts existing
  plain DB one-shot.

## Option C — System-level (no code)

- **What:** Move `~/.local/state/iphonebridge` onto an encrypted volume
  (`fscrypt` on `~`, or `gocryptfs` mount at `~/.local/state/iphonebridge`).
- **Key:** Managed by login password / `pam_mount`.
- **Pros:** Zero code, covers JSONL + SQLite + everything.
- **Cons:** Requires one-time system setup (`sudo apt install gocryptfs`,
  `gocryptfs -init ~/.cipher && gocryptfs ~/.cipher ~/.local/state/iphonebridge`);
  not portable to a fresh laptop without the mount.

## Recommendation for your setup (ASUS Vivobook S16, Omarchy, 32GB)

Start with **Option A** (`sqlcipher`) — it’s the least code and you already
manage system packages via `apt`. If you prefer no new system dep, fall back
to **Option B**.

## What I need from you (so I can code it without guessing)

1. **Key storage:** `.key` file (0600) alone, or also `libsecret` keyring?
2. **Enforcement:** `IPHONEBRIDGE_ENCRYPT=1` env var to opt-in, or always-on
   after first setup?
3. **Scope:** contacts only, or also migrate `events.jsonl` → `messages.sqlite`
   and encrypt that too?

Reply with `A`/`B`/`C` + answers to 1-3 and I’ll implement the chosen path
(`src/iphonebridge/crypto.py`, `contacts.py`, `sinks/jsonl.py`) and add
`iphonebridge encryption-enable` to `cli.py:584`.

## References

- `src/iphonebridge/contacts.py:59` (`_SCHEMA`, `_open_db`)
- `src/iphonebridge/config.py:68` (`STATE_DIR`, `CONTACTS_DB`, `EVENTS_JSONL`)
- `https://www.zetetic.net/sqlcipher/` and `https://github.com/rigelk/sqlcipher`
