# SimpleFlowHRAgentSystem

Standalone test repo for an HR-focused SimpleFlow runtime built from SimpleFlow templates.

## What this repo includes

- `backend/`: Python runtime service compatible with SimpleFlow runtime contract.
- `frontend/`: Vue operator shell for invoke, events, and registration lifecycle calls.
- `workflows/`: YAML workflows copied from prior SimpleAgentChatTemplate setup.

## Runtime contract

- `GET /health`
- `GET /meta`
- `POST /invoke`

Additional helper endpoints:

- `POST /webhook`
- `POST /queue/enqueue`
- `POST /queue/process`

## Quick start

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
make setup
```

Start services in separate terminals:

```bash
make run-backend
make run-frontend
```

## Workflows

Configured by env vars in `backend/.env`:

- `WORKFLOW_ROOT=../workflows`
- `WORKFLOW_ENTRY_FILE=email-chat-orchestrator-with-subgraph-tool.yaml`

`/invoke` converts runtime input into workflow `input.messages`, executes the selected YAML graph via `simple_agents_py`, and returns terminal output as `output.reply`.

If provider credentials are not set (`CUSTOM_API_KEY` or provider-specific env vars), `/invoke` returns `503` with a configuration hint.

## Local verification

```bash
make test-backend
make build-frontend
make smoke
```

## Optional local editable installs

If `simpleflow-sdk` or `simple-agents-py` are not available from your package index:

```bash
backend/.venv/bin/pip install -e ../../SimpleFlowSDKs/python
backend/.venv/bin/pip install -e ../../SimpleAgents/crates/simple-agents-py
```
