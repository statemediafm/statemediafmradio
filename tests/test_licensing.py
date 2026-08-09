"""Tests for the open-core licensing / entitlement layer.

Verification is stubbed (the forgeable HMAC scaffold was removed), so no key
unlocks anything yet — every commercial module stays locked, the open-core base
stays free. These tests pin that safe-default behaviour and the enforcement
surface so a future asymmetric verifier can be dropped in.
"""

from __future__ import annotations

import pytest

from statemediafm.licensing import (
    LicenseError,
    entitled,
    entitlements,
    license_key,
    register_module,
    require,
    save_license,
)


@pytest.fixture(autouse=True)
def _isolate_license(monkeypatch, tmp_path):
    # No ambient license; a temp file so nothing leaks between tests.
    monkeypatch.delenv("STATEMEDIAFM_LICENSE", raising=False)
    monkeypatch.setenv("STATEMEDIAFM_LICENSE_FILE", str(tmp_path / "statemediafm.license"))


def test_open_core_has_no_entitlements_by_default():
    assert entitlements() == frozenset()
    assert entitled("voice-personas") is False
    with pytest.raises(LicenseError, match="commercial module"):
        require("voice-personas")


def test_verification_is_stubbed_so_no_key_unlocks_anything(monkeypatch):
    # Any key — a plausible token, a wildcard, garbage — unlocks nothing while
    # verification is stubbed pending asymmetric signing.
    for key in ("some.signed-looking.key", "*", "garbage", ""):
        monkeypatch.setenv("STATEMEDIAFM_LICENSE", key)
        assert entitlements() == frozenset()
        assert entitled("voice-personas") is False


def test_require_raises_for_a_registered_locked_module():
    register_module("test-module", "Test Module", "for the test")
    with pytest.raises(LicenseError, match="Test Module"):
        require("test-module")


def test_license_file_is_stored_but_unlocks_nothing_yet(tmp_path, monkeypatch):
    monkeypatch.setenv("STATEMEDIAFM_LICENSE_FILE", str(tmp_path / "lic"))
    save_license("a-key-the-user-pasted")
    assert license_key() == "a-key-the-user-pasted"  # stored + readable ...
    assert entitled("voice-personas") is False  # ... but nothing verifies yet
