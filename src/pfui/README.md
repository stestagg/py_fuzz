# PFUI v2

PFUI is the cross-project pyfuzz dashboard. It is intentionally separate from
the original `./pfx ui` implementation.

Run the packaged application from the repository root:

```sh
./pfui
```

The nearest `.pyfuzz_project` is selected initially. Use `--project NAME` to
override it or `--no-open` to leave the browser closed. By default, PFUI runs
the frontend through Vite with hot module replacement; use `--no-dev` to serve
the built static frontend instead.

Frontend development:

```sh
cd src/pfui/web
pnpm install
pnpm test
pnpm build
```

Backend tests run from the repository root:

```sh
PYTHONPATH=src python -m unittest tests.test_pfui -v
```

`pnpm build` writes the tracked production application to `../static`.
`./pfui --no-dev` needs only Python because aiohttp serves those packaged
assets and the websocket from one process.

The websocket protocol uses explicit project context:

```json
{"id":"1","method":"summary.get","project":"lazy","params":{}}
```

Project selection is browser-tab state and never changes `.pyfuzz_project`.
