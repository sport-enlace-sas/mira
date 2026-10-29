# AI connections and fallback flows

Administrators and delegated users configure AI at **Settings → IA**. Connection
secrets are write-only and encrypted with AES-256-GCM. The database contains
credential versions, immutable flow versions, scenario assignments, diagnostic
runs and administrative events. Prompts, output bodies and provider error bodies
are deliberately absent from diagnostic history.

## Connect, test and rotate

Create a connection, then choose **Conectar cuenta** for Claude/Codex. MIRA runs
the provider's native login in a new temporary home. Complete the official
provider's verification in your browser; paste the returned Claude authorization
code only if requested by that client. Login state belongs to the initiating
administrator, expires after ten minutes and is cancelled on process restart.
No password or mailbox access is collected. The resulting session is staged in
the encrypted database; it does not replace a working connection automatically.

API keys, compatible session JSON and AWS credentials can also be imported.
Static tokens require manual replacement; renewable sessions persist the native
client's updated authentication file under a distributed lock with revision CAS.
Use **Validar / rotar credencial**: only a valid real inference activates the
candidate. A failed check preserves the previous active credential. Diagnostic
and model-discovery operations use the same per-credential lock as production.
Imports of an already known renewable session are rejected, including stale
snapshots from before renewal. Reuse its connection or connect a new session;
separate credential records must not compete for the same rotating token.

Model lists are scoped to credential versions. `listed` means the provider
returned the model; `verified` additionally requires a successful inference with
that exact model and reasoning effort. Claude CLI's registry is explicitly
unverified until tested. Models added to the catalog never change published
flows. GPT 6.1 Sol is `gpt-6.1-sol`, with effort `max` stored and transmitted
separately. Native inference always uses an isolated temporary directory; Codex
tools have a deny-by-default filesystem profile and no network, and Claude's
tools, external MCP configuration and session persistence are disabled.

## Flows and diagnostics

The React Flow editor accepts one connected linear chain with no branches or
cycles. Node count is not capped. Publish creates an immutable version; current
jobs retain their snapshot. Scenario assignments are independent. Every new
request starts at the first node. Context or capability incompatibilities are
recorded as skipped without modifying the request. Node retries own the retry
policy; managed adapters do not change models or silently remove reasoning.
Quota health is scoped by model, so Fable cannot block another Claude model.
When the provider supplies no reset time, the initial quota cooldown is 30
seconds; it is a retry time, not a claimed provider quota reset.

Simulation forces quota/auth/timeout/invalid failures without changing real
credential health. Real diagnostics consume provider usage. They are cancellable
and persist their steps. Advisory jobs link the same production trace by job ID.
The deterministic OCR, OSV and secrets-scanning paths are unaffected.

## Phased migration

1. Deploy the module; the existing provider configuration remains active.
2. In IA choose **Importar configuración actual**. This stages existing secrets
   and creates visible migrated flows and the Fable → alternate Claude → Codex
   Sol/max template. It does not activate imported credentials. Connect new
   sessions owned exclusively by MIRA, rather than copying a workstation session.
3. Select models, test each model/effort, activate valid credentials, and assign
   every scenario. Test the published chains with simulated and real failures.
4. Back up the key and database, then choose **Activar configuración de BD**.
   Activation requires checked model/effort access and at least one available model per flow. A checked quota-limited primary may remain first while another verified node serves the request.
   A protected volume latch prevents reactivating old configuration on a database
   error or incomplete restore. This cutover is intentionally one-way in the UI.
5. Verify a current production job and deployed commit. Remove AI keys/tokens
   from Railway and obsolete plaintext auth files. Do not remove GitHub,
   webhook, database or unrelated AWS credentials. The managed broker does not
   read provider credentials from the environment even before physical cleanup.

## Key recovery and backup

For PostgreSQL the master key is `/data/mira/ai-private/vault.key`. Mount `/data`
on durable storage. SQLite stores `ai-private/vault.key` next to its database.
The directory is 0700 and key file 0600. No environment key is accepted. If
encrypted credentials exist and the key is missing, MIRA fails closed and never
generates a replacement. No web endpoint exports the key.

Take a database backup and an offline key backup before cutover and after key
recovery. The administrator runs `scripts/ai-vault-backup.py SOURCE DESTINATION`
inside a trusted operator session; the destination's existing parent directory
must be 0700 and the destination must not exist. Keep this backup in a separate
restricted backup location, outside the application/model filesystem.

To recover, stop workers, restore the database and its original 32-byte key to
the paths above, restore the `managed` latch for a managed installation, set the
directory/file modes and start MIRA. Test a connection and flow before releasing
jobs. Losing the original key requires reconnecting/reimporting credentials;
existing ciphertext cannot be recovered with a newly generated key.

## Validation

Local development runs only newly added/modified named cases from
`tests/test_ai_vault_flows.py`. CI runs the broad regression suite plus the
PostgreSQL persistence and locking case on a PostgreSQL 16 service. Check the
exact PR head SHA before merging. A green build is not evidence that live
credentials have been authorized, or that DB mode has been activated.
