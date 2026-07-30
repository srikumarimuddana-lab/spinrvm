"""Unit tests for services/data_transfer/sgi_template_versions.py — the
checksum-pinning registry for the real SGI regulator PDF templates.
"""

from __future__ import annotations

import pytest

try:
    from backend.services.data_transfer import sgi_template_versions
except ImportError:  # pragma: no cover
    from services.data_transfer import sgi_template_versions  # type: ignore

pytestmark = pytest.mark.unit


class TestStartupVerifySgiTemplates:
    def test_real_templates_pass_verification(self):
        # The checked-in templates must match the pinned checksums — if
        # this fails, either the templates were changed without updating
        # the registry, or the registry drifted. Either way this is the
        # exact failure this module exists to catch.
        sgi_template_versions.startup_verify_sgi_templates()

    def test_raises_on_checksum_mismatch(self, monkeypatch):
        real = sgi_template_versions.TEMPLATE_VERSIONS["driver_details"]
        tampered = sgi_template_versions.TemplateVersion(
            filename=real.filename, form_revision=real.form_revision, sha256="0" * 64
        )
        monkeypatch.setitem(sgi_template_versions.TEMPLATE_VERSIONS, "driver_details", tampered)
        with pytest.raises(sgi_template_versions.SgiTemplateChecksumMismatch, match="checksum mismatch"):
            sgi_template_versions.startup_verify_sgi_templates()

    def test_raises_on_missing_file(self, monkeypatch):
        real = sgi_template_versions.TEMPLATE_VERSIONS["vehicle_details"]
        missing = sgi_template_versions.TemplateVersion(
            filename="does_not_exist.pdf", form_revision=real.form_revision, sha256=real.sha256
        )
        monkeypatch.setitem(sgi_template_versions.TEMPLATE_VERSIONS, "vehicle_details", missing)
        with pytest.raises(sgi_template_versions.SgiTemplateChecksumMismatch, match="file is missing"):
            sgi_template_versions.startup_verify_sgi_templates()
