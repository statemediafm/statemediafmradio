"""Open-core licensing: commercial modules gated by an entitlement key.

Maelcom is **open-core**. The base station — sources, the deterministic news
copy, the generative music, the browser player — is free and fully offline and
requires no key. A **commercial distribution** adds *modules* (e.g. themed voice
personas) that are unlocked by a **license key**.

Design goals for a self-hostable product:

- **Offline verification, no phone-home.** A key is a signed token verified
  locally; nothing is sent to a server.
- **Stdlib only** (``hmac``/``hashlib``/``base64``/``json``), so this still works
  inside the zero-dependency zipapp.
- **One enforcement pattern.** A commercial feature registers a module slug and
  wraps its enable-points with :func:`require` / :func:`entitled`. Everything else
  stays free.

The default verifier here is an **HMAC-signed token** — adequate to *scaffold*
the gate and issue dev keys, but a shipped product must swap ``_secret`` for
asymmetric verification (an Ed25519/RSA **public** key baked in, private key held
by the vendor) or a signing license server, because a shared HMAC secret lives in
the verifying binary and can be extracted. See PLAN.md §5.9.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
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
    """Where the license key file lives (gitignored). ``$MAELCOM_LICENSE_FILE``
    overrides; otherwise ``maelcom.license`` in the cwd."""
    if path is not None:
        return Path(path)
    return Path(os.environ.get("MAELCOM_LICENSE_FILE", "maelcom.license"))


def license_key(path: str | os.PathLike | None = None) -> str | None:
    """The active license key: ``$MAELCOM_LICENSE`` if set, else the license file,
    else ``None`` (open-core only)."""
    env = os.environ.get("MAELCOM_LICENSE")
    if env:
        return env.strip()
    p = license_path(path)
    if p.exists():
        key = p.read_text(encoding="utf-8").strip()
        return key or None
    return None


def save_license(key: str, path: str | os.PathLike | None = None) -> Path:
    """Persist a license key to the gitignored license file, owner-only."""
    p = license_path(path)
    p.write_text(key.strip() + "\n", encoding="utf-8")
    p.chmod(0o600)
    return p


# ── Verification (scaffold: HMAC-signed token — see module docstring) ─────────


def _secret() -> bytes:
    """The signing secret. Overridable via ``$MAELCOM_LICENSE_SECRET`` for dev /
    tests. **A shipped product must replace this with asymmetric verification.**"""
    return os.environ.get("MAELCOM_LICENSE_SECRET", "MAELCOM-DEV-SECRET-CHANGE-ME").encode()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_license(modules, *, exp: float | None = None, sub: str = "maelcom") -> str:
    """Issue a signed key unlocking ``modules`` (``["*"]`` for all). **Vendor-side
    tooling** — the scaffold uses it for dev keys and tests. ``exp`` is an epoch
    expiry (``None`` = perpetual)."""
    payload = {"modules": sorted(modules), "exp": exp, "sub": sub}
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64url(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def _verify(key: str, *, now: float | None = None) -> frozenset[str]:
    """The module slugs a key unlocks, or empty on a bad/expired/forged key."""
    try:
        body, sig = key.strip().split(".", 1)
        expected = _b64url(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return frozenset()
        payload = json.loads(_unb64url(body))
    except (ValueError, TypeError, json.JSONDecodeError):
        return frozenset()
    exp = payload.get("exp")
    if exp is not None and (now if now is not None else time.time()) > exp:
        return frozenset()
    return frozenset(payload.get("modules") or [])


# ── Entitlements (the enforcement surface) ───────────────────────────────────


def entitlements(key: str | None = None) -> frozenset[str]:
    """The unlocked module slugs for ``key`` (defaults to the active license)."""
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
