"""Vercel entrypoint. Runs the same Revision B runtime as the Docker deployment.

The legacy faceted-body workspace (`drawing2step.legacy_web_api`) is retired: it is no
longer wired to any deployment, so a feature-complete job (holes, ports, slots, threads)
can never silently land on the exporter that classifies those as unsupported. See
`docs/PLAN_SOURCE_CONFORMANCE.md` Milestone A.

Known limitation carried over from this change, not yet solved: `web_api.create_app`'s
`root` is local disk (`/tmp` here, matching Vercel's writable path), with no storage
abstraction across serverless invocations. The legacy runtime used Cloudflare R2
(`web_storage.py`) specifically because a poll request can land on a different function
instance than the one running the background build thread, and `/tmp` is not shared or
guaranteed to persist across instances. Revision B does not yet have an equivalent durable
storage layer. Until it does, treat the Docker deployment (`Dockerfile`, persistent disk)
as the reliable target for real jobs; this entrypoint is appropriate for a single warm
instance or local `vercel dev`, not for verified multi-instance production traffic.
"""

from pathlib import Path

from drawing2step.web_api import create_app

root = Path("/tmp/drawing2step-web")
app = create_app(root=root, frontend=Path(__file__).resolve().parent / "frontend")
