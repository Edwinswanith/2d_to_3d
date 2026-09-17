"""Deployed-build identity: exposed in health checks and every job manifest.

Answers exactly the question a customer-support investigation needs first: which runtime and
which source actually served a given job. A running container may not have `.git` (a Docker
image copies source only, not history), so the commit is read from an environment variable
set at build/deploy time, falling back to `git rev-parse HEAD` in a development checkout, then
to "unknown" — never guessed, never silently omitted.
"""

import os
import subprocess
from functools import lru_cache
from typing import Any

from drawing2step.revb_model import BUILDER_VERSION, CAPABILITIES
from drawing2step.revb_pipeline import PIPELINE_VERSION

# Sources checked in order: CI/deploy platforms set one of these; a dev checkout has none.
_COMMIT_ENV_VARS = ("GIT_COMMIT", "VERCEL_GIT_COMMIT_SHA", "SOURCE_COMMIT")


@lru_cache(maxsize=1)
def commit_sha() -> str:
    for var in _COMMIT_ENV_VARS:
        value = os.environ.get(var)
        if value:
            return value
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=2
            )
            .decode()
            .strip()
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def deployment_info() -> dict[str, Any]:
    """Fields to merge into a health response and every job manifest."""
    return {
        "pipeline_version": PIPELINE_VERSION,
        "builder_version": BUILDER_VERSION,
        "commit": commit_sha(),
        "supported_geometry": sorted(CAPABILITIES),
    }
