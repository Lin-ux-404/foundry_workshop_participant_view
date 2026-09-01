# Build the DRAAD AI components

This is the participant application lab. The web interface and application
shell are supplied; your team implements the AI components that make the
application useful.

The finished result remains a teaching system built from synthetic data. It
does not authorize electrical work or replace an operational decision maker.

## Outcome

Your team will deploy four versioned prompt agents into the supplied DRAAD
runtime:

1. a Search-grounded procedure Q&A agent;
2. a procedure retriever that returns a strict JSON contract;
3. a matcher that judges applicable VWIs without choosing authorization or
   crew records; and
4. a reviewer that can pass, request a bounded correction, or require human
   review.

You then run one Q&A request and one incident through the intact FastAPI and
Next.js application.

## What is supplied and what you build

| Supplied application component | Participant-owned component |
|---|---|
| Next.js chat and pipeline visualization | Agent instructions |
| FastAPI endpoints and server-sent events | Azure AI Search tool attachment |
| Input parsing and typed response models | Prompt-agent definitions and versions |
| Synthetic incidents, crew and authorization scopes | Grounding and citation behavior |
| Deterministic postcode, date, coverage and live-work rules | Structured retriever, matcher and reviewer outputs |
| Fail-closed final operational gate | Evidence from the final smoke test |

This boundary is deliberate. Models retrieve, classify and review. Python owns
record selection, auditable rules and the final operational action.

## Time and team split

Use the two scheduled blocks, about 60 minutes in total. In a team of four,
assign one agent to each person, then review the four contracts together before
deployment. Allow 60–90 minutes when working alone. Microsoft Learn commonly
uses a short concept section followed by a 30–45 minute hands-on exercise; this
lab adds team integration and an application smoke test to that pattern.

## Prerequisites

- Complete the required core agent and Azure AI Search labs first.
- Use Python 3.12 and the repository's pinned dependencies.
- Sign in with Azure CLI to the assigned tenant.
- Receive a generated `.env` and a unique `WORKSHOP_RESOURCE_NAMESPACE`.
- Confirm that the four team-scoped agent names are empty at initial handoff;
  completed reference agents must not be preloaded into the participant project.
- Confirm the signed-in tenant and subscription with the facilitator; run the
  participant preflight when it is included in your delivered package.
- Confirm that the prepared Search index contains synthetic VWI content.

From the repository root:

```bash
source .venv/bin/activate
az account show --query "{tenant:tenantId,subscription:id,user:user.name}" -o table
```

The notebook setup cell performs the application-specific environment and
project-connection checks before any agent version is created.

Do not run infrastructure deployment scripts. The cloud operator prepares the
project, Search service, connection and index before the workshop.

## Lab path

Open [`participant/1-build-ai-components.ipynb`](participant/1-build-ai-components.ipynb)
and work through the tasks in order.

### 1. Resolve the project connection and build the Search tool

Use the assigned Foundry project endpoint and the existing project connection.
The Search tool requires the project connection ID and index name. Use semantic
querying and a bounded `top_k`; do not create or delete an index.

### 2. Implement the Q&A and retriever agents

The Q&A agent must answer only from Search evidence, include inspectable source
references, and say that evidence is insufficient when the index does not
support an answer.

The retriever must return JSON with this shape:

```json
{
  "vwi_candidates": [
    {
      "vwi_id": "E-85",
      "title": "...",
      "content": "...",
      "source_doc": "..."
    }
  ]
}
```

It retrieves candidates; it does not decide authorization, crew assignment or
the final operational action.

### 3. Implement the matcher

The matcher receives the incident, retrieved candidates and a list of
authorization scopes already filtered by Python. It selects only candidate VWI
identifiers, confidence, rationale and citations. It must never invent a VWI or
select a crew or authorization record.

Malformed, ambiguous or out-of-scope results must be able to fail closed in the
supplied backend.

### 4. Implement the reviewer

The reviewer challenges the proposal and returns one of:

- `pass`;
- `revise`, with concrete feedback for the matcher; or
- `flagged_for_human_review`.

Its output must include structured findings. A failed deterministic rule or a
danger signal cannot be converted into approval by prose.

### 5. Deploy and test

The notebook deploys new versions under the four team-scoped names already used
by the runtime. Do not rename them in the notebook or application.

The final cells run:

- one direct Q&A smoke test;
- one structured retriever-contract check; and
- one complete incident through the FastAPI pipeline code.

Then start the supplied application:

```bash
cd app/backend
python -m uvicorn main:app --reload --port 8000
```

In a second terminal:

```bash
cd app/frontend
npm ci
npm run dev
```

Open `http://localhost:3000` and repeat the Q&A and incident cases.

## Completion evidence

A team is complete when it can show all of the following:

- four agent names and newly created version numbers;
- a Q&A answer whose source can be inspected in the prepared Search index;
- a retriever response that satisfies the JSON contract;
- a dispatch trace containing retriever, matcher, deterministic rule and
  reviewer stages;
- five deterministic rule verdicts;
- a final action of either `dispatch_ok` or `wv_escalation_needed`, with the team
  able to explain why; and
- one failure encountered and the evidence used to correct it.

Do not submit access tokens, `.env`, personal data or real operational records.

## Recovery

- If the agent cannot be created, verify the project endpoint, model deployment,
  identity and Foundry role before changing code.
- If Search returns no evidence, verify the connection ID, index name, retrievable
  fields and data-plane roles.
- If JSON parsing fails, reduce prose and make the output contract explicit.
- If the reviewer repeatedly requests changes, stop at the bounded application
  limit and require human review.
- If Foundry IQ is unavailable, this application lab still uses the required
  direct Azure AI Search path.

## Official references

The notebook follows the current Azure AI Projects 2.x prompt-agent pattern and
the current Search-tool contract. These links were reviewed on 2026-08-27:

- [Create a prompt agent](https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/prompt-agent) — `AIProjectClient`, `PromptAgentDefinition`, version creation and agent-bound Responses client.
- [Connect an Azure AI Search index to Foundry agents](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/ai-search?view=foundry) — connection ID, index parameters, Entra/RBAC prerequisites and citation verification.
- [Agent development lifecycle](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/development-lifecycle) — named prompt agents, versioning, testing and evaluation.
- [Develop your first agent with Microsoft Foundry](https://learn.microsoft.com/en-us/training/modules/develop-first-agent/) — concept, implementation, client integration and a hands-on lab.
- [Evaluate and optimize agents through structured experiments](https://learn.microsoft.com/en-us/training/modules/evaluate-optimize-agents/) — explicit metrics, comparison and evidence-based iteration.

Product contracts can change. Use the pinned repository environment during the
workshop and treat the repository's tests and preflight as the release contract.
