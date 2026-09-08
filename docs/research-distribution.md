# Distribution and agent discovery research

Research date: 2026-09-08. This is a focused survey of primary sources relevant to distributing Python module families and independently publishable algorithms. It complements the module-language research; it does not claim to exhaust all contemporary package-management literature. Recommendations below are this project's design synthesis, not claims that the cited systems implement the proposed Python design.

## Findings that determine the architecture

The useful unit is an independently identifiable implementation with an explicit dependency closure, a contract, and evidence describing when to select it. A family supplies shared vocabulary and compatibility relationships. Publication, selection, installation, composition, and execution should be distinct operations with distinct guarantees. Agents make more detailed metadata practical, but neither natural-language descriptions nor greater model capability remove the need for mechanically checked boundaries.

| Concern | Recommended representation | Guarantee and limit |
| --- | --- | --- |
| Problem fit | Family and member context: problems, assumptions, exclusions, examples, complexity, evidence | Supports discovery; descriptions remain publisher claims |
| Interface | Versioned contract ID, named exports, argument/result schemas, optional nominal type identities | Validates declared structure; does not establish algorithm correctness |
| Implementation | Source closure digest and separately recorded wheel digest | Identifies committed bytes; does not prove equivalent behavior |
| Composition | Named requirements bound to concrete member identities | Records the chosen dependency graph; shared Python ambient state still exists |
| Installation | Exact member lock plus ordinary Python dependency lock | Makes the intended selection replayable within specified environments |
| Trust | Expected publisher identity and artifact-bound attestations | Establishes origin under an explicit trust policy; does not certify benign behavior |
| Effects | Declared effects, policy decision, and separately identified enforcement backend | In-process Python cannot enforce a declared absence of effects |

## Content-addressed code: borrow identity separation, avoid overstating it

