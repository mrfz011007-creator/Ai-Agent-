# Agent Memory

The agent uses structured persistent memory in memory.json.

## Memory types

- fact — observed or verified information.
- decision — an intentional choice made for a project or task.
- experience — a result learned from an execution attempt, including failures and fixes.
- preference — a user or project preference that should influence future execution.

## Record model

Every record contains:

- stable record ID
- memory type and key
- value
- project/task scope
- contextual metadata
- source/provenance
- creation/update timestamps
- revision version
- active/invalidated status
- optional superseded record
- tags

Memory revisions are append-oriented: updating a memory invalidates the previous active revision and creates a new version. Historical records remain available until lifecycle compaction; execution evidence remains authoritative for audit.

## Retrieval

Retrieval is deterministic and local. It can scope results by:

- project
- task
- context
- memory type
- tags

Text retrieval uses lightweight token matching and contextual scoring. No embedding model or vector database is used.

## Trust model

Memory is not evidence. Verified execution evidence remains authoritative for completion decisions. Memory can inform planning and execution, but it must not override current verification.

## Compatibility

The legacy flat key/value memory.json format is automatically migrated into typed fact records on load.


## Lifecycle and storage bounds

Automatic execution experiences are stored with ephemeral retention and bounded payloads; the full execution evidence remains in the authoritative evidence store. Oversized experience values are replaced by a short preview plus SHA-256 and original-size metadata.

The JSON store performs automatic compaction when it exceeds the configured threshold:

- AI_AGENT_MEMORY_AUTO_COMPACT_BYTES — default 8 MiB.
- AI_AGENT_MEMORY_MAX_ACTIVE_EXPERIENCES — default 500 non-durable active experiences.
- AI_AGENT_MEMORY_MAX_HISTORY_PER_KEY — default 5 historical revisions per memory identity.

Compaction preserves all active non-experience memory and active durable experiences. It does not delete execution evidence.

Memory values included in model context are bounded per record before the planner/executor applies its overall context limit.
