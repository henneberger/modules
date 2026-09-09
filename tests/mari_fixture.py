"""Seed a Mari family from copied source and its workload guide, without imports."""

from __future__ import annotations

import os
import tomllib
from collections import Counter
from pathlib import Path

from module_families.catalog import Catalog

GUIDANCE = {
    "retrieval.fusion.reciprocal_rank_fusion": {
        "solves": [
            "Combine lexical and dense ranked search results without calibrated score scales."
        ],
        "use_when": ["You have ranked document IDs from several retrieval sources."],
        "avoid_when": [
            "Absolute score differences should influence the final ranking."
        ],
        "tags": ["rank-fusion", "rrf", "hybrid-search", "uncalibrated-scores"],
        "effects": ["caller-callback"],
    },
    "retrieval.fusion.maximal_marginal_relevance": {
        "solves": ["Choose relevant evidence while reducing redundant search results."],
        "use_when": [
            "You can supply relevance values and a pairwise similarity callback."
        ],
        "avoid_when": [
            "You need a globally optimal subset or formal independent corroboration."
        ],
        "tags": ["mmr", "diversity", "context-selection"],
        "effects": ["caller-callback"],
    },
    "algorithms.temporal.recency_decay": {
        "provides": {"id": "ranking.recency", "version": "1"},
        "capabilities": ["freshness", "exponential-decay"],
        "solves": ["Convert age in days into an explicit freshness ranking signal."],
        "use_when": [
            "Compare linear, exponential half-life, and neutral recency policies."
        ],
        "avoid_when": ["Freshness alone must establish factual validity or proof."],
        "tags": [
            "recency",
            "freshness",
            "exponential",
            "half-life",
            "temporal-ranking",
        ],
        "effects": [],
    },
    "algorithms.lexical.BM25VariantIndex": {
        "solves": [
            "Compare Okapi, BM25L, and BM25Plus lexical ranking over supplied tokens."
        ],
        "use_when": [
            "Token frequency and document length are useful relevance signals."
        ],
        "avoid_when": [
            "You require semantic matching without term overlap; L/Plus nonmatches can score above zero."
        ],
        "tags": ["bm25", "okapi", "bm25l", "bm25plus", "lexical-search"],
    },
    "algorithms.subsets.maximize_subset": {
        "solves": [
            "Choose a bounded subset using naive, lazy, stochastic, or lazier greedy search."
        ],
        "use_when": [
            "An explicit objective quantifies coverage or diversity; lazy modes need submodularity."
        ],
        "avoid_when": [
            "You cannot establish the objective assumptions required by the selected optimizer."
        ],
        "tags": ["greedy", "submodular", "subset-selection", "coverage", "diversity"],
        "effects": ["caller-callback"],
    },
    "algorithms.compression.fastcdc_chunks": {
        "solves": [
            "Split byte streams into content-defined chunks for incremental deduplication."
        ],
        "use_when": [
            "Byte boundaries should tolerate insertions and be independent of stream read sizes."
        ],
        "avoid_when": ["Chunks must respect semantic text or document structure."],
        "tags": ["fastcdc", "deduplication", "chunking", "bytes"],
        "effects": ["caller-stream-read"],
    },
    "retrieval.maxsim.exact_maxsim": {
        "solves": [
            "Compute exact late-interaction similarity between supplied multi-vector representations."
        ],
        "use_when": [
            "Rerank a bounded candidate set after approximate candidate retrieval."
        ],
        "avoid_when": [
            "An exhaustive comparison over a large corpus is too expensive."
        ],
        "tags": ["maxsim", "colbert", "multi-vector", "reranking"],
        "effects": [],
    },
}

INTERFACES = [
    {
        "id": "ranking.recency",
        "version": "1",
        "types": [],
        "callables": {
            "decay": {
                "asynchronous": False,
                "parameters": [
                    {"name": "days", "kind": "POSITIONAL_ONLY", "required": True},
                    {"name": "method", "kind": "KEYWORD_ONLY", "required": False},
                    {"name": "half_life", "kind": "KEYWORD_ONLY", "required": False},
                ],
            }
        },
    }
]


def generate_mari(project: str | Path, destination: str | Path) -> dict:
    """Adapt the copied candidate with compact discovery and seven annotations."""
    from module_families.authoring import draft_family
    from module_families.manifest import write_manifest

    project, destination = Path(project).resolve(), Path(destination).resolve()
    if destination.suffix != ".toml":
        raise ValueError("Mari adaptation writes a compact .toml family declaration")
    if destination.exists():
        raise ValueError(f"refusing to overwrite existing manifest: {destination}")
    contribution_root = destination.parent / "members"
    fragments = {}
    for identifier, context in GUIDANCE.items():
        domain = identifier.split(".", 1)[0]
        fragments.setdefault(domain, []).append(
            {
                "id": identifier,
                "kind": "algorithm",
                **context,
                "metadata_basis": ["curated-from-source-and-workload-guide"],
            }
        )
    for domain in [*fragments, "interfaces"]:
        path = contribution_root / f"{domain}.toml"
        if path.exists():
            raise ValueError(f"refusing to overwrite contribution: {path}")
    draft_family(
        project,
        family="mari",
        publisher="mari",
        package="mark_kit",
        destination=destination,
        version="0.1.0",
    )
    document = tomllib.loads(destination.read_text())
    document["family"]["description"] = (
        "Reusable algorithms and support types adapted from the copied Mari project."
    )
    document["context"] = {
        "solves": ["Retrieve, rank, organize, and evaluate evidence and agent memory."],
        "selection_guidance": [
            "Inspect source assumptions and shared types before composing implementations.",
            "Unknown effects require characterization; shared family membership is not substitutability.",
        ],
        "guide": os.path.relpath(
            project / "docs" / "algorithm-choices.md", destination.parent
        ),
    }
    # Optional and dynamic imports need declarations that cannot be inferred
    # from the upstream package's mandatory dependency list.
    document["external_dependencies"] = {
        "numpy": "numpy>=1.26",
        "networkx": "networkx>=3.2",
        "pcst_fast": "pcst-fast>=1.0.10",
        "graspologic_native": "graspologic-native>=1.2",
        "scipy": "scipy>=1.11",
        "rdflib": "rdflib",
        "torch": "torch",
        "torch_geometric": "torch-geometric",
    }
    document["dynamic_dependencies"] = {
        "mark_kit.algorithms.graphs:_dependency": [],
        "mark_kit.algorithms.graphs:_nx_graph": ["networkx>=3.2"],
        "mark_kit.algorithms.graphs:prize_collecting_forest": ["pcst-fast>=1.0.10"],
        "mark_kit.algorithms.graphs:hierarchical_leiden_partition": [
            "graspologic-native>=1.2"
        ],
        "mark_kit.algorithms.linkage:centroid_clusters": ["scipy>=1.11"],
    }
    contribution_root.mkdir(parents=True, exist_ok=True)
    for domain, members in sorted(fragments.items()):
        write_manifest(
            {"members": sorted(members, key=lambda member: member["id"])},
            contribution_root / f"{domain}.toml",
        )
    write_manifest({"interfaces": INTERFACES}, contribution_root / "interfaces.toml")
    write_manifest(document, destination)
    members = Catalog.load(destination).document["members"]
    return {
        "manifest": str(destination),
        "members": len(members),
        "explicit_annotations": len(GUIDANCE),
        "contribution_files": len(fragments) + 1,
        "kinds": dict(sorted(Counter(member["kind"] for member in members).items())),
    }
