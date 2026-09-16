# Local web app validation

Validated on 2026-09-16. Start the app using the commands in README.md.

- Production React/TypeScript build, Ruff, formatting, strict mypy and Python package build passed.
- 90 tests passed. Coverage exceeded the configured 80 percent gate (approximately 93 percent).
- Local browser upload of GEBK472722A (1).pdf, rotation 90 clockwise, explicit mm override: successful Gemini reading, 30 proposed callouts, review status because the internal profile is uncertain. No STEP or model was produced for this PDF. This is not an accuracy benchmark.
- Clearly labelled synthetic simple-ring PDF uploaded through the browser, auto units: live Gemini reading, valid body, STEP reimport, 120 x 120 x 16 mm bounding box, interactive mesh and successful STEP download event. STEP, STL, mesh and manifest endpoints returned 200.
- Viewer wireframe/reset and reading-notes expansion checked. Mobile viewport 390 pixels wide had no horizontal overflow. Saved result URLs reload the persisted job.
- Review approved the local unverified body-draft scope. No manufacturing release, full-part conformance or production deployment is claimed.

Local artifacts live under ignored work/web. The sample result IDs are:

- Original PDF review: 9e0e0be358084d4eab366c4ba3017684
- Synthetic generated body: 456d61ee4e124e65975a30e3b9d87ea2

TLS keeps certificate verification enabled using Certifi; credentials remain in ignored .env and are not served to the frontend. The provider wire schema expands references for compatibility while server-side Pydantic validation retains the complete bounds. Runtime origin checks prevent external browser pages from triggering local uploads.
