# Revision B implementation status

Revision B replaces the earlier pipeline. The local workflow now continues from evidence
review into **draft** construction, preview, STEP/STL export and versioned correction. The
user's later request explicitly permits useful draft output while unresolved findings remain.
Paired-data acceptance still blocks manufacturing release, rather than hiding draft artifacts.
This is not a claim of 98% reading accuracy or complete production readiness.

## Implemented and exercised

- Immutable source, 300/600 dpi PDF renders, rotation transforms, native text, source hashes.
  Raster images keep their native resolution; no additional source detail is claimed.
- Configured, separate context, visual inventory and text calls. Visual inventory never receives
  text-reader output. Detailed per-view crops get independent inventory calls and page mapping.
- Exact model IDs, prompts, schemas, immutable responses, bounded attempts, usage and timing.
  A new model call needs a new run. Offline replay verifies all recorded artifact hashes and the
  implementation/configuration/prompt/schema fingerprint. Replay returns the saved result;
  it does not yet resume partially completed provider work.
- Strict context blocking: no silent mm default, missing identity/envelope, unit conflicts and
  an implausible envelope route to review. The upload selector cannot silently override a title
  block. The provisional 2000 mm family envelope bound is configurable and needs shop review.
- Pinned Document AI adapter with fixed overlapping tiles and normalized token mapping.
  Missing processor/credentials remains UNAVAILABLE; there is no disguised Gemini OCR fallback.
- Requirement union retaining unmatched independent observations, raw tolerance text, general
  notes, source boxes and reader uncertainties. Exact whole numeric tokens are required. Raw
  tolerance strings remain uninterpreted; agreement never approves their geometric meaning.
- Versioned contracts for inventory, requirements, associations, numeric provenance and assumptions.
  Restricted numeric evaluator accepts citations, dimensionally compatible +/- and division by
  literal 2, explicit datum zero, cardinal centreline angles with reasons, or registered assumptions.
- Coverage findings for missing associations, absent/unclear counts, different cross-view counts,
  missing readers and text-vs-inventory feature categories. Different cross-view visibility is a
  review finding, not proof that the physical feature count is wrong.
- Independent STEP measurements for coaxial axial bands, axial circular hole patterns and
  straight cylindrical passages into a coaxial cavity. Counts, placement, depth and material
  obstruction are checked. Unimplemented measurements return UNKNOWN.
- Analytic B-rep inspection for gland rings; all-planar/faceted rings cannot pass it. This is not
  an arbitrary-part tessellation detector or complete AP242 conformance test.
- Paired-data evaluation validates input hashes, a frozen candidate, approved positive references,
  family split isolation, real-only acceptance metrics and seeded structural errors. Synthetic
  tests cannot unlock the gate. Scan and clean metrics are reported separately.
- Browser inventory review with source highlighting, visible open findings and downloadable
  ledger/JSON/annotated report. Legacy bodies are explicitly marked unverified.
- Closed draft feature specification, dimensional provenance checks, required per-feature
  parameters, explicit coordinate policy and separate registered assumptions.
- Deterministic revolved polygon bodies (including grooves), axial hole patterns, tap-drill
  geometry, straight/oblique radial passages, cylindrical bore notches and circular chamfers.
  Failed/ineffective/splitting cuts name their feature and retain the previous valid solid.
- Reimported STEP measurements, named cut receipts, analytical B-rep validation and exported
  STEP/STL/mesh. Surface fragments map back to feature identifiers for 3D highlighting.
- XZ/YZ section SVGs and topology signatures measured from reimported STEP. The comparison
  utility checks boundary types and ordered line-turn adjacency against reviewed annotations.
  Missing references remain UNKNOWN. This is not a complete semantic drawing-view matcher.
- Drafts remain downloadable with UNKNOWN/FAIL findings. STEP integrity never means drawing
  conformance. The original audit and each model attempt are immutable and separately hashed.
