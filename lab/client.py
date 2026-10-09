"""Small GitHub REST client for the isolated CAS laboratory (no third-party deps).

Never print the GitHub token. This client does not execute arbitrary repository
code or contact any service outside api.github.com.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


REPO = "RamonRDR/SaaS-CAS-Lab"
PREFIX = "refs/heads/experiment/cas-probe-"
LEDGER_PATH = "lab/ledger.json"


def require_environment() -> tuple[str, str]:
    if os.environ.get("GITHUB_REPOSITORY") != REPO:
        raise RuntimeError("Repository identity mismatch: fail closed")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Main-only experiment, never untrusted PR/fork")
    run = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    if not run.isdigit() or not attempt.isdigit():
        raise RuntimeError("Missing Actions run identifiers")
    return run, attempt


def experiment_ref() -> str:
    run, attempt = require_environment()
    return PREFIX + run + "-" + attempt


def checked_ref(ref: str) -> str:
    if ref != experiment_ref():
        raise RuntimeError("Ref outside exactly-scoped disposable test run")
    return urllib.parse.quote(ref.removeprefix("refs/"), safe="/")


@dataclass
class Response:
    status: int
    data: dict | list | str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Client:
    def __init__(self):
        require_environment()
        self.token = os.environ.get("GH_TOKEN", "")
        if not self.token:
            raise RuntimeError("GH_TOKEN not available: cannot test remote CAS")
        self.base = f"https://api.github.com/repos/{REPO}"

    def request(self, method: str, path: str, payload: dict | None = None) -> Response:
        if not path.startswith("/"):
            raise ValueError("Invalid REST path")
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + path, data=data, method=method, headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "SaaS-CAS-Lab-one-use",
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=22) as raw:
                content = raw.read().decode("utf-8", errors="replace")
                return Response(raw.status, json.loads(content) if content else {})
        except urllib.error.HTTPError as exc:
            content = exc.read(1800).decode("utf-8", errors="replace")
            try:
                body = json.loads(content)
            except json.JSONDecodeError:
                body = content
            return Response(exc.code, body)

    def must(self, method: str, path: str, payload: dict | None = None):
        result = self.request(method, path, payload)
        if not result.ok:
            message = result.data.get("message", "") if isinstance(result.data, dict) else result.data
            raise RuntimeError(f"GitHub API {method} {path} status={result.status}: {str(message)[:220]}")
        return result.data

    def tip(self, ref: str) -> str | None:
        path = "/git/ref/" + checked_ref(ref)
        response = self.request("GET", path)
        if response.status == 404:
            return None
        if not response.ok:
            raise RuntimeError("Failed to read disposable ref, HTTP " + str(response.status))
        return response.data["object"]["sha"]

    def ledger(self, ref: str) -> dict | None:
        sha = self.tip(ref)
        if sha is None:
            return None
        # Ref is run-derived, path is constant, no data from PRs.
        name = urllib.parse.quote(ref.removeprefix("refs/"), safe="")
        response = self.request("GET", f"/contents/{LEDGER_PATH}?ref={name}")
        if response.status == 404:
            return None
        if not response.ok:
            raise RuntimeError("Unable to read persisted ledger, HTTP " + str(response.status))
        raw = base64.b64decode(response.data["content"].replace("\n", ""))
        return json.loads(raw.decode("utf-8"))

    def candidate(self, parent_sha: str, ledger: dict, message: str) -> str:
        if len(parent_sha) != 40 or any(c not in "0123456789abcdef" for c in parent_sha):
            raise RuntimeError("Not an exact commit SHA")
        parent = self.must("GET", "/git/commits/" + parent_sha)
        blob = self.must("POST", "/git/blobs", {
            "content": json.dumps(ledger, sort_keys=True, separators=(",", ":")) + "\n",
            "encoding": "utf-8",
        })["sha"]
        tree = self.must("POST", "/git/trees", {
            "base_tree": parent["tree"]["sha"],
            "tree": [{"path": LEDGER_PATH, "mode": "100644", "type": "blob", "sha": blob}],
        })["sha"]
        return self.must("POST", "/git/commits", {
            "tree": tree, "parents": [parent_sha], "message": "test: " + message,
        })["sha"]

    def try_advance(self, ref: str, candidate_sha: str) -> Response:
        if len(candidate_sha) != 40:
            raise RuntimeError("Invalid candidate SHA")
        return self.request(
            "PATCH", "/git/refs/" + checked_ref(ref),
            {"sha": candidate_sha, "force": False},
        )

    def create_ref(self, ref: str, sha: str):
        checked_ref(ref)
        return self.must("POST", "/git/refs", {"ref": ref, "sha": sha})

    def delete_ref(self, ref: str) -> Response:
        return self.request("DELETE", "/git/refs/" + checked_ref(ref))
