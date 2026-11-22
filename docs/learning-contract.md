# User-approved reusable methods

The model may propose a method and execute an approved method. Only the separate
user review IPC can approve or disable it. Mail text, model confidence and a prior
assistant answer are never approval. Corrections create new immutable versions;
the previous approved version stays active until the new version is approved.
Approval also checks the parent chain and server-authored `reviewBase` under a
transaction: a stale draft from another review window cannot replace a newer
approved branch. Initial unapproved drafts and revisions of the active version
can be corrected repeatedly before approval. Their authority baseline is held
fixed across the draft chain. The baseline includes active version plus a digest
of prior approvals/revocations, so approving then disabling another branch cannot
make an old draft valid again merely because the active pointer is empty.
Approval of the already-current version is idempotent. Superseded and disabled
versions cannot be directly re-approved. When no version is active, the most
recently disabled version can be the parent of a **new** draft requiring separate
review. `reviewBase` is immutable server evidence, never a writable model input.
When another review changes that baseline, older draft versions are shown as
`superseded`, remain readable in history, and disappear from pending review;
their proposal bytes and recorded hash are not rewritten.

## Service and tools

`LearningService(service)` uses `service.files` and `service.mail.store` and owns
`learning.sqlite`. It exposes `propose(value, proposal_id=None, *, teaching=None)`, `list(value=None)`,
`get(id)`, `approve({id, expectedHash})`, `disable({id, expectedHash})`,
`search(query='', sender='', scope_id='', status='approved')`, and
`run(version_id, file_id, name, scope_id='', source_mail_id='', run_id=None)`.
`call(method,*args)` dispatches only UI list/get/approve/disable.

The model-facing `LearningAgentTools` mixin exposes `propose_learning(value,
call_id=...)`, `search_learning(query='', sender='', scope_id='', status='approved')`,
`get_learning(id)`, `run_learning(version_id,file_id,name,scope_id,
source_mail_id='',call_id=...)`. There is deliberately no approval tool.

`learning_schema.LearningProposal` is the strict nested Pydantic input exposed
by the LangChain tool, including scope, sources, recipe, concat, commercial basis
and all nine bounded table operation shapes. Unknown fields are rejected rather
than discarded. Read results contain enriched source hashes, names, teaching,
approval metadata and preview data; these are **not** proposal inputs. Copy only
`kind` and `id` for each source. `parentId` is the approved version UUID returned
as `get_learning.id`, never the `scopeId`. A `note` omits the table recipe and
example fields. Server-side shape errors identify missing/unknown key paths,
without echoing field values, so a malformed model call can be repaired directly.

Drafts return `kind: learning_draft`, `id`, `title`, `versionHash`, `status`,
`scopeId`, `scope`, `preview`, and the immutable `proposal`. A user approval changes
status, never proposal bytes. `kind: learning` means the currently approved version.
Search defaults to active approved versions; `status='draft'` finds drafts for
continued teaching and `status='all'` allows comparison/recovery of historical
versions. `get_learning(id)` can read any version, returning `canRun` and an explicit
execution warning. Reading is not approval: `run_learning` still requires the
current approved table workflow. An unscoped query can discover scope names, but
the caller must resolve the actual scope before executing.

## Proposal JSON example

All field names below are exact. A correction supplies `parentId` equal to the
parent version ID (including an unapproved draft being corrected), and must preserve its scope and title. Omit optional `parentId`
for the initial version. `sources` are evidence, not executable instructions.

```json
{
  "title": "订单转内部清单",
  "kind": "table_workflow",
  "scope": {
    "kind": "customer",
    "label": "Harbor Instruments",
    "emails": ["orders@harbor.example"],
    "contextKey": ""
  },
  "instruction": "客户料号用 ITEM-REV，REV 不足两位补零；保留 PO 的前导零。所有单价为 USD 未税，不合并行。",
  "sources": [{"kind": "file", "id": "MANAGED-EXAMPLE-FILE-UUID"}],
  "exampleFileId": "MANAGED-EXAMPLE-FILE-UUID",
  "recipe": {
    "inputColumns": ["PO", "ITEM", "REV", "QTY", "PRICE"],
    "optionalInputColumns": [],
    "allowExtraColumns": false,
    "mapping": {"PO号": "PO", "数量": "QTY", "单价": "PRICE"},
    "constants": {},
    "concat": [{"target": "客户料号", "columns": ["ITEM", "REV"], "separator": "-", "padLast": 2}],
    "operations": [{"op": "derive", "column": "金额", "operator": "multiply", "left": {"column": "数量"}, "right": {"column": "单价"}}],
    "outputColumns": ["PO号", "客户料号", "数量", "单价", "金额"],
    "requiredValues": ["PO号", "客户料号", "数量", "单价"],
    "numericColumns": ["数量", "单价", "金额"],
    "moneyColumns": ["单价", "金额"],
    "totals": ["金额"],
    "preserveRowCount": true,
    "commercial": {"currency": "USD", "taxBasis": "exclusive", "unit": "PCS"},
    "outputFormat": "xlsx"
  }
}
```