- Correction editor, expected-version rejection, retained prior drafts after failed rebuilds,
  authenticated individual review decisions bound to the exact manifest, and immutable logs.
- Local container definition with CadQuery/OCCT and locked Python/frontend dependencies.

The separate legacy `api/index.py` entrypoint preserves the existing Vercel/R2 body-preview
deployment through `legacy_web_api.py`. Its faceted exporter is not used by the Revision B
builder and cannot pass Revision B's analytic B-rep gate. R2 storage exists for that legacy
workflow; Revision B currently uses local artifact storage. The `cad` installation extra
provides CadQuery for a minimal container install, while development dependencies include it.

Alternate whole-page and crop feature observations are retained rather than silently merged.
They are not canonical physical feature counts. Engineer-labelled evaluation and association
review must resolve duplicate appearances. Inventory observations alone do not establish recall.

## Not yet implemented or accepted

The following remain incomplete or require external acceptance:

- Full per-view text reading, per-callout high-zoom rereads and spatial dispute reconciliation.
- Reliable automatic semantic association and host-face confirmation on an approved paired
  dataset. The live test still needed a source-based draft correction; model proposals alone
  did not reconstruct this drawing correctly. Corrections are logged, not automatically promoted
  into engineer-approved evaluation truth.
- Complete section topology/adjacency validation for curved boundaries, arbitrary drawing
  section planes, and reviewed source-section annotations. Current measurement has a declared
  narrower scope; absent annotations remain UNKNOWN.
- Reviewed tapered/helical thread geometry, arbitrary ports/slots, automatic deskew and complete
  interrupted-stage resumption. Supported threads currently use tap-drill cylinders only.
- PostgreSQL/object-storage deployment, shared-user authentication, production release
  transactions backed by accepted evaluation/CAM evidence, retention/region approval and ops.
- Production model/prompt acceptance, independent real Document AI validation and AP242/CAM
  import approval. The emitted STEP is an analytic B-rep; AP242 is not certified by this check.

The release endpoint intentionally returns BLOCKED until acceptance/CAM evidence is implemented
and approved. An UNKNOWN review sign-off cannot waive FAIL, a missing measurement obligation,
or the dataset gate. The six engineering questions for 2H-66033 remain review dependencies.

## Draft and review API

- `POST /api/drawings`: evidence audit followed by a draft attempt when context resolves.
- `POST /api/drawings/{id}/build`: `{expected_version}` for a fresh proposal, or add `spec`,
  `reviewer` and `reason` to build a correction. Names here identify draft authors, not approval.
- `GET /api/drawings/{id}` and `/versions`: status, checks and immutable model manifests.
- `GET /api/drawings/{id}/files/{kind}`: `step`, `stl`, `mesh`, `spec`, `checks`,
  `model-manifest`, `model-ledger`, `associations`, `sections`, `section-XZ`, `section-YZ`.
- `POST /api/drawings/{id}/reviews`: `{expected_version,subject,layer,decision,reason}`.
  Configure `D2S_REVIEWER_TOKEN_HASHES` as a JSON map from reviewer names to SHA-256 token hashes;
  send the credential as `Authorization: Bearer ...`. Never put credentials in model prompts.
- `POST /api/drawings/{id}/releases`: `{expected_version,manifest_sha256}` and reviewer credential.
  Returns explicit blockers; production acceptance is not bypassable through this endpoint.

The local API uses one CAD worker and a process lock. It is not a multi-worker production
transaction system. Draft corrections and reviews preserve prior evidence and stale sign-offs.

## Run

```sh
uv sync --locked
npm ci --prefix ui
npm run build --prefix ui
uv run uvicorn drawing2step.web_api:app --host 127.0.0.1 --port 8000
```

