# Vue Frontend Shell

Control-plane-required HR chat shell.

## Run

```bash
npm install
npm run dev
```

## Environment

- `VITE_TEMPLATE_BACKEND_BASE_URL` (default: `http://localhost:8092`)
- `VITE_AGENT_ID` (optional initial selected agent id)
- `VITE_AGENT_VERSION` (default: `v1`)

Copy `.env.example` to `.env` and adjust values as needed.

## Included behavior

- Sign-in/sign-out through template backend gateway (`/api/control-plane/auth/sessions*`)
- Agent selector populated from `GET /api/agents/available`
- Onboarding controls via `POST /api/onboarding/start`, `GET /api/onboarding/status`, and `POST /api/onboarding/retry`
- Chat invoke via `POST /api/control-plane/runtime/invoke`
- Chat sessions + history via:
  - `GET /api/control-plane/chat/history/sessions`
  - `GET /api/control-plane/chat/history/messages`
  - `POST /api/control-plane/chat/history/messages`
  - `PATCH /api/control-plane/chat/history/messages/{message_id}`

Frontend does not require a direct control-plane env token; backend forwards bearer tokens via SDK.