Unison assigns a definition a hash of its syntax tree; names are separately stored metadata, and references incorporate definition identities. This permits naming and code organization to change without rewriting the underlying definition identity. That is a strong model for distributing much smaller units than a repository or package. [Unison: The big idea](https://www.unison-lang.org/docs/the-big-idea/)

For Python, implement a conservative analogue: hash a canonical manifest containing export bindings, source-file byte hashes, contract references, and dependency identities. Store artifact blobs under immutable digests; separately index human names and searchable family descriptions. Version the canonicalization format so changing the hash procedure cannot silently change its meaning. An independently signed, revised assessment can refer to unchanged code, allowing new benchmarks and warnings without manufacturing a new implementation release.

Unison's language-reference treatment of hashes makes hashes the actual references to terms and types. It does not establish that every pair of behaviorally equivalent programs receives the same hash. Our Python layer should be stricter about its terminology: a source digest identifies specified source bytes and declared closure; a wheel digest identifies an archive; a contract digest identifies a contract document. None is a proof of semantic equivalence, absence of vulnerabilities, or deterministic execution. [Unison: Hashes](https://www.unison-lang.org/docs/language-reference/hashes/)

Python adds practical complications: imports execute code, normal package imports execute parent `__init__.py`, and `sys.modules` caches by module name. Import hooks alone cannot turn arbitrary Python into Unison's immutable definition graph. Therefore discovery must parse data rather than import candidate modules, and incompatible versions sharing an import path require separate environments or deliberate namespace rewriting. [Python import system](https://docs.python.org/3/reference/import.html)

## Reproducibility: close over inputs, and distinguish three claims

Nix distinguishes input-addressed and content-addressed store objects. Its closure is the set of directly and transitively referenced store paths; deployment must include the required closure. The manual explicitly describes purity as an assumption that cannot be guaranteed generally. These distinctions are more useful than simply saying that everything should be hashed. [Nix 2.35 glossary](https://nix.dev/manual/nix/2.35/glossary.html)

Nix derivations identify inputs, outputs, execution platform, builder, arguments, and environment. The inputs are explicitly enumerated rather than inferred solely by scanning other fields. Apply that principle to each algorithm's build: record selected source files, data files, build backend/version, interpreter/platform constraints, dependency artifacts, and generated-file inputs. Static Python import analysis can assist migration, but dynamic imports, plugins, runtime-loaded resources, and extension libraries require explicit declarations or validation evidence. [Nix derivations](https://nix.dev/manual/nix/2.35/store/derivation/)

Guix's `time-machine` operates using a chosen revision or recorded channel descriptions. The transferable idea is that the package universe and recipe definitions must themselves be reproducible inputs, rather than an implicit current catalog. The official HTML manual was inaccessible during this research, so this finding is based on the official indexed PDF. [GNU Guix reference manual: time-machine](https://guix.gnu.org/manual/devel/en/guix.pdf)

The 2024 study *Reproducibility of Build Environments through Space and Time* evaluates historical Nix environments and reports reproducing approximately seven million build environments and rebuilding 99.94% of 14,000 packages from a six-year-old revision. This is evidence for preserving complete environments and historical package definitions, not evidence that a Python manifest alone guarantees reproducibility. [Original paper](https://arxiv.org/abs/2402.00424)

The implementation should report its claims separately:

1. **Selection replay:** a lock chooses the same member versions, digests, and bindings.
2. **Installation replay:** a compatible environment installs the same dependency artifacts.
3. **Build reproduction:** independent builds produce byte-identical artifacts under recorded build inputs.

A deterministic ZIP writer can provide reproducible archives from the same prepared source tree. It does not by itself reproduce compilation, Python dependency selection, native runtimes, GPU kernels, training, model downloads, or remote API behavior. Random seeds and test vectors should be recorded where relevant, with numerical tolerances described as contract evidence.

## Keep Python's distribution transport

Wheels already provide a standard archive format, compatibility tags, installation metadata, and a `RECORD` inventory with secure file hashes. Publishing one algorithm does not require inventing an incompatible installer format. Build a wheel containing that algorithm's actual source closure and family/member metadata. The archive's SHA-256 and its file inventory serve different verification purposes. [PyPA wheel specification](https://packaging.python.org/en/latest/specifications/binary-distribution-format/)

The Simple Repository API already provides project-index discovery, artifact hashes, Python requirements, yanking, JSON serialization, and separately retrievable core metadata. Serve standard wheel indexes for existing installers, and a separate family/contract/search API for richer selection. Detached metadata means an agent can filter candidates before downloading their code. A standard Simple API is an interoperability layer; it does not supply semantic search or family constraint solving. [PyPA Simple Repository API](https://packaging.python.org/en/latest/specifications/simple-repository-api/)

Current Core Metadata is version 2.6, approved May 2026. `Import-Name` and `Import-Namespace`, introduced in 2.5, describe exclusive and shared import ownership and support collision detection. Emit ordinary `Requires-Dist`, `Requires-Python`, licensing, and project URLs, and use standard import ownership fields when the builder/toolchain supports them. Put the richer module document in an explicitly versioned additional file rather than assuming existing installers interpret custom capabilities. [PyPA Core Metadata](https://packaging.python.org/en/latest/specifications/core-metadata/)

Native namespace packages allow several distributions to contribute separate portions of a common namespace. They require compatible packaging conventions, including omitting a normal namespace-root `__init__.py`. A family may group distributions whose imports are entirely different; membership should not depend on a shared import path. When shared namespaces are used, each physical file needs one distribution owner. [PyPA namespace packaging guide](https://packaging.python.org/en/latest/guides/packaging-namespace-packages/)

Entry points standardize discovery of installed components through a group, name, and importable object reference. They can advertise local adapters or the module-system CLI. They are insufficient as the global catalog: they primarily describe installed distributions, and resolving their object references imports Python code. Read their metadata without loading the advertised objects. [PyPA entry points](https://packaging.python.org/en/latest/specifications/entry-points/)

PEP 751 is Final and its maintained specification is `pylock.toml`. It records environment applicability and exact package sources/artifacts, with hashes for archive artifacts, and supports wheels. Use it for the Python distribution environment when a conforming lock is available. A custom family binding lock may coexist with it, but should identify itself as a different format unless it meets the complete standard. Do not label a list of family members a PEP 751 lock when transitive Python dependencies remain unresolved. [PEP 751](https://peps.python.org/pep-0751/), [PyPA pylock.toml specification](https://packaging.python.org/en/latest/specifications/pylock-toml/)

For the Mari migration specifically, inspection of the source project's `pyproject.toml` found distribution name `mark-kit`, import metadata for `mark_kit`, Python >=3.11, base NumPy, and several optional solver and integration dependencies. The family name can remain Mari while preserving an explicit mapping to source distributions and import packages. Each algorithm wheel should require only its actual closure; a wrapper that still depends on all of `mark-kit` would not demonstrate independent distribution.

## WIT/component model: contracts describe requirements and exports

WIT defines interfaces containing types and functions, worlds containing imports and exports, and packages organizing interfaces and worlds. The document explicitly limits WIT to contracts rather than behavior. This is a useful template for a family member that provides `rank` while requiring a distance function or embedding provider: dependencies are named interfaces, and a composition supplies implementations. JSON schemas can provide a useful Python boundary for the first version, while nominal family/type identifiers preserve distinctions that identical JSON shapes may hide. [WIT reference](https://component-model.bytecodealliance.org/design/wit.html)

A world describes both provided functionality and required functionality; a component can satisfy another component's imports in place of a host. For module families, maintain a difference between ordinary installation dependencies and logical requirements that can be satisfied by alternate members. Record the resulting bindings in a lock, and validate required signatures and shared type identities before invocation. [WIT worlds](https://component-model.bytecodealliance.org/design/worlds.html)

An execution boundary is a separate architectural decision. Wasmtime documents WebAssembly sandboxing and capability-based WASI filesystem access. This can provide a future runtime for a compatible subset, with host resources explicitly granted, but WIT files alone do not supply that security. Native Python, NumPy, extension modules, GPU code, and remote backends need different deployment arrangements; compatibility and performance must be demonstrated for each execution backend. [Wasmtime security](https://docs.wasmtime.dev/security.html)

## Agent-facing discovery: bounded context with evidence

The dated MCP tools specification supports listing tools with pagination, tool descriptions, input schemas, and optional output schemas. It expressly treats tool annotations from untrusted servers as untrusted. An adapter can expose a small stable set of catalog operations such as search, describe, resolve, and invoke instead of injecting every published algorithm into every agent context. Descriptions of third-party members must remain data, never instructions with authority over the agent. [MCP tools, 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

The current draft additionally recommends deterministic ordering to improve cache behavior. Treat that as draft guidance, not a requirement retroactively imposed on the dated protocol. In our catalog, deterministic paging over a named snapshot is useful independently of protocol version. [MCP draft tools](https://modelcontextprotocol.io/specification/draft/server/tools)

Agentic Resource Discovery (ARD), introduced in Hugging Face's June 17, 2026 announcement as a draft specification, targets capability search across registries. The project explicitly separates discovery from native invocation and permits independent discovery services with different trust and ranking policies. It is a promising future interoperability target for exported family/member catalog entries; the first implementation should keep a data/CLI boundary that an ARD adapter could consume without claiming protocol conformance. [Hugging Face's ARD announcement](https://huggingface.co/blog/agentic-resource-discovery-launch), [ARD project](https://agenticresourcediscovery.org/)

Recommended staged retrieval:

1. Search compact family summaries by problem and hard constraints.
2. Retrieve only candidate member manifests and contract documents.
3. Filter by environment, required capabilities, trust policy, and dependency compatibility.
4. Rank remaining candidates by task context and explicit evaluation evidence.
5. Produce a deterministic selection record containing candidates, rejection reasons, chosen versions/digests, and parameter bindings.
6. Fetch and verify artifacts, then load through the selected execution backend.

Each member should publish positive use cases, unsuitable cases, input assumptions, complexity or measured cost, determinism information, dependency costs, effects, known limitations, and example inputs/results. Benchmark evidence should have its own identity, dataset digest, environment, metric, evaluation version, and producer. No single natural-language similarity score should override a failed hard constraint. This is the principal agent-oriented extension beyond ordinary package metadata.

## Provenance and revocation at distribution scale

The maintained PEP 740 specification defines index-hosted attestations bound to a distribution filename and SHA-256, with verification of certificate, expected identity, statement, and signature. Its provenance container may acquire additional attestations over time. That directly supports separate immutable artifacts and evolving evidence. A first implementation may record provenance fields, but unsigned JSON is not a verified attestation and must not be represented as one. [PyPA index-hosted attestations](https://packaging.python.org/en/latest/specifications/index-hosted-attestations/)

SLSA requires verification against expected build identities and parameters, as well as matching artifact digests and valid provenance signatures. A valid signature from an arbitrary publisher is not enough to establish that this is the intended implementation. Track namespace authority, source repository, release workflow, builder identity, and expected contract authority separately; consumers decide which identities they trust. [SLSA v1.2 artifact verification](https://slsa.dev/spec/v1.2/verifying-artifacts)

SLSA's threat analysis also highlights build caches whose keys omit transitive inputs. Cache lookup must incorporate the complete declared dependency closure and build configuration. Provenance establishes a claim about how an artifact was produced; it does not establish algorithm correctness or that a dependency is harmless. [SLSA v1.2 threats](https://slsa.dev/spec/v1.2/threats)

Content hashes do not establish freshness: an old vulnerable artifact remains correctly hashed. TUF addresses authenticated updates through roles, threshold signatures, expiring metadata, snapshots, and client checks against rollback, freeze, and inconsistent metadata attacks. For a public or federated catalog, prefer a mature TUF implementation over an improvised signing protocol. Local append-only publication can be an initial storage property, but it is not equivalent to TUF or globally authenticated history. [TUF specification 1.0.36, 2026-08-05](https://theupdateframework.github.io/specification/latest/)

## Implementation priorities and honest limits

The first end-to-end implementation should establish independently buildable algorithm wheels, immutable artifact digests, a validated family/member schema, import-free discovery, explicit contracts, inspectable resolution, lock verification, and controlled execution of already selected members. Meaningful tests should prove that one selected algorithm works in an isolated installation without the whole Mari repository, that missing or conflicting requirements fail before execution, that artifact tampering is rejected, and that catalog discovery cannot execute package import code.

Then add integration with conforming Python locks, detached metadata and a Simple index, evaluated selection quality, signed provenance verification, remote catalog snapshots, and an explicit sandbox backend. These are independent features and should be reported independently in documentation and CLI output.

The scale hypothesis remains to be measured. A local catalog with hundreds of independently published algorithms demonstrates granularity, not global infrastructure scale. Measure index size, search latency, cold and warm resolution cost, artifact deduplication, graph expansion, installation size, metadata update throughput, and agent selection accuracy. Family namespacing, immutable blob storage, paging, and lazy retrieval provide a plausible path, while registry federation, malicious catalog entries, revocation, distributed consistency, and multi-version native dependencies remain substantive engineering work.

## Sources

Every linked source in this review appears below. Access date: 2026-09-08. A maintained page without a verified publication date is identified as such; access and search-engine crawl dates are not publication dates.

1. Unison project. *The Big Idea*. Maintained language documentation, publication date not stated. [Official documentation](https://www.unison-lang.org/docs/the-big-idea/).
2. Unison project. *Hashes*. Maintained language reference, publication date not stated. [Official reference](https://www.unison-lang.org/docs/language-reference/hashes/).
3. Python Software Foundation. *The Import System*. Python 3 language reference; consulted page identifies Python 3.14.7. Maintained documentation. [Official reference](https://docs.python.org/3/reference/import.html).
4. Nix project. *Glossary*. Nix Reference Manual 2.35.2, maintained documentation. [Versioned reference](https://nix.dev/manual/nix/2.35/glossary.html).
5. Nix project. *Store Derivation and Deriving Path*. Nix Reference Manual 2.35.2, maintained documentation. [Versioned reference](https://nix.dev/manual/nix/2.35/store/derivation/).
6. GNU Guix project. *GNU Guix Reference Manual*, development edition, section *Invoking guix time-machine*. Official PDF consulted through indexed text; direct HTML access failed. A current publication date was not verified. [Official manual](https://guix.gnu.org/manual/devel/en/guix.pdf).
7. Julien Malka, Stefano Zacchiroli, and Théo Zimmermann. *Reproducibility of Build Environments through Space and Time*. ICSE 2024, New Ideas and Emerging Results track, April 2024; preprint submitted February 1, 2024. DOI: 10.1145/3639476.3639767. [Authors' preprint](https://arxiv.org/abs/2402.00424).
8. Python Packaging Authority. *Binary Distribution Format*. Python Packaging User Guide, maintained wheel specification. [Official specification](https://packaging.python.org/en/latest/specifications/binary-distribution-format/).
9. Python Packaging Authority. *Simple Repository API*. Python Packaging User Guide, maintained repository specification. [Official specification](https://packaging.python.org/en/latest/specifications/simple-repository-api/).
10. Python Packaging Authority. *Core Metadata Specifications*. Version 2.6 approved May 2026; maintained in the Python Packaging User Guide. [Official specification](https://packaging.python.org/en/latest/specifications/core-metadata/).
11. Python Packaging Authority. *Packaging Namespace Packages*. Python Packaging User Guide, maintained guide. [Official guide](https://packaging.python.org/en/latest/guides/packaging-namespace-packages/).
12. Python Packaging Authority. *Entry Points Specification*. Originally formalized October 2017; consulted page updated September 1, 2026. [Official specification](https://packaging.python.org/en/latest/specifications/entry-points/).
13. Brett Cannon. *PEP 751: A File Format to Record Python Dependencies for Installation Reproducibility*. Python Enhancement Proposals; created July 24, 2024, accepted March 31, 2025, status Final at access. [PEP](https://peps.python.org/pep-0751/).
14. Python Packaging Authority. *pylock.toml Specification*. Maintained normative specification originating in PEP 751. [Official specification](https://packaging.python.org/en/latest/specifications/pylock-toml/).
15. Bytecode Alliance. *An Overview of WIT*. The WebAssembly Component Model documentation, maintained reference. [Official reference](https://component-model.bytecodealliance.org/design/wit.html).
16. Bytecode Alliance. *WIT Worlds*. The WebAssembly Component Model documentation, maintained reference. [Official reference](https://component-model.bytecodealliance.org/design/worlds.html).
17. Bytecode Alliance / Wasmtime project. *Security*. Maintained Wasmtime documentation. [Official documentation](https://docs.wasmtime.dev/security.html).
18. Model Context Protocol project. *Tools*. Specification revision November 25, 2025. [Dated specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
19. Model Context Protocol project. *Tools*. Current draft at access; recommendations may change and are distinguished from the dated specification above. [Draft specification](https://modelcontextprotocol.io/specification/draft/server/tools).
20. Ben Burtenshaw and Shaun Smith. *Agentic Resource Discovery: Let Agents Search for Tools, Skills, and Other Agents*. Hugging Face, June 17, 2026. [Publisher announcement](https://huggingface.co/blog/agentic-resource-discovery-launch).
21. Agentic Resource Discovery working group. *Agentic Resource Discovery Specification*. Maintained project overview; no separate publication date verified. [Official project](https://agenticresourcediscovery.org/).
22. Python Packaging Authority. *Index Hosted Attestations*. Maintained specification originating in PEP 740; consulted page updated September 1, 2026. [Official specification](https://packaging.python.org/en/latest/specifications/index-hosted-attestations/).
23. SLSA project. *Build: Verifying Artifacts*. Approved specification v1.2. [Versioned specification](https://slsa.dev/spec/v1.2/verifying-artifacts).
24. SLSA project. *Threats & Mitigations*. Approved specification v1.2. [Versioned specification](https://slsa.dev/spec/v1.2/threats).
25. Justin Cappos, Trishank Karthik Kuppusamy, Joshua Lock, Marina Moore, and Lukas Pühringer, editors. *The Update Framework Specification*. TUF project, version 1.0.36, last modified August 5, 2026. [Official specification](https://theupdateframework.github.io/specification/latest/).
