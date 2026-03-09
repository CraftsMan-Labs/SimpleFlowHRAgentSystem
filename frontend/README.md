# Vue Frontend Shell

Control-plane-required HR chat shell.

## Run

```bash
npm install
npm run dev
```

## Environment

- `VITE_CONTROL_PLANE_BASE_URL` (default: `http://localhost:8080`)
- `VITE_CONTROL_PLANE_TOKEN` (optional preseed token)
- `VITE_AGENT_ID` (required for registration preflight and chat)
- `VITE_AGENT_VERSION` (default: `v1`)

Copy `.env.example` to `.env` and adjust values as needed.

## Included behavior

- Sign-in/sign-out against control-plane auth session APIs
- Registration preflight gate using `GET /v1/runtime/registrations`
- Chat invoke via `POST /v1/runtime/invoke`
- Chat sessions + history via:
  - `GET /v1/chat/history/sessions`
  - `GET /v1/chat/history/messages`
  - `POST /v1/chat/history/messages`
  - `PATCH /v1/chat/history/messages/{message_id}`

Direct browser calls to runtime `/invoke` are intentionally removed from the chat path.
