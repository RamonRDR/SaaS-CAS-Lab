"""Real multi-runner Git-ref CAS probe.

All Git mutations are restricted to one run-specific ref under
refs/heads/experiment/cas-probe-*. No API provider costs or untrusted PR code.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from lab.client import Client, REPO, experiment_ref, require_environment

WORKERS = ("alpha", "beta")
OP_IDS = ("op-alpha", "op-beta", "op-03", "op-04", "op-05", "op-06")
BUDGET_LIMIT = 4


def write_json(path: str, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def mark_output(key: str, val: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        raise RuntimeError("Missing GitHub Actions output path")
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(f"{key}={val}\n")


def require_match(value: str, expected: str, label: str) -> None:
    if value != expected:
        raise RuntimeError(f"Evidence {label} mismatch: fail-closed")


def snapshot(phase: str, worker: str, parent: str) -> dict:
    if phase == "claim":
        return {
            "schema": "cas-lab-v1", "generation": 1,
            "claim_owner": worker, "budget_limit_minor": BUDGET_LIMIT,
            "reservations": [], "last": "claim-" + worker,
        }
    if phase == "budget":
        return {
            "schema": "cas-lab-v1", "generation": 2,
            "claim_owner": os.environ["CLAIM_OWNER"],
            "budget_limit_minor": BUDGET_LIMIT,
            "reservations": [{"request_id": "op-" + worker, "max_cost_minor": 1}],
            "last": "budget-" + worker,
        }
    raise ValueError("Unexpected phase")


def assert_ledger(data: dict):
    if data.get("schema") != "cas-lab-v1" or data.get("claim_owner") not in WORKERS:
        raise RuntimeError("Persisted ledger has invalid schema or winner")
    if data.get("budget_limit_minor") != BUDGET_LIMIT:
        raise RuntimeError("Budget limit unexpectedly changed")
    reservations = data.get("reservations", [])
    ids = [r["request_id"] for r in reservations]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Duplicate reservation detected")
    if any(r["max_cost_minor"] != 1 or r["request_id"] not in OP_IDS for r in reservations):
        raise RuntimeError("Invalid reservation identity/cost")
    if sum(x["max_cost_minor"] for x in reservations) > BUDGET_LIMIT:
        raise RuntimeError("Budget cap exceeded")
    return ids


def bootstrap(args):
    api = Client()
    ref = experiment_ref()
    sha = os.environ["GITHUB_SHA"]
    if api.tip(ref) is not None:
        raise RuntimeError("Run-specific test ref already exists")
    api.create_ref(ref, sha)
    if api.tip(ref) != sha:
        raise RuntimeError("Bootstrap ref was not persisted")
    mark_output("ref", ref)
    mark_output("baseline", sha)
    print(f"CAS_BOOTSTRAP_OK ref={ref} baseline={sha}")


def prepare(args):
    api = Client()
    ref = experiment_ref()
    require_match(args.ref, ref, "ref")
    if args.worker not in WORKERS:
        raise RuntimeError("Not a known worker")
    if args.phase == "claim":
        parent = os.environ["GITHUB_SHA"]
    else:
        parent = args.parent
        current = api.ledger(ref)
        if not current or current["generation"] != 1:
            raise RuntimeError("Budget prepared before persisted claim")
        require_match(current["claim_owner"], os.environ["CLAIM_OWNER"], "claim owner")
    require_match(args.parent, parent, "parent")
    ledger = snapshot(args.phase, args.worker, parent)
    candidate = api.candidate(parent, ledger, f"{args.phase} contender {args.worker}")
    report = {
        "phase": args.phase, "worker": args.worker, "ref": ref,
        "run_id": os.environ["GITHUB_RUN_ID"],
        "parent": parent, "sha": candidate, "ledger": ledger,
    }
    write_json(args.out, report)
    print(f"CAS_PROPOSAL_CREATED phase={args.phase} worker={args.worker} sha={candidate}")


def race(args):
    api = Client()
    ref = experiment_ref()
    require_match(args.ref, ref, "ref")
    proposal = read_json(args.proposal)
    for k, v in (
        ("phase", args.phase), ("worker", args.worker), ("ref", ref),
        ("run_id", os.environ["GITHUB_RUN_ID"]), ("parent", args.parent),
    ):
        require_match(proposal[k], v, k)
    # Both contenders prepared sibling commits from the same immutable parent.
    result = api.try_advance(ref, proposal["sha"])
    readback_tip = api.tip(ref)
    report = {
        "phase": args.phase, "worker": args.worker,
        "ref": ref, "candidate": proposal["sha"], "parent": args.parent,
        "http_status": result.status,
        "response_message": str(result.data.get("message", ""))[:180]
            if isinstance(result.data, dict) else "",
        "observed_ref_after": readback_tip,
    }
    write_json(args.out, report)
    print(f"CAS_RACE_RESULT phase={args.phase} worker={args.worker} "
          f"http={result.status} head={readback_tip}")
    if result.status not in (200, 409, 422):
        raise RuntimeError("Unexpected PATCH response; preserve evidence and fail closed")


def verify(args):
    api = Client()
    ref = experiment_ref()
    require_match(args.ref, ref, "ref")
    phase = args.phase
    proposals = {w: read_json(f"{args.proposals}/{phase}-proposal-{w}.json") for w in WORKERS}
    responses = {w: read_json(f"{args.results}/{phase}-result-{w}.json") for w in WORKERS}
    for w in WORKERS:
        prop, response = proposals[w], responses[w]
        for key, val in (("worker", w), ("phase", phase), ("ref", ref), ("parent", args.parent)):
            require_match(prop[key], val, "proposal." + key)
        require_match(prop["run_id"], os.environ["GITHUB_RUN_ID"], "run id")
        require_match(response["candidate"], prop["sha"], "response candidate")
        require_match(response["parent"], args.parent, "response parent")
    winners = [w for w in WORKERS if responses[w]["http_status"] == 200]
    losers = [w for w in WORKERS if responses[w]["http_status"] in (409, 422)]
    if len(winners) != 1 or len(losers) != 1:
        raise RuntimeError(f"Expected exactly one successful write; got {responses}")
    winner = winners[0]
    tip = api.tip(ref)
    require_match(tip, proposals[winner]["sha"], "remote HEAD")
    persisted = api.ledger(ref)
    if persisted != proposals[winner]["ledger"]:
        raise RuntimeError("GitHub persisted content differs from selected candidate")
    ids = assert_ledger(persisted)
    if phase == "claim" and ids:
        raise RuntimeError("Claim phase already spent money")
    if phase == "budget" and len(ids) != 1:
        raise RuntimeError("Budget initial sibling race must record one reservation")
    report = {
        "phase": phase, "winner": winner, "loser": losers[0],
        "winner_sha": tip, "http_status_by_worker":
            {w: responses[w]["http_status"] for w in WORKERS},
        "persisted_ledger": persisted, "proof": "observed remote SHA and exact ledger",
    }
    write_json(args.out, report)
    mark_output("winner_sha", tip)
    mark_output("winner", winner)
    print(f"CAS_VERIFIED phase={phase} winner={winner} loser={losers[0]} "
          f"winner_sha={tip}")


def reconcile(args):
    api = Client()
    ref = experiment_ref()
    require_match(args.ref, ref, "ref")
    initial = api.ledger(ref)
    ids = assert_ledger(initial)
    if initial["generation"] != 2 or len(ids) != 1:
        raise RuntimeError("Budget CAS results not verified, fail-closed")
    claim_owner = initial["claim_owner"]
    accepted = []
    denied = []
    mutations = []
    simulated_lost_ack = False
    for operation in OP_IDS:
        for retry in range(4):
            prior_sha = api.tip(ref)
            state = api.ledger(ref)
            require_match(state["claim_owner"], claim_owner, "claim owner invariant")
            current = assert_ledger(state)
            if operation in current:
                accepted.append(operation)
                break
            if len(current) >= BUDGET_LIMIT:
                denied.append(operation)
                break
            next_state = dict(state,
                generation=state["generation"] + 1,
                reservations=state["reservations"] + [
                    {"request_id": operation, "max_cost_minor": 1}],
                last="reconcile-" + operation,
            )
            sha = api.candidate(prior_sha, next_state, "budget reconcile " + operation)
            result = api.try_advance(ref, sha)
            # Emulate lost response after a write, then reconcile by reading
            # the remote ref and checking the exact operation identity.
            if result.status == 200 and not simulated_lost_ack:
                simulated_lost_ack = True
                received_status = "ACK_LOST_SIMULATED"
            else:
                received_status = result.status
            readback = api.ledger(ref)
            if not readback:
                raise RuntimeError("Uncertain write cannot be read back")
            remote_ids = assert_ledger(readback)
            mutations.append({"operation": operation, "result": received_status,
                              "persisted_after_readback": operation in remote_ids})
            if operation in remote_ids:
                accepted.append(operation)
                break
            if result.status not in (409, 422):
                raise RuntimeError("Ambiguous update not attributable to a remote state")
        else:
            raise RuntimeError("Reconciliation retry cap exceeded")
    final_sha = api.tip(ref)
    final = api.ledger(ref)
    final_ids = assert_ledger(final)
    if len(final_ids) != BUDGET_LIMIT or len(set(final_ids)) != BUDGET_LIMIT:
        raise RuntimeError("Final budget ledger not at expected cap")
    if len(denied) != 2 or len(set(accepted)) != BUDGET_LIMIT:
        raise RuntimeError("Incorrect accept/deny count")
    if final["claim_owner"] != claim_owner or not simulated_lost_ack:
        raise RuntimeError("Claim mutated or fault case not exercised")
    report = {
        "phase": "budget-final",
        "ref": ref, "final_sha": final_sha,
        "claim_owner": claim_owner,
        "accepted": accepted, "denied": denied,
        "final_ledger": final, "mutations": mutations,
        "simulated_lost_ack_recovered": simulated_lost_ack,
        "provider_calls": 0,
        "scope": "single disposable ref in separate repo; not multi-ref transaction",
    }
    write_json(args.out, report)
    print(f"CAS_BUDGET_VERIFIED accepted={len(accepted)} denied={len(denied)} "
          f"budget={len(final_ids)}/{BUDGET_LIMIT} remote_sha={final_sha}")


def cleanup(args):
    api = Client()
    ref = experiment_ref()
    # The caller is not allowed to supply a ref. Cleanup is run-derived only.
    tip = api.tip(ref)
    if tip is None:
        print("CAS_CLEANUP_OK: ref absent (nothing to clean)")
        return
    response = api.delete_ref(ref)
    if response.status not in (204, 200):
        raise RuntimeError(f"Unable to delete disposable ref, HTTP {response.status}")
    if api.tip(ref) is not None:
        raise RuntimeError("Ref cleanup not verified")
    print(f"CAS_CLEANUP_OK deleted={ref} former_tip={tip}")


def main():
    require_environment()
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="action", required=True)
    sub.add_parser("bootstrap")
    p = sub.add_parser("prepare")
    p.add_argument("--phase", choices=("claim", "budget"), required=True)
    p.add_argument("--worker", choices=WORKERS, required=True)
    p.add_argument("--ref", required=True)
    p.add_argument("--parent", required=True)
    p.add_argument("--out", required=True)
    r = sub.add_parser("race")
    r.add_argument("--phase", choices=("claim", "budget"), required=True)
    r.add_argument("--worker", choices=WORKERS, required=True)
    r.add_argument("--ref", required=True)
    r.add_argument("--parent", required=True)
    r.add_argument("--proposal", required=True)
    r.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--phase", choices=("claim", "budget"), required=True)
    v.add_argument("--ref", required=True)
    v.add_argument("--parent", required=True)
    v.add_argument("--proposals", required=True)
    v.add_argument("--results", required=True)
    v.add_argument("--out", required=True)
    a = sub.add_parser("reconcile")
    a.add_argument("--ref", required=True)
    a.add_argument("--out", required=True)
    sub.add_parser("cleanup")
    args = ap.parse_args()
    if args.action == "bootstrap":
        bootstrap(args)
    elif args.action == "prepare":
        prepare(args)
    elif args.action == "race":
        race(args)
    elif args.action == "verify":
        verify(args)
    elif args.action == "reconcile":
        reconcile(args)
    else:
        cleanup(args)


if __name__ == "__main__":
    main()
