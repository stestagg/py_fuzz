# pyfuzz UI

This directory contains the UI-only implementation: the pnpm/Vite React app and the small single-use websocket backend.

Run the backend from the repository root:

```sh
python src/ui/backend/server.py lazy
```

Run both development servers through the project CLI:

```sh
./pfx ui
```

Run the frontend during development:

```sh
cd src/ui
pnpm install
VITE_PYFUZZ_WS_URL=ws://localhost:8765/ws pnpm dev
```

For a built UI served by the backend:

```sh
cd src/ui
pnpm build
cd ../..
python src/ui/backend/server.py lazy
```

The websocket protocol uses request messages with a `type` and `requestId`. The current request types are `projects:list`, `project:get`, `project:select`, and `summary:refresh`.
