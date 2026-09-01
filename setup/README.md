# Participant preflight

This directory contains only the read-only participant prerequisite check. It
does not deploy, update or delete Azure resources and does not assign roles.

The cloud operator supplies a team-specific `.env`. From the repository root,
run:

```powershell
pwsh ./setup/Test-WorkshopPrerequisites.ps1 -EnvironmentFile .env
```

The default check verifies Python 3.12, Node.js 20.9 or newer, the selected
Azure identity, the exact Foundry model, the assigned Azure AI Search project
connection, and the primary procedure index.

Foundry IQ and Blob Storage ingestion are optional extensions. Their `.env`
settings are all-or-none: omit a complete group when the extension is not part
of the workshop, or provide every setting in that group. A partial group fails
preflight. When an extension is promised, require it explicitly:

```powershell
pwsh ./setup/Test-WorkshopPrerequisites.ps1 -EnvironmentFile .env -RequireFoundryIQ
pwsh ./setup/Test-WorkshopPrerequisites.ps1 -EnvironmentFile .env -RequireStorage
pwsh ./setup/Test-WorkshopPrerequisites.ps1 -EnvironmentFile .env -RequireFoundryIQ -RequireStorage
```

An omitted optional group produces a warning and leaves the core direct-Search
path available. The check only performs read operations. Report failures to the
facilitator; do not attempt to run operator deployment or RBAC commands.

At initial handoff, the cloud operator must also run the one-time empty-agent
gate:

```powershell
pwsh ./setup/Test-WorkshopPrerequisites.ps1 -EnvironmentFile .env -RequireEmptyAgentNames
```

It proves that the four participant-owned runtime names do not already contain
completed agent versions. Do not use this switch after your team has deployed
its own implementations.
