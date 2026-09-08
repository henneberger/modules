"""Bounded metadata context for an agent adapting or composing accepted modules."""

from __future__ import annotations

from collections import deque

from .registry import canonical_bytes


def contribution_context(
    task,
    repository,
    *,
    max_candidates=64,
    max_interfaces=32,
    max_depth=4,
    max_bytes=1_000_000,
):
    """Read declarations only. Truncation is explicit, never an absence proof."""
    if (
        any(
            type(value) is not int or value < 1
            for value in (max_candidates, max_interfaces, max_depth, max_bytes)
        )
        or max_candidates > 1000
        or max_interfaces > 1000
        or max_depth > 16
        or max_bytes > 10_000_000
    ):
        raise ValueError("invalid worker context bounds")
    contract = task["document"]["task"]
    result = {
        "format": "module-families-worker-context-1",
        "task_sha256": task["sha256"],
        "scope": "bounded accepted-repository metadata; incomplete discovery",
        "interfaces": [],
        "candidates": [],
        "truncations": [],
        "bounds": {
            "candidates": max_candidates,
            "interfaces": max_interfaces,
            "depth": max_depth,
            "bytes": max_bytes,
        },
    }
    cards, interfaces = set(), set()
    pending = deque([(contract["requires"], 0)])

    def truncate(reason):
        if reason not in result["truncations"]:
            result["truncations"].append(reason)

    def append(field, value):
        result[field].append(value)
        # Reserve room for bounded truncation diagnostics.
        if len(canonical_bytes(result)) + 256 > max_bytes:
            result[field].pop()
            truncate("max_bytes")
            return False
        return True

    def candidate(card, depth):
        key = (card["family"], card["id"], card["version"])
        if key in cards:
            return
        if len(cards) >= max_candidates:
            truncate("max_candidates")
            return
        if not append("candidates", card):
            return
        cards.add(key)
        pending.append((card["provides"], depth))
        for reference in card.get("requires", {}).values():
            pending.append((reference, depth + 1))

    query = " ".join([contract["summary"], *contract.get("capabilities", [])])
    policy = task["document"].get("policy", {}).get("allowed_effects")
    search_limit = min(16, max_candidates)
    for text in (query, ""):
        matches = repository.search(
            text, limit=search_limit + 1, allowed_effects=policy
        )
        if len(matches) > search_limit:
            truncate("search_page")
        for card in matches[:search_limit]:
            candidate(card, 0)
    while pending:
        reference, depth = pending.popleft()
        if depth > max_depth:
            truncate("max_depth")
            continue
        key = (reference["id"], reference["version"])
        if key in interfaces:
            continue
        if len(interfaces) >= max_interfaces:
            truncate("max_interfaces")
            break
        declaration = repository.interface(*key)
        if not append("interfaces", declaration):
            break
        interfaces.add(key)
        remaining = max_candidates - len(cards)
        if not remaining:
            truncate("max_candidates")
            continue
        page = repository.candidates(*key, limit=min(1000, remaining + 1))
        if len(page) > remaining:
            truncate("max_candidates")
        for card in page[:remaining]:
            candidate(card, depth)
    if len(canonical_bytes(result)) > max_bytes:
        raise ValueError("context byte budget cannot hold its envelope")
    return result
