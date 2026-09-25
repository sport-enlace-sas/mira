import asyncio
import base64
from pathlib import Path

from mira.config import _strip_deployment_only_llm_settings
from mira.models import PRInfo
from mira.ocr_delegation import OpenCodeReviewDelegation


class _Provider:
    async def _resolve_token(self):
        return "test-token"


class _OCR(OpenCodeReviewDelegation):
    def __init__(self, preview, rules):
        super().__init__("ocr", "/corporate/rules", 5, 32)
        self.preview = preview
        self.rules = rules
        self.root = None

    async def _checkout(self, root, pr, token):
        self.root = root
        assert token == "test-token"

    async def _version(self, root):
        return "ocr test"

    async def _ocr(self, root, *args):
        return self.rules if args[1] == "rule" else self.preview


def _pr():
    return PRInfo(
        title="t",
        description="",
        base_branch="main",
        head_branch="feature",
        url="https://github.com/acme/repo/pull/1",
        number=1,
        owner="acme",
        repo="repo",
        base_sha="a" * 40,
        head_sha="b" * 40,
    )


def test_delegation_returns_safe_json_plan_and_cleans_checkout():
    ocr = _OCR(
        {
            "schema_version": "1",
            "reviewable_files": [{"path": "src/a.py"}],
            "excluded_files": [{"path": "lock.json"}],
        },
        {
            "schema_version": "1",
            "groups": [{"files": ["src/a.py"], "rule": "Check authorization."}],
        },
    )
    plan = asyncio.run(ocr.prepare(_Provider(), _pr()))
    assert plan.status == "used"
    assert plan.reviewed_paths == ["src/a.py"]
    assert plan.excluded_paths == ["lock.json"]
    assert "Check authorization" in plan.rules_context
    assert ocr.root is not None and not Path(ocr.root).exists()


def test_invalid_ocr_schema_degrades_without_leaking_output():
    ocr = _OCR({"schema_version": "unexpected", "reviewable_files": []}, {})
    plan = asyncio.run(ocr.prepare(_Provider(), _pr()))
    assert plan.status == "degraded"
    assert plan.error == "invalid_preview"


def test_untrusted_repo_config_cannot_enable_or_repoint_ocr():
    cleaned = _strip_deployment_only_llm_settings(
        {
            "review": {
                "ocr_delegation": True,
                "ocr_command": "/tmp/evil",
                "ocr_rule_path": "/tmp/rules",
                "ocr_timeout_seconds": 600,
                "walkthrough": False,
            }
        }
    )
    assert cleaned["review"] == {"walkthrough": False}


def test_github_checkout_uses_basic_auth_extraheader(tmp_path):
    ocr = OpenCodeReviewDelegation("ocr", "/corporate/rules", 5, 32)

    env = ocr._env("installation-token", tmp_path)

    expected = base64.b64encode(b"x-access-token:installation-token").decode("ascii")
    assert env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    assert env["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {expected}"


def test_ocr_output_contract_drops_unsafe_paths_and_unexpected_rule_fields():
    ocr = _OCR(
        {
            "schema_version": "1",
            "reviewable_files": [
                {"path": "src/a.py"},
                {"path": "../outside.py"},
                {"path": "/etc/passwd"},
            ],
            "excluded_files": [],
        },
        {
            "schema_version": "1",
            "groups": [
                {
                    "files": ["src/a.py", "../outside.py"],
                    "rule": "Corporate rule",
                    "untrusted_payload": "do not keep",
                }
            ],
        },
    )
    plan = asyncio.run(ocr.prepare(_Provider(), _pr()))
    assert plan.status == "used"
    assert plan.reviewed_paths == ["src/a.py"]
    assert plan.rule_groups == [
        {"files": ["src/a.py"], "rule": "Corporate rule", "source": "", "pattern": ""}
    ]
