# DRAAD participant application shell

This package supplies the runnable FastAPI backend, Next.js frontend,
deterministic safety rules, typed contracts and synthetic lookup data. Your
team implements and deploys the four Microsoft Foundry prompt agents.

The completed prompt definitions and operator deployment/indexing utilities are
intentionally absent. Follow the [AI component playbook](PLAYBOOK.md) and use
the [starter notebook](participant/1-build-ai-components.ipynb).

## Install

Use Python 3.12 and Node.js 20.9 or newer. From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip==26.2
python -m pip install -r requirements.lock

cd app/frontend
npm ci
cd ../..
```

Use the team-specific `.env` supplied by the cloud operator. Never commit it.

## Build the AI layer

Complete `app/participant/1-build-ai-components.ipynb`. The notebook creates
new versions under the stable prompt-agent names expected by the supplied
runtime and checks the Search connection, structured retrieval contract and
end-to-end pipeline.

## Run

Backend:

```bash
cd app/backend
python -m uvicorn main:app --reload --port 8000
```

Frontend, in a second terminal:

```bash
cd app/frontend
npm run dev
```

Open `http://localhost:3000` and repeat one grounded Q&A case and one dispatch
case from the notebook.

## Boundary

- `backend/agents/` is absent because participants implement those prompt
  contracts.
- Infrastructure deployment, Search indexing and administrator recovery tools
  are not participant assets.
- The runtime shell retains deterministic filtering, rule evaluation and the
  final fail-closed action.
- All records are synthetic teaching fixtures, not operational authorization.
