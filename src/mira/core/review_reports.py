"""Immutable per-SHA reports for review history, finding evolution, and impact."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field

from mira.core.diff_parser import parse_diff
from mira.models import FileDiff, ReviewComment, ThreadDecision, WalkthroughResult

_REPORT_KIND_RE = re.compile(r"[^a-z0-9_-]+")
_SCOPE_RE = re.compile(r"[^a-z0-9._-]+")
_NEXT_PAGE_RE = re.compile(r"(?:^|/)app/(.+)/page\.(?:[jt]sx?)$")
_NEXT_ROOT_PAGE_RE = re.compile(r"(?:^|/)app/page\.(?:[jt]sx?)$")
_PAGES_PAGE_RE = re.compile(r"(?:^|/)pages/(.+)\.(?:[jt]sx?)$")


def make_report_marker(kind: str, scope: str) -> str:
    """Return an exact marker that can only match one report kind and SHA."""
    safe_kind = _REPORT_KIND_RE.sub("-", kind.strip().lower()).strip("-") or "report"
    safe_scope = _SCOPE_RE.sub("-", scope.strip().lower()).strip("-") or "unknown"
    return f"<!-- mira-report:{safe_kind}:{safe_scope} -->"


def _single_line(value: str, limit: int = 180) -> str:
    text = " ".join((value or "").split())
    text = text.replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _thread_title(body: str) -> str:
    for line in (body or "").splitlines():
        value = line.strip()
        if not value or value.startswith(("⚠️", "🐛", "💡", "🔒", "⚡", "🛑")):
            continue
        if value.startswith("**") and value.endswith("**"):
            value = value.strip("* ")
        return _single_line(value)
    return "Historical finding"


_STATUS_LABELS = {
    "new": "New",
    "regressed": "Regressed",
    "fixed_by_code": "Fixed by code",
    "still_present": "Still present",
    "rejected_false_positive": "Rejected false positive",
    "outdated_or_moved": "Outdated or moved",
    "resolved_before_audit": "Resolved before this audit",
    "unverifiable": "Unverifiable",
}


def _finding_key(path: str, title: str) -> tuple[str, str]:
    normalized_title = " ".join(re.findall(r"[a-z0-9]+", title.lower()))
    return path.replace("\\", "/").lower(), normalized_title


def build_evolution_report(
    *,
    head_sha: str,
    previous_sha: str,
    decisions: list[ThreadDecision],
    new_findings: list[ReviewComment],
    unverifiable_threads: list[ThreadDecision],
    historical_resolved_threads: list[ThreadDecision] | None = None,
) -> str:
    """Render a human-readable comparison without mutating older reports."""
    marker = make_report_marker("evolution", head_sha)
    grouped: dict[str, list[tuple[str, int, str, str]]] = {status: [] for status in _STATUS_LABELS}
    historical_by_key = {
        _finding_key(decision.path, _thread_title(decision.body)): decision
        for decision in (historical_resolved_threads or [])
    }
    for finding in new_findings:
        key = _finding_key(finding.path, finding.title)
        previous = historical_by_key.pop(key, None)
        status = "regressed" if previous else "new"
        evidence = (
            f"Matches previously resolved thread {previous.thread_id}."
            if previous
            else "current audit"
        )
        grouped[status].append((finding.path, finding.line, finding.title, evidence))
    for decision in [*decisions, *unverifiable_threads]:
        status = decision.status or ("fixed_by_code" if decision.fixed else "still_present")
        if status not in grouped:
            status = "unverifiable"
        grouped[status].append(
            (decision.path, decision.line, _thread_title(decision.body), decision.evidence)
        )
    for decision in historical_by_key.values():
        grouped["resolved_before_audit"].append(
            (decision.path, decision.line, _thread_title(decision.body), decision.evidence)
        )

    parts = [marker, "## Mira Finding Evolution", ""]
    if previous_sha:
        parts.append(f"Scope: `{previous_sha[:12]}` → `{head_sha[:12]}`")
    else:
        parts.append(f"Scope: `{head_sha[:12]}` · **First recorded audit**")
    parts.extend(
        [
            "",
            "Each status is tied to this SHA. Earlier Mira comments remain unchanged.",
            "",
            "| Status | Count |",
            "|---|---:|",
        ]
    )
    for status, label in _STATUS_LABELS.items():
        parts.append(f"| {label} | {len(grouped[status])} |")

    for status, label in _STATUS_LABELS.items():
        entries = grouped[status]
        if not entries:
            continue
        parts.extend(["", f"### {label}", ""])
        for path, line, title, evidence in entries:
            location = f"{path}:{line}" if line > 0 else path
            suffix = f" — {_single_line(evidence)}" if evidence else ""
            parts.append(f"- `{_code_span(location)}` — {_single_line(title)}{suffix}")

    parts.extend(
        [
            "",
            "> `Unverifiable`, `outdated or moved`, and `resolved before this audit` are not "
            "presented as code fixes. They require current-code evidence before Mira can call "
            "them fixed.",
        ]
    )
    return "\n".join(parts)


class ImpactMagnitude(enum.Enum):
    MINIMAL = "minimal"
    MODERATE = "moderate"
    MAJOR = "major"


@dataclass(frozen=True)
class LogicChange:
    path: str
    before: str = ""
    after: str = ""
    description: str = ""


@dataclass
class ImpactReport:
    head_sha: str
    magnitude: ImpactMagnitude
    changed_paths: list[str] = field(default_factory=list)
    visual_routes: list[str] = field(default_factory=list)
    route_evidence: dict[str, str] = field(default_factory=dict)
    route_urls: dict[str, str] = field(default_factory=dict)
    endpoints: list[str] = field(default_factory=list)
    logic_changes: list[LogicChange] = field(default_factory=list)

    def to_markdown(self) -> str:
        parts = [
            make_report_marker("impact", self.head_sha),
            "## Mira Change Impact",
            "",
            f"Scope: `{self.head_sha[:12]}` · magnitude: **{self.magnitude.value}**",
            "",
            "> Static analysis only · **runtime-unverified**. This report does not claim that "
            "a page rendered successfully and does not include screenshots.",
            "",
            "### Visually affected views",
            "",
        ]
        if self.visual_routes:
            parts.extend(["| Route | Evidence |", "|---|---|"])
            for route in self.visual_routes:
                route_label = (
                    f"[{_link_text(route)}](<{self.route_urls[route]}>)"
                    if route in self.route_urls
                    else f"`{_code_span(route)}`"
                )
                parts.append(f"| {route_label} | {self.route_evidence[route]} |")
        else:
            parts.append("No web view was identified from the changed files or indexed dependents.")

        parts.extend(["", "### Endpoints and public handlers", ""])
        if self.endpoints:
            parts.extend(f"- `{_code_span(endpoint)}`" for endpoint in self.endpoints)
        else:
            parts.append("No HTTP or tRPC endpoint was identified in the changed hunks.")

        parts.extend(["", "### Before / after and changed logic", ""])
        if not self.logic_changes:
            parts.append("No line-level logic summary was available.")
        for change in self.logic_changes[:12]:
            parts.append(f"- `{_code_span(change.path)}`")
            if change.description:
                parts.append(f"  - Change: {_single_line(change.description, 260)}")
            if change.before:
                parts.append(f"  - Before: `{_inline_code(change.before)}`")
            if change.after:
                parts.append(f"  - After: `{_inline_code(change.after)}`")
        return "\n".join(parts)


def _inline_code(value: str, limit: int = 180) -> str:
    return _code_span(value, limit)


def _code_span(value: str, limit: int = 180) -> str:
    text = " ".join((value or "").split()).replace("`", "'").replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _link_text(value: str) -> str:
    return _single_line(value).replace("[", "\\[").replace("]", "\\]")


def _route_from_path(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    if _NEXT_ROOT_PAGE_RE.search(normalized):
        return "/"
    match = _NEXT_PAGE_RE.search(normalized)
    if match:
        segments = []
        for segment in match.group(1).split("/"):
            if (segment.startswith("(") and segment.endswith(")")) or segment.startswith("@"):
                continue
            segment = re.sub(r"^\((?:\.{1,3})\)", "", segment)
            if segment:
                segments.append(segment)
        return "/" + "/".join(segments)
    match = _PAGES_PAGE_RE.search(normalized)
    if not match:
        return None
    page = match.group(1)
    if page.startswith(("api/", "_app", "_document", "_error")):
        return None
    if page.endswith("/index"):
        page = page[: -len("/index")]
    elif page == "index":
        page = ""
    return "/" + page


def _full_route_url(path: str, route: str, web_base_urls: dict[str, str]) -> str | None:
    normalized = path.replace("\\", "/").strip("/")
    roots = sorted(web_base_urls, key=len, reverse=True)
    for root in roots:
        clean_root = root.replace("\\", "/").strip("/")
        if normalized == clean_root or normalized.startswith(clean_root + "/"):
            base = web_base_urls[root].rstrip("/")
            return base + (route if route != "/" else "/")
    return None


def _hunk_text(file_diff: FileDiff) -> str:
    return "\n".join(hunk.content for hunk in file_diff.hunks)


def _changed_lines(file_diff: FileDiff) -> tuple[list[str], list[str]]:
    removed: list[str] = []
    added: list[str] = []
    for hunk in file_diff.hunks:
        for line in hunk.content.splitlines():
            if line.startswith("---") or line.startswith("+++"):
                continue
            if line.startswith("-"):
                value = line[1:].strip()
                if value:
                    removed.append(value)
            elif line.startswith("+"):
                value = line[1:].strip()
                if value:
                    added.append(value)
    return removed, added


def _join_route(base: str, child: str) -> str:
    pieces = [piece.strip("/") for piece in (base, child) if piece.strip("/")]
    return "/" + "/".join(pieces) if pieces else "/"


def _extract_endpoints(path: str, text: str) -> set[str]:
    endpoints: set[str] = set()
    controller_match = re.search(r"@Controller\(\s*['\"]([^'\"]*)['\"]\s*\)", text)
    controller = controller_match.group(1) if controller_match else ""
    for match in re.finditer(
        r"@(Get|Post|Put|Patch|Delete|Options|Head)\(\s*(?:['\"]([^'\"]*)['\"])?\s*\)",
        text,
    ):
        endpoints.add(f"{match.group(1).upper()} {_join_route(controller, match.group(2) or '')}")
    for match in re.finditer(
        r"@(?:router|app)\.(get|post|put|patch|delete|options|head)\(\s*['\"]([^'\"]+)['\"]",
        text,
        re.IGNORECASE,
    ):
        endpoints.add(f"{match.group(1).upper()} {match.group(2)}")
    for match in re.finditer(
        r"\b(?:app|router)\.(get|post|put|patch|delete|options|head)\(\s*['\"]([^'\"]+)['\"]",
        text,
        re.IGNORECASE,
    ):
        endpoints.add(f"{match.group(1).upper()} {match.group(2)}")
    for match in re.finditer(
        r"\b([A-Za-z_$][\w$]*)\s*:\s*(?:public|protected|private|admin|authed)Procedure\b",
        text,
    ):
        endpoints.add(f"tRPC {path}#{match.group(1)}")
    return endpoints


def _classify_magnitude(
    changed_paths: list[str], routes: set[str], endpoints: set[str], lines: int
) -> ImpactMagnitude:
    lower_paths = "\n".join(changed_paths).lower()
    critical_terms = (
        "migration",
        "/schema",
        "auth",
        "wallet",
        "payment",
        "kyc",
        "permission",
        "security",
    )
    shared_terms = ("packages/", "shared/", "design-system", "contracts/")
    if (
        any(term in lower_paths for term in critical_terms)
        or len(routes) >= 3
        or len(changed_paths) > 20
        or lines > 800
    ):
        return ImpactMagnitude.MAJOR
    if (
        routes
        or endpoints
        or any(term in lower_paths for term in shared_terms)
        or len(changed_paths) > 5
        or lines > 200
    ):
        return ImpactMagnitude.MODERATE
    return ImpactMagnitude.MINIMAL


def analyze_pr_impact(
    diff_text: str,
    *,
    head_sha: str,
    walkthrough: WalkthroughResult | None = None,
    blast_radius_paths: list[str] | None = None,
    web_base_urls: dict[str, str] | None = None,
) -> ImpactReport:
    """Build a deterministic impact report from the PR diff and indexed dependents."""
    patch = parse_diff(diff_text)
    descriptions = {
        entry.path: entry.description for entry in (walkthrough.file_changes if walkthrough else [])
    }
    changed_paths = [file.path for file in patch.files]
    route_evidence: dict[str, str] = {}
    route_urls: dict[str, str] = {}
    endpoints: set[str] = set()
    logic_changes: list[LogicChange] = []
    total_lines = 0

    for file in patch.files:
        total_lines += file.total_changes
        route = _route_from_path(file.path)
        if route:
            route_evidence[route] = "direct page change"
            full_url = _full_route_url(file.path, route, web_base_urls or {})
            if full_url:
                route_urls[route] = full_url
        text = _hunk_text(file)
        endpoints.update(_extract_endpoints(file.path, text))
        removed, added = _changed_lines(file)
        logic_changes.append(
            LogicChange(
                path=file.path,
                before=removed[0] if removed else "",
                after=added[0] if added else "",
                description=descriptions.get(file.path, ""),
            )
        )

    for path in blast_radius_paths or []:
        route = _route_from_path(path)
        if route and route not in route_evidence:
            route_evidence[route] = "inferred from indexed dependency"
            full_url = _full_route_url(path, route, web_base_urls or {})
            if full_url:
                route_urls[route] = full_url

    routes = set(route_evidence)
    magnitude = _classify_magnitude(changed_paths, routes, endpoints, total_lines)
    return ImpactReport(
        head_sha=head_sha,
        magnitude=magnitude,
        changed_paths=changed_paths,
        visual_routes=sorted(routes),
        route_evidence=route_evidence,
        route_urls=route_urls,
        endpoints=sorted(endpoints),
        logic_changes=logic_changes,
    )
