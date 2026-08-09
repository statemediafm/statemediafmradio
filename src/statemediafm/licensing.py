"""Open-core licensing: commercial modules gated by an entitlement key.

State Media FM is **open-core**. The base station — sources, the deterministic news
copy, the generative music, the browser player — is free and fully offline and
requires no key. A **commercial distribution** adds *modules* (e.g. themed voice
personas) that would be unlocked by a **license key**.

**Verification is STUBBED.** The earlier HMAC scaffold was removed: a shared
signing secret baked into the verifying binary is trivially extractable and lets
anyone forge a key. Until an **asymmetric** verifier is wired (an Ed25519/RSA
*public* key compiled into the build, keys signed by a vendor-held private key —
still fully offline, no phone-home), :func:`_verify` unlocks nothing, so every
commercial module stays locked (the safe default) and the whole open-core base
remains free. The registry + enforcement surface (:func:`register_module`,
:func:`require`, :func:`entitled`) and the key storage below are kept intact so a
future iteration only has to drop in the real verifier. See SECURITY_MODEL.md and
PLAN.md §5.9.

Stdlib only, so this works inside the zero-dependency zipapp.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# ── Commercial-module registry ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Module:
    """A commercially-licensed feature: a stable ``slug`` + human metadata."""

    slug: str
    name: str
    description: str


COMMERCIAL_MODULES: dict[str, Module] = {}


def register_module(slug: str, name: str, description: str) -> None:
    """Declare a commercial module (idempotent). Its enable-points then guard on
    ``entitled(slug)`` / ``require(slug)``."""
    COMMERCIAL_MODULES[slug] = Module(slug, name, description)


# ── License key resolution ───────────────────────────────────────────────────


class LicenseError(RuntimeError):
    """Raised when a commercial module is used without a valid entitlement."""


def license_path(path: str | os.PathLike | None = None) -> Path:
    """Where the license key file lives (gitignored). ``$STATEMEDIAFM_LICENSE_FILE``
    overrides; otherwise ``statemediafm.license`` in the cwd."""
    if path is not None:
        return Path(path)
    return Path(os.environ.get("STATEMEDIAFM_LICENSE_FILE", "statemediafm.license"))


def license_key(path: str | os.PathLike | None = None) -> str | None:
    """The active license key: ``$STATEMEDIAFM_LICENSE`` if set, else the license file,
    else ``None`` (open-core only)."""
    env = os.environ.get("STATEMEDIAFM_LICENSE")
    if env:
        return env.strip()
    p = license_path(path)
    if p.exists():
        key = p.read_text(encoding="utf-8").strip()
        return key or None
    return None


def save_license(key: str, path: str | os.PathLike | None = None) -> Path:
    """Persist a license key to the gitignored license file, owner-only. (The key
    is stored but does not unlock anything until asymmetric verification lands.)"""
    p = license_path(path)
    p.write_text(key.strip() + "\n", encoding="utf-8")
    p.chmod(0o600)
    return p


# ── Verification (STUBBED — see module docstring / SECURITY_MODEL.md) ─────────


def _verify(key: str, *, now: float | None = None) -> frozenset[str]:
    """The module slugs a license key unlocks — currently **none**.

    The prior HMAC scaffold (a shared secret in the verifying binary, forgeable)
    was removed. A future iteration wires ASYMMETRIC verification here — an
    Ed25519/RSA *public* key compiled into the build verifies keys signed by a
    vendor-held private key, still fully offline. Until then no key verifies, so
    every commercial module stays locked (the safe default).
    """
    _ = (key, now)  # accepted for API stability; ignored until real verification lands
    return frozenset()


# ── Entitlements (the enforcement surface) ───────────────────────────────────


def entitlements(key: str | None = None) -> frozenset[str]:
    """The unlocked module slugs for ``key`` (defaults to the active license).
    Empty while verification is stubbed."""
    key = key if key is not None else license_key()
    return _verify(key) if key else frozenset()


def entitled(slug: str, key: str | None = None) -> bool:
    """Is the commercial module ``slug`` unlocked? (``"*"`` unlocks everything.)"""
    ent = entitlements(key)
    return slug in ent or "*" in ent


def require(slug: str, key: str | None = None) -> None:
    """Raise :class:`LicenseError` unless ``slug`` is entitled."""
    if not entitled(slug, key):
        mod = COMMERCIAL_MODULES.get(slug)
        label = mod.name if mod else slug
        raise LicenseError(f"{label!r} is a commercial module; a license key is required")


def license_status(key: str | None = None) -> dict:
    """A UI-facing summary: which modules exist and whether each is unlocked."""
    ent = entitlements(key)
    return {
        "has_key": bool(key if key is not None else license_key()),
        "modules": [
            {"slug": m.slug, "name": m.name, "description": m.description,
             "entitled": (m.slug in ent or "*" in ent)}
            for m in COMMERCIAL_MODULES.values()
        ],
    }
