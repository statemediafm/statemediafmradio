"""Tests for the open-core licensing / entitlement layer."""

from __future__ import annotations

import pytest

from statemediafm.licensing import (
    LicenseError,
    entitled,
    entitlements,
    license_key,
    require,
    save_license,
    sign_license,
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


def test_signed_key_unlocks_named_modules(monkeypatch):
    monkeypatch.setenv("STATEMEDIAFM_LICENSE", sign_license(["voice-personas"]))
    assert entitled("voice-personas") is True
    assert entitled("something-else") is False
    require("voice-personas")  # no raise


def test_wildcard_key_unlocks_everything(monkeypatch):
    monkeypatch.setenv("STATEMEDIAFM_LICENSE", sign_license(["*"]))
    assert entitled("voice-personas") and entitled("anything-at-all")


def test_forged_or_tampered_key_unlocks_nothing(monkeypatch):
    key = sign_license(["voice-personas"])
    body, _sig = key.split(".", 1)
    monkeypatch.setenv("STATEMEDIAFM_LICENSE", body + ".deadbeef")  # bad signature
    assert entitlements() == frozenset()
    monkeypatch.setenv("STATEMEDIAFM_LICENSE", "garbage")
    assert entitlements() == frozenset()


def test_expired_key_unlocks_nothing():
    # exp in the past → not entitled.
    key = sign_license(["voice-personas"], exp=1.0)
    assert entitled("voice-personas", key=key) is False


def test_license_file_is_read(tmp_path, monkeypatch):
    monkeypatch.setenv("STATEMEDIAFM_LICENSE_FILE", str(tmp_path / "lic"))
    save_license(sign_license(["voice-personas"]))
    assert license_key() is not None
    assert entitled("voice-personas") is True
