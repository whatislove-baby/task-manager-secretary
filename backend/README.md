# Python backend

Run tests from the project root with `npm run test:python`; this selects the local `.venv` explicitly. No system-site-packages are enabled. Requirements are fixed in requirements.txt.

`task_backend.__main__` exposes bounded UTF-8 NDJSON over stdin/stdout, without an HTTP server. Electron retains native window/tray/dialog/startup/notification responsibilities; all registered business IPC calls route to BackendService. Slow network calls use separate service/database connections off the input loop. Long-running mail/AI jobs execute in an owned Python worker process and checkpoint to mail-operations.sqlite.

Core: tree.py / repository.py / archive.py. Integrations: settings.py / vault.py / transport.py / canvas.py. Mail: mail_types.py / mail_accounts.py / mail_readers.py / mail_store.py / mail_workspace.py / legacy_mail.py. Jobs and conversation memory: operations.py / worker.py.

SQLite schemas and confirmation fingerprints remain compatible with the original TypeScript repository, which remains only as a development/test oracle. Runtime accepts empty databases and v2-v6, upgrades transactionally to v6, and rejects unknown or malformed schemas. Backups v2-v6 may only enter a fresh empty database.

RPC requests are limited to 2MiB; 64MiB backup files use internal host-selected file operations, not arbitrarily exposed renderer paths. AI output never directly writes tasks. Confirmation receipts prevent duplicates. Credentials use current-user Windows DPAPI. Legacy Electron credentials require a one-time native decrypt/re-encrypt migration through internal host-only RPC.

Runtime packaging: scripts/prepare-python-runtime.py verifies the official CPython archive SHA256 and copies only pinned dependencies; build.extraResources places the runtime and backend next to the packaged Electron archive. scripts/verify-python-package.cjs tests the actual shipped bridge/runtime/worker and rejects legacy TypeScript business modules in the release.

See ../HANDOFF.md for current validation evidence and account-dependent acceptance boundaries.
