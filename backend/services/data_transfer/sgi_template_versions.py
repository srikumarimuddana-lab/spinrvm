"""Version registry for the real SGI regulator PDF templates
(``backend/static/sgi_forms/``).

Why this exists: these are not Spinr's own documents — they're SGI's
official "Passenger for Hire – Driver Details" (D00032) and "Transportation
Network Company – Vehicle Details" (D00033) forms, each printed with SGI's
own revision stamp (e.g. "04/2021") on the last page. A template swap here
is a compliance-relevant change, not a routine asset update: swap in a
tampered or stale file and every generated regulator submission is wrong,
possibly silently (see docs/reporting/sgi-form-field-mapping.md and
sgi_form_filler.py's own notes on the field-name inconsistencies these
exact templates have).

This module pins the SHA-256 of each template file this codebase was built
and tested against, plus the revision date printed on the form itself.
``startup_verify_sgi_templates()`` is called once at process startup
(``core/lifespan.py``) and raises loudly on a mismatch — a silently-swapped
template fails the deploy instead of silently generating wrong regulator
forms in production. This does NOT check the templates in per-request;
that would be wasted I/O for a file that only ever changes via a reviewed
code change.

Updating this file when SGI issues a new form revision:
1. Replace the template PDF under backend/static/sgi_forms/.
2. Read the new revision date off the form's own footer stamp (bottom-left
   of the last page, e.g. "1/2 | D00032 | 04/2021").
3. Recompute the checksum: `sha256sum backend/static/sgi_forms/<file>.pdf`.
4. Update the matching TemplateVersion entry below in the SAME commit as
   the template file — never as a follow-up, so code review sees both the
   binary diff and the version-registry diff together.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "static" / "sgi_forms"


@dataclass(frozen=True)
class TemplateVersion:
    filename: str
    # Revision date printed on the form itself (SGI's own footer stamp,
    # e.g. "04/2021" for "April 2021") — not a Spinr-assigned version.
    form_revision: str
    sha256: str


TEMPLATE_VERSIONS: dict[str, TemplateVersion] = {
    "driver_details": TemplateVersion(
        filename="D00032_driver_details_template.pdf",
        form_revision="04/2021",
        sha256="9dc4089603d6c93b9f6dc73a8e7e1189a8f1ceef836e1adebdf3fdccb3c7e822",
    ),
    "vehicle_details": TemplateVersion(
        filename="D00033_vehicle_details_template.pdf",
        form_revision="04/2021",
        sha256="0b081a59a2e2382366b44570474b28b4657c066fb9a490d61e0031e3f1b26445",
    ),
}


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SgiTemplateChecksumMismatch(RuntimeError):
    pass


def startup_verify_sgi_templates() -> None:
    """Raise if any registered template's on-disk checksum doesn't match
    TEMPLATE_VERSIONS — called once at process startup. A mismatch means
    either the template file was changed without updating this registry
    (a review-process gap this file exists to catch) or the file was
    corrupted/tampered in deployment. Either way, fail the deploy loudly
    rather than silently generate SGI submissions from an unverified form.
    """
    for form_type, version in TEMPLATE_VERSIONS.items():
        path = _TEMPLATE_DIR / version.filename
        if not path.is_file():
            raise SgiTemplateChecksumMismatch(
                f"SGI template registry references {version.filename} for "
                f"'{form_type}' but the file is missing at {path}"
            )
        actual = _sha256_of(path)
        if actual != version.sha256:
            raise SgiTemplateChecksumMismatch(
                f"SGI template '{version.filename}' (form_type={form_type}) checksum mismatch — "
                f"expected {version.sha256}, found {actual}. If this template was legitimately "
                f"updated (new SGI form revision), update TEMPLATE_VERSIONS in "
                f"sgi_template_versions.py in the SAME commit as the file change."
            )
        logger.info(
            "SGI template verified: %s (form_type=%s, revision=%s)",
            version.filename,
            form_type,
            version.form_revision,
        )
