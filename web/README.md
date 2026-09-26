# reshelf web

React + Vite SPA for `reshelf serve`.

```bash
npm install
npm run dev     # dev server on 5173, proxies /api to reshelf serve on 8080
npm run build   # type-checks, then builds into ../src/reshelf/web/static
```

`reshelf serve` mounts `src/reshelf/web/static` at `/` when it exists
(`src/reshelf/web/app.py`), so a build must run before the built-in server
has anything to serve. `/api/*` is registered before the SPA mount and is
never shadowed by it.

`src/api.ts` is the only file that talks to the backend - a pure fetch
wrapper, typed against the routers in `src/reshelf/web/api/`. Every route
component imports from it instead of calling `fetch` directly.