For optional Document AI, install `uv sync --locked --extra cloud`, configure Application Default
Credentials and set `DOCUMENT_AI_PROCESSOR_VERSION` to an exact processor version resource:
`projects/PROJECT/locations/us/processors/PROCESSOR/processorVersions/VERSION`.
The initial adapter supports `us` and `eu`. Aliases such as `latest`/`stable` are rejected.
A Gemini API key does not configure a Document AI processor or its service-account permissions.

Model role overrides are `D2S_CONTEXT_MODEL`, `D2S_TEXT_MODEL`, `D2S_INVENTORY_MODEL`,
`D2S_LAYOUT_MODEL`, `D2S_SPEC_MODEL`, and `D2S_DISPUTE_MODEL`. IDs are recorded; an unavailable model produces an
unavailable stage, never a silent substitution. Layout defaults to `gemini-3.5-flash-lite`,
context/text/inventory to `gemini-3.5-flash`, and the reserved dispute role to
`gemini-3.8-flash`. Dispute orchestration remains gated work.

```sh
uv run drawing2step audit-drawing DRAWING.pdf --output work/revb/new-run --rotate 90
uv run drawing2step audit-drawing DRAWING.pdf --output work/revb/new-run --rotate 90 --replay
uv run drawing2step check-structure BODY.step EXPECTATIONS.json
uv run drawing2step eval-revb examples/revb-evaluation-template.json
```

Exit code 2 means blocked/review, not a successful acceptance. Every fresh run needs a new
output directory. Numerical expectations for `check-structure` are in millimetres/degrees in
an explicitly declared input frame; the tool does not infer a drawing-to-model alignment.

Local container (requires Docker; image build is not validated on this host):

```sh
docker build -t drawing2step-revb .
docker run --rm -p 127.0.0.1:8000:8000 --env-file .env drawing2step-revb
```

Use persistent `/app/work` storage with suitable ownership if retaining container results.
The container exposes a local unauthenticated review prototype, not a shared production service.

## Dataset manifest

Start with the empty evaluation template. Each case requires `id`, `group`, `quality`
(`clean`/`scan`), `split` (`development`/`held_out`), `synthetic`, `approved_by` and
`approval_reason`. An approved engineer must resolve drawing/reference discrepancies first.
`drawing`, `reference_step`, `truth` and `audit` each contain `{path, sha256}` relative to the
manifest. Set `frozen_candidate` to the audit's `candidate_fingerprint` after freezing a
candidate. Do not tune against individual held-out failures.

The truth JSON contains `inventory.features` in the Inventory schema and `structure`, a list
of independent expectations accepted by `check-structure`. The reference STEP must pass those
expectations before seeded failures can contribute to catch rate. Each `seeded_errors` entry
contains `step: {path, sha256}` and `subject`, the ID of the violated truth expectation.
A real drawing may not be duplicated under another case ID; identical reference geometry
cannot cross family groups. Twenty development and ten held-out approved real cases are the
minimum inherited from the original plan. These declarations do not replace authorised
reviewer authentication for future production releases.

The supplied `body-draft (1).step` is a negative example, not a correct reference pair. Six
engineering questions in Revision B remain unresolved, including reference faces, chamfer edge,
port depths, slot placement/length, plan orientation and the tapped-hole view discrepancy.

## Verification

Run `uv run pytest --cov=drawing2step --cov-fail-under=80`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy src`, and `npm run build --prefix ui`.
Recorded-response tests make no paid calls. Live paid tests are separate experiments.
Required regressions already cover wrong axial structure, absent units, implausible inch
envelopes, forbidden expressions, short drills, missing group counts, missing/extra/misplaced
holes and faceted STEP. The builder attributes split/ineffective cuts to named operations. Additional tests cover
review authentication, stale versions, artifact tampering, draft downloads during review,
section topology, dimensional typing and mixed-unit child readings.

## Primary references

- [Google model lifecycle](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/model-versions)
- [Document AI REST API](https://docs.cloud.google.com/document-ai/docs/reference/rest)
