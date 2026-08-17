"""``LLMClient`` backed by the local **Claude Code CLI** (``claude -p``).

Writes the news through the ``claude`` command line the operator is already
logged into — the same auth used interactively, so no API key or gateway is
needed. The prompt is fed on **stdin** (not as a shell string), so untrusted
source content can never be interpreted by a shell.

Stdlib only (``subprocess``); no SDK. If ``claude`` isn't installed the call
raises, and the newsroom simply skips that bulletin (see ``serve._segment_reads``).
"""

from __future__ import annotations

import os
import shutil
import subprocess

from .base import LLMClient, LLMConfig


class ClaudeCliClient(LLMClient):
    """Complete a prompt by running ``claude -p`` and returning its stdout."""

    def __init__(self, binary: str | None = None, timeout: float = 180.0) -> None:
        # ``$STATEMEDIAFM_CLAUDE_BIN`` overrides the executable (e.g. a full path).
        self.binary = binary or os.environ.get("STATEMEDIAFM_CLAUDE_BIN", "claude")
        self.timeout = timeout

    def available(self) -> bool:
        """Is the ``claude`` executable on PATH (or an explicit path that exists)?"""
        return shutil.which(self.binary) is not None or os.path.exists(self.binary)

    def complete(self, prompt: str, cfg: LLMConfig) -> str:
        exe = shutil.which(self.binary) or self.binary
        cmd = [exe, "-p", "--output-format", "text"]
        # A bare CLI alias ("opus", "sonnet", …) may be passed as --model; a
        # LiteLLM-style "provider/model" isn't valid here, so the CLI default is used.
        model = (cfg.model or "").strip()
        if model and "/" not in model:
            cmd += ["--model", model]
        try:
            # Fixed argv + prompt on stdin (no shell); we inspect returncode below.
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                timeout=self.timeout, check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"claude CLI not found ({self.binary!r}); install Claude Code or set "
                "STATEMEDIAFM_CLAUDE_BIN, or switch the news writer to the gateway"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"claude CLI timed out after {self.timeout:.0f}s") from exc
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI failed ({proc.returncode}): {(proc.stderr or '').strip()[:200]}"
            )
        out = (proc.stdout or "").strip()
        if not out:
            raise RuntimeError("claude CLI returned no output")
        return out
