# Implementation handoff and milestone status

The user-approved architecture is implemented in gated order. The empty repository and
missing paired dataset/cloud permission were confirmed on 16 September 2026. This commit's
scope is deliberately restricted to prerequisite tooling and synthetic feasibility work.

## Available now

- A Python package and locked development toolchain, CLI, CI, and generated schema commands.
- Read-only pair inventory, hash validation, split leakage detection, permission coverage,
  baseline reference checks, and explicit prerequisite reports.
- Synthetic requirement/readings contracts, deterministic precedence experiments, conservative
  evaluation scoring, atomic content-addressed run artifacts, and an annotated HTML report.
- Fault-injection and integrity tests. No synthetic result grants CAD or release acceptance.

These are not completion claims for the real evaluation harness or S0–S9 pipeline. The
synthetic contracts are experimental and must be expanded/versioned at the next milestone.

### Subsequent single-PDF test authorization

The user supplied a Gemini key and explicitly requested a test of a local PDF. A separate
`inspect-pdf` command now provides local intake/rendering plus an optional Gemini Developer
API reading diagnostic. It retains native spans, 300/600 dpi renders, original hashes,
coordinate transforms, raw response, prompt/schema, and an unaccepted proposed ledger.
This narrow diagnostic does not satisfy dataset, independent OCR, accuracy or release gates.
The credential stays in the ignored `.env`; drawing artifacts stay under ignored `work/`.

### Subsequent blocker-resolution implementation

The local prototype now includes spatial character corroboration retaining all OCR evidence,
explicit engineering association validation tied to the exact reconciled ledger hash, and a
CadQuery 2.6.1 axial-body evaluation builder on Python 3.12. The builder supports cited lengths
and bounded dimensional arithmetic, ordered nested profiles, STEP export/reimport, analytical
volume validation, fresh cylindrical measurements, and optional reference-shape boolean comparison.
These additions are evaluation capabilities, not completion of the production CAD/release phases.
V1 and association/location conformance remain UNKNOWN where measurements are not implemented.
No geometry from the supplied drawing has been built without its unresolved unit decision.

## Gates and work still required

| Milestone | Work | Gate |
| --- | --- | --- |
| 0: prerequisites | Owner permission, 30–50 real pairs, 5–10 timed baselines, workload share, reviewers, environment verification. | Permission and paired data available; humans verify declared records. |
| 1: ground truth | Label 20 development and ≥10 held-out cases, adjudicate historical STEP discrepancies, validate independent B-rep measurement. | Every reference discrepancy explained; unsupported checks UNKNOWN. |
| 2–3: evidence ledger | Implement S0–S9, actual reader adapters, context resolution, reconciliation, associations, reports, costs, latency and resume. | Clean recall ≥98%, accepted error ≤0.5%; assess scans separately. |
| 4: body geometry | S10–S14 for revolve profiles, grooves and chamfers, safe derivations, STEP reimport and independent checks. | ≥80% correct clean bodies, zero accepted geometry errors, ≥95% seeded association-error detection. |
| 5: review/pilot | Authenticated drawing/model/review interface, version-bound decisions, completeness, release gate, shadow pilot. | Measurable time saving; below 20% pause CAD expansion. |
| 6: holes/patterns | Through/blind/tapped holes, slots, pins, PCD, count and clearance checks. | No body regression; zero accepted geometry errors. |
| 7: ports | Port interpretation, entry faces, compound angles, drill paths and intersection proofs. | Dense examples accepted by engineer; retain manual guidance if automation is inefficient. |
| 8: operations | Cloud orchestration, backup/restore, retention, monitoring, cost dashboard, upgrades and runbook. | CAM import, regression acceptance, concurrency/retry/restore tests. |

If reading recall remains below 90% or accepted error above 2% after two development prompt
iterations, stop and reconsider the reader design before any CAD work. Do not inspect and
tune against individual held-out failures. Use frozen candidates for held-out acceptance.

## Next implementation slice after prerequisite clearance

1. Build PostgreSQL migrations for drawings/revisions, stage attempts, immutable evidence,
   readings, requirements, associations, corrections, versions, checks and releases.
2. Add filesystem/Cloud Storage artifact adapters. Retain originals, raw model responses,
   renders, crops, page transforms, model IDs, prompt/schema/rule versions and configuration.
3. Implement S0 intake and 300/600 dpi rendering, with native extraction, independent fixed-grid
   OCR and layout branches. No inferred drawing identity from filenames.
4. Add typed provider adapters and bounded retries. Unavailable readers remain explicit.
   Replay stored provider responses; new calls create new evidence, regardless of temperature.
5. Implement per-callout units, defaults, union reconciliation, the full dispute precedence
   table, early-stop rules, and type/view/count validation for proposed associations.
6. Implement `ingest`, `run --through S9`, `resume`, `report` and real-data `eval`. Persist stage
   fingerprints and attempts. Changed configuration or evidence invalidates dependent stages.
7. Establish the real development/held-out benchmark and publish cost, latency, recall,
   accepted-error, association and UNKNOWN metrics before deciding whether CAD can begin.

Use Python/Pydantic, PyMuPDF, OpenCV, Pillow, the Google Gen AI and Document AI SDKs, PostgreSQL,
FastAPI, CadQuery/OCCT, and eventually React/TypeScript with PDF.js/three.js as approved. Pin
dependency versions when each subsystem is introduced; do not create unused placeholder apps.

## CAD and release invariants for subsequent milestones

- Every geometry value cites a requirement or a code-evaluated derivation. Restricted expression
  trees must check units, dependencies and cycles; never execute model-authored code.
- Unsupported geometry routes the whole drawing to ledger-only output. Partial body STEP files
  are evaluation artifacts, never release candidates.
- V3 measures the reimported STEP without trusting the spec's associations. Match location,
  multiplicity, orientation and relationships, not just numerical values; ambiguity is UNKNOWN.
- Use logical feature IDs for review/tessellation; never assume OCCT face indices survive rebuilds.
- Reviewer writes carry an expected version; reject stale writes. Corrections create new versions,
  rerun relevant checks, and invalidate old sign-offs.
- Before a shared customer-data pilot, implement authentication, reviewer roles and audit events.
- Release atomically binds drawing, ledger, spec, STEP hash, checks and sign-offs. No FAIL is
  waivable, every geometry UNKNOWN needs individual sign-off, disputes and missing associations
  block release, every feature must trace to a requirement, completeness must be confirmed,
  and STEP reimport must pass.

The original 20-week estimate assumes one full-time developer, one part-time developer and
three CAD-engineer hours weekly. Re-estimate after measured ledger results. No live services
have been provisioned and no production milestones are asserted complete.
