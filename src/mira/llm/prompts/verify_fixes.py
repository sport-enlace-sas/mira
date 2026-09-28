"""Prompt builder for verifying whether review issues have been fixed."""

from __future__ import annotations

from mira.llm.utils import strip_code_fences, strip_think_blocks
from mira.models import FixVerification, UnresolvedThread

# Markers that signal the start of noise sections in formatted review comments.
# Everything from these markers onward is stripped before inclusion in prompts.
_BODY_NOISE_MARKERS = ("**Suggested fix:**", "```suggestion", "<details>")

_MAX_DESCRIPTION_LENGTH = 300


def _extract_issue_description(body: str) -> str:
    """Extract the core issue description from a formatted review comment body.

    Mira's posted comments follow this structure::

        {emoji} **{category_label}**
        {severity_badge}

        **{title}**

        {description}

        **Suggested fix:**
        ```suggestion ...```

        <details>🤖 Prompt for AI Agents ...</details>

    This function strips the badge header, suggestion blocks, and agent prompts,
    returning just the title and explanation text.
    """
    text = body

    # Cut off suggestion blocks and agent prompt sections
    for marker in _BODY_NOISE_MARKERS:
        pos = text.find(marker)
        if pos != -1:
            text = text[:pos]

    # Strip markdown bold markers
    text = text.replace("**", "")

    # Split into paragraphs (double-newline separated)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    # The formatted comment starts with a compact badge paragraph
    # (emoji + category label + optional severity line).  Skip it when
    # there is more content after it.
    if len(paragraphs) > 1 and len(paragraphs[0]) < 80:
        paragraphs = paragraphs[1:]

    result = " ".join(paragraphs).strip()
    if not result:
        # Fallback: use the cleaned full text
        result = " ".join(text.split()).strip()

    if len(result) > _MAX_DESCRIPTION_LENGTH:
        result = result[:_MAX_DESCRIPTION_LENGTH].rsplit(" ", 1)[0] + "…"
    return result


def build_verify_fixes_prompt(
    file_groups: list[tuple[str, str, list[UnresolvedThread]]],
) -> list[dict[str, str]]:
    """Build a prompt asking the LLM which review issues have been fixed.

    Each entry in *file_groups* is a ``(path, file_content, threads)`` tuple
    where *file_content* is the current code (full file or relevant sections)
    and *threads* lists the unresolved review comments in that file.
    """
    sections: list[str] = []
    for path, content, threads in file_groups:
        issue_lines: list[str] = []
        for idx, t in enumerate(threads, 1):
            line_label = f"Line {t.line}" if t.line > 0 else "Location unknown (outdated comment)"
            outdated_tag = " [OUTDATED — code has changed]" if t.is_outdated else ""
            issue_lines.append(
                f'{idx}. (id: "{t.thread_id}") {line_label}{outdated_tag}: '
                f"{_extract_issue_description(t.body)}"
            )
        issues = "\n".join(issue_lines)
        sections.append(
            f"File: {path}\n```\n{content}\n```\n\nIssues to verify in this file:\n{issues}"
        )

    user_content = "\n\n---\n\n".join(sections)

    return [
        {
            "role": "system",
            "content": (
                "You are verifying whether code review issues have been fixed.\n\n"
                "For each issue below, you will see the current file content "
                "(full or relevant sections) with line numbers, and a list of "
                "previously flagged issues.\n\n"
                "For each issue:\n"
                "1. Look at the referenced line number in the current code.\n"
                "2. Check if the EXACT problematic code pattern described in "
                "the issue is still present at or near that line.\n"
                "3. Assign exactly one status:\n"
                "   - fixed_by_code: the problematic behavior was removed or corrected.\n"
                "   - still_present: the problematic behavior is clearly still present.\n"
                "   - rejected_false_positive: the original finding was incorrect; the "
                "code was already safe without a corrective change.\n"
                "   - outdated_or_moved: the referenced code moved or disappeared and the "
                "specific concern no longer maps to a current location.\n"
                "   - unverifiable: the available code is insufficient or ambiguous.\n"
                "4. Include short, code-specific evidence. Never call a false positive "
                "fixed_by_code. Never infer a fix only because GitHub marked a line outdated.\n\n"
                "Respond with ONLY the JSON object below, no other text:\n"
                '{"results": [{"id": "<thread_id>", "status": "<status>", '
                '"evidence": "<current-code evidence>"}, ...]}'
            ),
        },
        {"role": "user", "content": user_content},
    ]


_VALID_STATUSES = {
    "fixed_by_code",
    "still_present",
    "rejected_false_positive",
    "outdated_or_moved",
    "unverifiable",
}
_RESOLVABLE_STATUSES = {"fixed_by_code", "rejected_false_positive"}


def parse_verify_fix_results(raw: str) -> dict[str, FixVerification]:
    """Parse structured results; malformed or unknown statuses stay unverifiable."""
    import json

    try:
        data = json.loads(strip_think_blocks(strip_code_fences(raw)))
    except (json.JSONDecodeError, TypeError):
        return {}

    results = data.get("results")
    if not isinstance(results, list):
        return {}

    parsed: dict[str, FixVerification] = {}
    for entry in results:
        if not isinstance(entry, dict):
            continue
        thread_id = entry.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            continue
        status = entry.get("status")
        # Backwards compatibility with cached/older model responses.
        if status is None and isinstance(entry.get("fixed"), bool):
            status = "fixed_by_code" if entry["fixed"] else "still_present"
        if status not in _VALID_STATUSES:
            status = "unverifiable"
        evidence = entry.get("evidence")
        parsed[thread_id] = FixVerification(
            thread_id=thread_id,
            status=status,
            evidence=evidence if isinstance(evidence, str) else "",
        )
    return parsed


def parse_verify_fixes_response(raw: str) -> list[str]:
    """Return IDs safe to resolve while preserving the legacy public API."""
    return [
        thread_id
        for thread_id, result in parse_verify_fix_results(raw).items()
        if result.status in _RESOLVABLE_STATUSES
    ]
