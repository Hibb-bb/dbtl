"""Append-only, hash-chained JSONL ledger.

Each line: {"seq", "ts", "kind", "round", "actor", "prev", "hash", "body"}.
`hash` = SHA-256 over the canonical JSON of the line without `hash`.
`prev` = the previous line's `hash` (64 zeros for the first line).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .errors import HarnessError

KINDS = {
    "campaign_init", "observation", "hypothesis", "batch_plan", "submission",
    "result", "round_review", "final", "rejection",
}
GENESIS = "0" * 64


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def line_hash(entry: dict) -> str:
    unhashed = {k: v for k, v in entry.items() if k != "hash"}
    return sha256_text(canonical(unhashed))


class ChainBroken(HarnessError):
    def __init__(self, line_no: int, why: str):
        self.line_no = line_no
        super().__init__("chain", f"ledger chain BROKEN at line {line_no}: {why}")


class Ledger:
    def __init__(self, ledger_dir: Path):
        self.dir = Path(ledger_dir)
        self.path = self.dir / "chain.jsonl"
        self.raw_dir = self.dir / "raw"

    # ---- reading -------------------------------------------------------
    def exists(self) -> bool:
        return self.path.exists() and self.path.stat().st_size > 0

    def entries(self, verify: bool = True) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        prev = GENESIS
        with open(self.path, encoding="utf-8") as f:
            for i, raw in enumerate(f, start=1):
                raw = raw.rstrip("\n")
                if not raw:
                    raise ChainBroken(i, "blank line")
                try:
                    e = json.loads(raw)
                except json.JSONDecodeError as ex:
                    raise ChainBroken(i, f"not valid JSON ({ex.msg})")
                if verify:
                    if e.get("seq") != i:
                        raise ChainBroken(i, f"seq {e.get('seq')!r} != line number {i}")
                    if e.get("prev") != prev:
                        raise ChainBroken(i, "prev does not match the previous line's hash")
                    if e.get("hash") != line_hash(e):
                        raise ChainBroken(i, "stored hash does not match the line content")
                    if e.get("kind") not in KINDS:
                        raise ChainBroken(i, f"unknown kind {e.get('kind')!r}")
                prev = e.get("hash")
                out.append(e)
        return out

    def verify(self) -> tuple[bool, str]:
        try:
            n = len(self.entries(verify=True))
        except ChainBroken as ex:
            return False, ex.message
        return True, f"chain ok ({n} entries)"

    # ---- writing -------------------------------------------------------
    def append(self, kind: str, body: dict, round_no: int, actor: str = "agent") -> dict:
        if kind not in KINDS:
            raise HarnessError("ledger", f"unknown ledger kind {kind!r}")
        entries = self.entries(verify=True)  # refuse to extend a broken chain
        prev = entries[-1]["hash"] if entries else GENESIS
        entry = {
            "seq": len(entries) + 1,
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "kind": kind,
            "round": round_no,
            "actor": actor,
            "prev": prev,
            "body": body,
        }
        entry["hash"] = line_hash(entry)
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(canonical(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return entry

    def store_raw(self, round_no: int, name: str, payload) -> tuple[str, str]:
        """Write a raw backend response untouched. Returns (relative path, sha256)."""
        d = self.raw_dir / str(round_no)
        d.mkdir(parents=True, exist_ok=True)
        path = d / name
        if path.exists():
            raise HarnessError("ledger", f"raw file already exists, refusing to overwrite: {path}")
        with open(path, "w", encoding="utf-8") as f:
            if isinstance(payload, str):
                f.write(payload)
            else:
                json.dump(payload, f, indent=2, sort_keys=True)
        return str(path.relative_to(self.dir.parent)), sha256_file(path)
