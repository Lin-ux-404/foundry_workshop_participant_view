# Microsoft Foundry grid-operations workshop — participant package

This is the participant-only package for the two-day Microsoft Foundry
workshop. It contains the hands-on labs, the application AI playbook, synthetic
exercise data and the environment-variable template.

It deliberately does **not** contain cloud deployment automation, RBAC or
facilitator material, operational evidence, or the completed reference
application. Your cloud operator provides the prepared Azure environment and a
team-specific `.env` file. You implement and test the AI components during the
workshop.

## Start here

1. Read the [workshop guide](docs/WORKSHOP_GUIDE.md).
2. Create a Python 3.12 environment and install `requirements.lock`.
3. Ask the cloud operator for your team-specific `.env`; do not commit it.
4. Run the [read-only participant preflight](setup/README.md).
5. Complete the [agent and knowledge labs](labs/azure-ai-agents/README.md).
6. Complete the [observability and evaluation labs](labs/observability-and-evaluation/README.md).
7. Use the [application AI playbook](app/PLAYBOOK.md) to implement the AI layer.

```bash
python scripts/validate_participant_package.py
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip==26.2
python -m pip install -r requirements.lock
python -m unittest discover -s tests -v
```

The repository's `quality` workflow repeats the participant boundary,
manifest, notebook, Python test, frontend build and dependency-audit gates
without connecting to Azure. Run the integrity validator before adding the
team-specific `.env`. The public repository is updated only through a fresh
validated export from the maintained source; direct edits intentionally fail
the immutable package-manifest gate.

Copy `.env.example` to `.env` only if the cloud operator has not provided the
file. Replace every placeholder with values assigned to your team.

## Boundaries

- All workshop records are synthetic.
- The exercises do not authorize electrical work or replace an operational
  decision maker.
- Never commit `.env`, credentials, tokens, participant identity files, run
  evidence, or screen recordings.
- Foundry IQ and other preview-dependent paths have fallbacks documented in the
  lab guides.

## Publication note

This directory is exported from an explicit allowlist into a new repository;
it does not inherit the internal repository's Git history. See `NOTICE.md` for
the current licensing boundary.
