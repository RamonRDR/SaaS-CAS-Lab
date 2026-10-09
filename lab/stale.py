"""Negative experiment: force:false does NOT validate semantic ledger revision.

Only mutates an already-created disposable ref in the SaaS-CAS-Lab repository.
If test succeeds, it demonstrates why a fast-forward-only Git ref update is not
a full application-level compare-and-swap (CAS).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from lab.client import Client, experiment_ref
from lab.probe import BUDGET_LIMIT, assert_ledger


def prove(ref: str, out: str) -> None:
    if ref != experiment_ref():
        raise RuntimeError("Wrong disposable ref")
    client = Client()
    before_sha = client.tip(ref)
    before = client.ledger(ref)
    original_ids = assert_ledger(before)
    if len(original_ids) != BUDGET_LIMIT or before["generation"] < 5:
        raise RuntimeError("Full budget proof must precede stale-descendant probe")
    # Simulate an executor that computes a new tree from an *outdated*
    # business snapshot, but attaches it to the CURRENT Git parent.
    # Git sees a valid fast-forward, while business revision regresses.
    stale = dict(before)
    stale["generation"] = 2
    stale["reservations"] = [dict(before["reservations"][0])]
    stale["last"] = "intentionally-stale-application-snapshot"
    stale_sha = client.candidate(before_sha, stale, "negative stale fast-forward overwrite")
    response = client.try_advance(ref, stale_sha)
    observed_sha = client.tip(ref)
    observed = client.ledger(ref)

    proof = {
        "experiment": "negative-stale-fast-forward",
        "prior_sha": before_sha,
        "prior_generation": before["generation"],
        "prior_reservations": len(original_ids),
        "candidate_sha": stale_sha,
        "http_status": response.status,
        "observed_sha": observed_sha,
        "observed_generation": observed["generation"] if observed else None,
        "observed_reservations": len(observed["reservations"]) if observed else None,
        "force": False,
        "unsafe_overwrite_observed": (
            response.status == 200 and observed_sha == stale_sha and observed == stale
        ),
        "restored": False,
        "scope": "one disposable branch, no AI calls or main branch writes",
    }
    if not proof["unsafe_overwrite_observed"]:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
        raise RuntimeError("Stale fast-forward was not accepted; inspect evidence")
    print("STALE_FAST_FORWARD_CONFIRMED: HTTP 200 accepted outdated application state")
    print(f"BUSINESS_REVISION_REGRESSION previous={before['generation']} observed={observed['generation']}")

    # Restore the *exact original reservations* before cleanup. This is still
    # merely a disposable experiment: any unexpected failure fails closed.
    restored = dict(before)
    restored["generation"] = before["generation"] + 1
    restored["last"] = "restored-after-negative-test"
    repair_sha = client.candidate(stale_sha, restored, "restore tested budget snapshot")
    repair_result = client.try_advance(ref, repair_sha)
    actual = client.ledger(ref)
    proof["restored"] = (
        repair_result.status == 200 and client.tip(ref) == repair_sha
        and actual == restored
        and len(assert_ledger(actual)) == BUDGET_LIMIT
    )
    proof["restored_sha"] = repair_sha
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not proof["restored"]:
        raise RuntimeError("Restoration of disposable ledger could not be verified")
    print("STALE_FAST_FORWARD_NEGATIVE_PROOF_OK: restored 4/4 budget before cleanup")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    prove(args.ref, args.out)