`sheetName` is an optional recipe field for XLSX inputs; multiple nonempty sheets
require an explicit name. It does not name the output sheet: new XLSX files use
`Result`. Do not infer a requirement to use a particular input sheet merely from
the output reference workbook's tab name. `expectedOutputFileId` is optional proposal evidence:
its entire table must match the preview. The required example is dry-run before
a draft is saved; preview includes row counts, a small row sample, exact totals,
input SHA-256 and output SHA-256. The user reviews the actual mapping and result.
Examples and expected output are automatically retained as source file IDs.
The actual example input must match the customer scope. An explicitly selected
`expectedOutputFileId` may come from an internal colleague: its source is shown
as `role: reference-output`, it must match the complete trial output, and the user
still approves the resulting method. It does not establish customer identity.
If `sources` explicitly includes a mail from that reference file's actual stored
provenance, the server marks that mail `role: reference-output` as well and retains
its true sender. This narrow exception never adds an email to the customer scope
and does not permit unrelated foreign mail or foreign working inputs. The model
still supplies only kind/id; reference roles are assigned by the server.

`kind: note` requires scope/title/instruction/sources and omits all table fields.
It stores a preference or method as approved text, not executable code. The model
can retrieve it to guide a later conversation; retrieval does not prove correctness.

Scope `kind` is `customer`, `course`, or `personal`. Customer/course scopes require
exact individual `emails`; course also requires `contextKey` (course code). A
personal scope may have no emails. Stable scope identity is derived from kind,
exact normalized email set and contextKey; display label does not establish
identity or authorize sharing. Different senders never inherit by display name.
Working-input mail provenance must match a scope email via stored `fromAddress`,
including parents of unzipped files. A learned generated output retains every
reference in parentIds, while customer identity follows its audited inputId;
an internal reference template does not change the customer. RFC From is a declaration, not verified sender
authentication. Files without mail provenance require explicit scope selection.

## Execution constraints

Input schema must match declared columns. No implicit layout detection, currency
conversion, tax conversion, missing-price filling or inferred revision handling.
Every declared input column is nonempty by default, before mapping or transforms.
`optionalInputColumns` explicitly exempts named input columns and must be shown in
the user review UI. Missing Required Date therefore cannot slip through simply
because a model omitted it from output `requiredValues`. An old immutable recipe
without `optionalInputColumns` runs with the same default `[]`; its stored payload
and hash are not rewritten. Required output and numeric checks still apply after
an input is marked optional; optional does not mean guessing missing prices.
The runtime supports column mapping, constants, item/revision concatenation, and
the existing bounded CSV operation language. Required cells and decimal columns
are checked before and after transformations. Monetary columns require explicit
currency, tax basis and unit. A missing/ambiguous field raises a clarification
error; no partial output is saved. Commercial statements are user-reviewed
assumptions, not facts inferred or verified from external sources.

CSV/XLSX identifiers remain strings (including leading zeros); arithmetic uses
Decimal. XLSX formulas, macros, external relationships, embedded objects and merged
cells are rejected. The implementation does not recalculate formulas or preserve
arbitrary workbook layout; output is a new fixed-schema literal sheet. CSV formula
injection protection may prefix suspicious values with an apostrophe and reports
that change. No eval, shell, executable skill source or networking exists.

Execution requires a current approved version and its exact scopeId. Audit output
includes version ID/hash, scope ID, input ID/hash, output ID/hash and totals. Output
files reference their input and example file IDs through `parentIds`. Replays
verify input and output hashes, current approval and scope again, and reuse the
same managed file. Disabling or approving v2 invalidates new v1 runs even if an
earlier execution is cached. Outputs remain available as immutable evidence.

## Original teaching evidence

`instruction` is the model's proposed interpretation. It is **not** labelled as
the user's verbatim instruction. The service also saves `proposal.teaching`, which
the model cannot supply in its tool arguments:

```json
{
  "source": "conversation",
  "conversationId": "actual-saved-conversation-id",
  "runId": "actual-saved-run-id",
  "turns": [{"id": "actual-turn-id", "runId": "actual-run-id", "createdAt": "saved-time", "content": "Exact saved user text, including clarification", "textSha256": "sha256-of-utf8-text"}],
  "omittedUserTurns": 0,
  "sourceHash": "sha256-of-canonical-evidence"
}
```

The agent adapter reads real `role=user` turns from the owning conversation. It
retains the latest contiguous maximum four complete user messages through this
execution, with a total 128000-character budget and existing per-message 64000
limit. Longer history is counted in `omittedUserTurns`; messages are never silently
cut or reconstructed from the assistant's summary. The service independently
checks each captured snapshot against its saved conversation and execution.
Each original tool call checkpoints its teaching snapshot before mutation. If a
later clarification arrives, replay of that old call keeps the old snapshot and
hash; a new call captures the new user instruction. This also survives the window
after proposal commit but before the generic tool result was checkpointed.

Direct service/fixture calls without a real conversation record save
`{source: "direct-input", turns: [], omittedUserTurns: 0, sourceHash: ...}`.
The UI labels these as no verified original conversation. Older versions may have
no teaching field and are labelled legacy rather than synthesizing an origin.
UI review shows this original evidence alongside the proposed instruction and
the optional-input exemptions. `teaching` is included in the immutable version
hash. It records what was said, not a guarantee that the model interpretation is
semantically correct; single-order commercial facts still need scoped review.
