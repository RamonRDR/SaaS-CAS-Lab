"""Pure tests that never call GitHub or use credentials."""
from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from lab.client import PREFIX, REPO, checked_ref, experiment_ref, require_environment
from lab.probe import BUDGET_LIMIT, OP_IDS, assert_ledger, snapshot


ENV = {
    "GITHUB_REPOSITORY": REPO, "GITHUB_REF": "refs/heads/main",
    "GITHUB_RUN_ID": "123456", "GITHUB_RUN_ATTEMPT": "1",
    "CLAIM_OWNER": "alpha",
}


class ScopeTests(TestCase):
    def test_exact_disposable_reference(self):
        with patch.dict(os.environ, ENV):
            self.assertEqual(experiment_ref(), PREFIX + "123456-1")
            self.assertEqual(checked_ref(experiment_ref()), "heads/experiment/cas-probe-123456-1")

    def test_reject_main_ref_mutation(self):
        with patch.dict(os.environ, ENV):
            with self.assertRaises(RuntimeError):
                checked_ref("refs/heads/main")

    def test_reject_other_run_reference(self):
        with patch.dict(os.environ, ENV):
            with self.assertRaises(RuntimeError):
                checked_ref(PREFIX + "123457-1")

    def test_reject_untrusted_repository(self):
        with patch.dict(os.environ, {**ENV, "GITHUB_REPOSITORY": "RamonRDR/SaaS-Project"}):
            with self.assertRaises(RuntimeError):
                require_environment()

    def test_reject_pr_context(self):
        with patch.dict(os.environ, {**ENV, "GITHUB_REF": "refs/pull/10/merge"}):
            with self.assertRaises(RuntimeError):
                require_environment()

    def test_reject_empty_run(self):
        with patch.dict(os.environ, {**ENV, "GITHUB_RUN_ID": "other"}):
            with self.assertRaises(RuntimeError):
                require_environment()


class LedgerTests(TestCase):
    def test_claim_snapshot_no_reservation(self):
        with patch.dict(os.environ, ENV):
            s = snapshot("claim", "alpha", "x" * 40)
            self.assertEqual(s["claim_owner"], "alpha")
            self.assertEqual(assert_ledger(s), [])
            self.assertEqual(s["generation"], 1)

    def test_budget_snapshot_adds_only_one_request(self):
        with patch.dict(os.environ, ENV):
            s = snapshot("budget", "beta", "x" * 40)
            self.assertEqual(assert_ledger(s), ["op-beta"])
            self.assertEqual(s["claim_owner"], "alpha")

    def test_reject_duplicate_reservation(self):
        with patch.dict(os.environ, ENV):
            s = snapshot("budget", "alpha", "x" * 40)
            s["reservations"].append(dict(s["reservations"][0]))
            with self.assertRaises(RuntimeError):
                assert_ledger(s)

    def test_reject_over_budget(self):
        with patch.dict(os.environ, ENV):
            s = snapshot("budget", "alpha", "x" * 40)
            s["reservations"] = [
                {"request_id": OP_IDS[i], "max_cost_minor": 1} for i in range(5)
            ]
            with self.assertRaises(RuntimeError):
                assert_ledger(s)

    def test_reject_unexpected_identity(self):
        with patch.dict(os.environ, ENV):
            s = snapshot("budget", "alpha", "x" * 40)
            s["reservations"][0]["request_id"] = "untrusted-other"
            with self.assertRaises(RuntimeError):
                assert_ledger(s)

    def test_budget_limit_is_fixed(self):
        self.assertEqual(BUDGET_LIMIT, 4)
