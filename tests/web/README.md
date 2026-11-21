# tests/web

Lane B's tests. Lands in Phase 3 alongside the React app.

Coverage planned here: EXIF and GPS are stripped in the browser before upload,
the scene reducer applies only committed `SceneRevision` patches, a reconnect
fetches `GET /api/state?since_revision`, and every mock, preset, cached,
generated and inferred item carries its label on screen.

Type safety is already covered: `cd web && npm run build` type-checks the app
against the generated contract types.
