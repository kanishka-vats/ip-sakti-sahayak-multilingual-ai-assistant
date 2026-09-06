"""Deterministic benchmark runner for /api/query.

Prefers the live server at http://localhost:8000 (realistic wall-clock
latency); falls back to in-process TestClient when it is unreachable.

Usage:  uv run python eval/run_eval.py [suite ...]
        (default: all suites; results -> eval/results_<ts>.json)
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import time
from datetime import datetime

sys.path.insert(0, ".")

from eval.suites import SUITES

BASE = "http://localhost:8000"
REF_PAT = re.compile(r"\[\^(\d+)\]")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")
CLAIM_PAT = re.compile(
    r"section|article|\bact\b|rule|regulation|clause|treaty|convention|statute|"
    r"shall|must|require|patent|licen[sc]e|approval|tribunal|court|authority|"
    r"opposition|infring|novel|prior art|trips|\bpct\b|tkdl|nba\b",
    re.IGNORECASE)


class Backend:
    def __init__(self):
        self.mode = "testclient"
        self.client = None
        try:
            import httpx
            r = httpx.get(BASE + "/health", timeout=5.0)
            if r.status_code == 200:
                self.mode = "live"
                self.client = httpx.Client(base_url=BASE, timeout=180.0)
                print(f"[eval] live server: {r.json().get('chunks')}")
        except Exception as exc:
            print(f"[eval] no live server ({exc}); using in-process TestClient")
        if self.mode == "testclient":
            from fastapi.testclient import TestClient
            from src.api.main import app
            self.client = TestClient(app)

    def query(self, query: str, jurisdiction: str) -> tuple[dict, float]:
        t0 = time.perf_counter()
        r = self.client.post("/api/query",
                             json={"query": query, "jurisdiction": jurisdiction})
        dt_ms = (time.perf_counter() - t0) * 1000
        r.raise_for_status()
        return r.json(), dt_ms


def evaluate_turn(query: str, jurisdiction: str, expect_abs: bool,
                  resp: dict, latency_ms: float) -> dict:
    answer = resp.get("answer", "")
    cites = resp.get("citations", [])
    abstained = bool(resp.get("abstained"))
    refs = sorted({int(m) for m in REF_PAT.findall(answer)})
    refs_valid = (
        bool(refs)
        and all(1 <= n <= len(cites) for n in refs)
        and all(c.get("act_name") and c.get("section") and c.get("quote")
                for c in cites)
    )
    sents = [s.strip() for s in SENT_SPLIT.split(answer) if len(s.strip()) > 40]
    claim_sents = [s for s in sents if CLAIM_PAT.search(s)]
    cited_claims = [s for s in claim_sents if REF_PAT.search(s)]
    coverage = (len(cited_claims) / len(claim_sents)) if claim_sents else None
    grounded = (not abstained) and refs_valid
    if expect_abs:
        passed = abstained
    else:
        passed = (not abstained) and refs_valid
    return {
        "query": query, "jurisdiction": jurisdiction,
        "expect_abstain": expect_abs, "abstained": abstained,
        "passed": passed, "grounded": grounded,
        "refs": refs, "n_cites": len(cites), "refs_valid": refs_valid,
        "claim_sentences": len(claim_sents), "cited_claims": len(cited_claims),
        "claim_coverage": round(coverage, 3) if coverage is not None else None,
        "confidence": resp.get("confidence", 0.0),
        "top_score": resp.get("top_score", 0.0),
        "latency_ms": round(latency_ms, 1),
        "clarification": resp.get("clarification", False),
    }


def run_suite(be: Backend, name: str,
              items: list[tuple[str, str, bool]]) -> dict:
    turns = []
    for i, (q, j, exp) in enumerate(items, 1):
        try:
            resp, dt = be.query(q, j)
            t = evaluate_turn(q, j, exp, resp, dt)
        except Exception as exc:
            t = {"query": q, "jurisdiction": j, "expect_abstain": exp,
                 "abstained": None, "passed": False, "grounded": False,
                 "refs": [], "n_cites": 0, "refs_valid": False,
                 "claim_sentences": 0, "cited_claims": 0, "claim_coverage": None,
                 "confidence": 0.0, "top_score": 0.0, "latency_ms": -1.0,
                 "clarification": False, "error": str(exc)[:200]}
        turns.append(t)
        flag = "PASS" if t["passed"] else ("ABSTAIN" if t["abstained"] else "FAIL")
        print(f"  [{i:02d}/{len(items)}] {flag} "
              f"conf={t['confidence']:.2f} lat={t['latency_ms']:.0f}ms cites={t['n_cites']} "
              f"cov={t['claim_coverage']} :: {q[:64]}", flush=True)
    ok = [t for t in turns if not t.get("error")]
    non_abs = [t for t in ok if t["abstained"] is False]
    lat = sorted(t["latency_ms"] for t in ok if t["latency_ms"] >= 0)
    return {
        "suite": name, "total": len(items),
        "grounded_pct": round(100 * sum(1 for t in non_abs if t["grounded"]) / len(non_abs), 1) if non_abs else 0.0,
        "abstain_pct": round(100 * sum(1 for t in ok if t["abstained"]) / len(ok), 1) if ok else 0.0,
        "pass_rate": round(100 * sum(1 for t in ok if t["passed"]) / len(ok), 1) if ok else 0.0,
        "avg_conf": round(sum(t["confidence"] for t in ok) / len(ok), 3) if ok else 0.0,
        "p50_ms": round(statistics.median(lat), 1) if lat else -1.0,
        "controls_passed": sum(1 for t in ok if t["expect_abstain"] and t["passed"]),
        "controls_total": sum(1 for t in ok if t["expect_abstain"]),
        "mean_claim_coverage": round(statistics.mean(
            [t["claim_coverage"] for t in ok if t["claim_coverage"] is not None]), 3) if any(
            t["claim_coverage"] is not None for t in ok) else None,
        "turns": turns,
    }


def main() -> None:
    wanted = sys.argv[1:] or list(SUITES)
    be = Backend()
    out = {"timestamp": datetime.now().isoformat(timespec="seconds"),
           "backend": be.mode, "suites": []}
    for name in wanted:
        print(f"== suite: {name} ({len(SUITES[name])} queries) ==", flush=True)
        out["suites"].append(run_suite(be, name, SUITES[name]))
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = f"eval/results_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\nresults -> {path}\n")
    print("| Query set | Grounded % | Abstain % | Avg conf | Latency p50 |")
    print("|---|---|---|---|---|")
    for s in out["suites"]:
        print(f"| {s['suite']} | {s['grounded_pct']} | {s['abstain_pct']} | "
              f"{s['avg_conf']} | {s['p50_ms']} ms |")
    print(f"\n(backend={out['backend']}; pass_rate/controls in {path})")


if __name__ == "__main__":
    main()
