"""Isolated execution of the pinned Analytics F12 preparer.

The subprocess receives only temporary file paths. Original evidence is never
placed on the command line, stdout, stderr, or application logs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Sequence


PUBLISHED_PREPARER_COMMIT = "94ff3a82ad3581e74cb6f97449adda264972c61b"
_VENDOR_ROOT = Path(__file__).resolve().parents[2] / "vendor" / "modera_f12_94ff3a8"
_RESULT_MAX_BYTES = 8 * 1024 * 1024


class F12PublishedPreparationError(Exception):
    pass


def run_published_preparer(
    binding: dict,
    pairs: Sequence[tuple[bytes, bytes]],
    *,
    timeout_seconds: float = 30,
) -> dict:
    """Run the immutable upstream code against exact byte pairs."""

    with tempfile.TemporaryDirectory(prefix="modera-f12-") as temporary:
        workspace = Path(temporary)
        (workspace / "binding.json").write_text(
            json.dumps(
                binding,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ),
            encoding="utf-8",
        )
        pair_files = []
        for ordinal, (summary_bytes, events_bytes) in enumerate(pairs):
            summary_name = f"summary-{ordinal:05d}.bin"
            events_name = f"events-{ordinal:05d}.bin"
            (workspace / summary_name).write_bytes(summary_bytes)
            (workspace / events_name).write_bytes(events_bytes)
            pair_files.append([summary_name, events_name])
        (workspace / "manifest.json").write_text(
            json.dumps({"pairs": pair_files}, separators=(",", ":")),
            encoding="utf-8",
        )

        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "app.services.f12_published_runner",
                    str(workspace),
                ],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise F12PublishedPreparationError(
                "The pinned F12 preparer could not be executed"
            ) from exc
        if completed.returncode != 0:
            raise F12PublishedPreparationError(
                "The persisted evidence does not satisfy the published F12 contract"
            )

        output_path = workspace / "result.json"
        if not output_path.is_file() or output_path.stat().st_size > _RESULT_MAX_BYTES:
            raise F12PublishedPreparationError(
                "The pinned F12 preparer returned an invalid result"
            )
        try:
            result = json.loads(output_path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise F12PublishedPreparationError(
                "The pinned F12 preparer returned an invalid result"
            ) from exc
        if not isinstance(result, dict) or set(result) != {"request", "audit"}:
            raise F12PublishedPreparationError(
                "The pinned F12 preparer returned an invalid result"
            )
        return result


def _run_child(workspace: Path) -> None:
    if not _VENDOR_ROOT.is_dir():
        raise RuntimeError("Pinned F12 source tree is missing")
    sys.path.insert(0, str(_VENDOR_ROOT))
    from analytics_service.f12_export import prepare_android_export

    binding = json.loads((workspace / "binding.json").read_bytes())
    manifest = json.loads((workspace / "manifest.json").read_bytes())
    pairs = [
        (
            (workspace / summary_name).read_bytes(),
            (workspace / events_name).read_bytes(),
        )
        for summary_name, events_name in manifest["pairs"]
    ]
    prepared = prepare_android_export(binding, pairs)
    result = {"request": prepared["request"], "audit": prepared["audit"]}
    (workspace / "result.json").write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(2)
    _run_child(Path(sys.argv[1]).resolve())
