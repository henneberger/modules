# Module systems research and implications for Python module families

Research date: 2026-09-08. This note covers the programming-language theory strand of the project. It is a targeted, source-checked review of influential foundations and relevant continuations through 2026, not a systematic review of every module-system publication. Recommendations below are design inferences for this project; the cited papers do not establish the correctness, scalability, or usefulness of our Python implementation.

## The central design lesson

A module system earns its name by describing and checking how software units compose. A catalog of small packages is useful but insufficient: it needs interfaces, explicit dependency requirements, identity rules, and a reproducible account of linking. The strongest retrofit precedent is Backpack, which brings separately typecheckable interfaces and mixin-style linking to packages above Haskell's existing module language. Its authors explicitly prioritize integration and simplicity over maximal expressiveness. That is a productive architectural starting point for Python. [Backpack, 2014](https://people.mpi-sws.org/~dreyer/papers/backpack/paper.pdf)

Our additional proposal is to make problem context, selection constraints, evaluation evidence, and agent-readable explanations part of a family's public metadata. This is a design hypothesis for software distribution. The historical literature reviewed here does not demonstrate that agents outperform people generally, or that greater agent capability removes the need for understandable semantics. It does motivate moving repetitive composition work into tools while retaining small explicit interfaces.

## Terminology that matters

There are three related meanings of “family” that should remain distinct:

1. **Parameterized ML modules.** Structures contain components; signatures describe their interfaces; functors produce modules from module arguments. Related modules can therefore form parameterized families. Harper and Pierce's module-system chapter explicitly distinguishes “Signature Families” and “Module Families.” The author's published table of contents verifies this terminology; the entire chapter was not available from the author in this review. [Harper and Pierce, 2005, chapter 8](https://www.cis.upenn.edu/~bcpierce/attapl/frontmatter.pdf)
2. **Family polymorphism.** This is a later technical term for maintaining relationships among a collection of related types and implementations as a family varies or is extended. Ernst introduced it in 2001 in the object-oriented gbeta setting. Merely placing packages under a family name does not provide its safety properties. [Ernst, 2001](https://pure.au.dk/portal/en/publications/family-polymorphism/)
3. **Distribution and discovery families.** In this project, `mari` can be an umbrella of separately published algorithms with common problem context. Within it, a `ranker` signature can define a substitutable set, while a tokenizer and a graph solver may share the umbrella without being substitutes. This third meaning is our proposed product concept.

For the payment example, a payment family might define processor, token, receipt, and webhook interfaces. “Processor A and Processor B both accept a dictionary” is much weaker than showing that their currency conventions, idempotency behavior, receipt provenance, and error contracts agree. A Processor A token may need to remain unusable with Processor B even if both happen to represent tokens as strings. Interface shape, semantic substitutability, and family-specific identity are separate concerns.

## Foundational mechanisms

### Structures, signatures, and functors

David MacQueen's modules proposal was circulated in August 1983 and published in 1984. Its goals included structuring large programs, generic library units, and separate compilation. The enduring separation is between an implementation, the public assumptions sufficient for clients, and a parameterized constructor for implementations. Original drafts and contemporaneous sharing proposals are preserved in the Standard ML historical archive. [MacQueen manuscript and design archive](https://smlfamily.github.io/history/index.html), [MacQueen, Harper, and Reppy, 2020](https://www.cs.cmu.edu/~rwh/papers/history/main.pdf)

**Project inference:** a member manifest should declare exports and dependency slots. A dependency slot should say which signature and capabilities it needs. A separate composition binds each slot to a concrete member. An algorithm family should not require clients to import every available member or depend on the family maintainer's preferred implementation.

### Abstract and manifest types; sharing

Leroy's 1994 work distinguishes abstract types from manifest types whose definitions are part of the interface. This makes separate compilation possible without revealing implementation details by accident. Lillibridge's translucent-sum account likewise supports selectively revealing type identities while hiding representations. Clients need to know certain equalities, such as whether a parser's output type is exactly the evaluator's input type, without learning the types' internals. [Leroy, 1994](https://doi.org/10.1145/174675.176926), [Lillibridge, 1997](https://www.cs.cmu.edu/~rwh/students/lillibridge.pdf)

**Project inference:** stable signature identifiers and explicit shared schema references are more useful than a bag of tags. A composition should have an explicit place to require shared representation or provider identity. A schema digest only establishes exact schema identity; it does not establish semantic equivalence between two differently written schemas or between their implementations. Signature compatibility needs a stated algorithm and versioning policy.

### Applicative and generative module instantiation

Leroy's applicative functors preserve compatibility of abstract result types when applied to provably equal arguments. Standard ML-style generative instantiation can create distinct abstract types on different applications. The difference concerns type identity and abstraction, not whether an artifact was downloaded twice. These ML terms are also distinct from Haskell's `Applicative` abstraction for effects. [Leroy, 1995](https://xavierleroy.org/publi/applicative-functors.pdf)

Rossberg, Russo, and Dreyer show in *F-ing modules* that a substantial ML module language can elaborate to System Fω. Their treatment connects applicative versus generative behavior to computational purity, and covers first-class packaging and structure sharing; cross-module recursion remains outside its main coverage. The paper proves soundness and decidable typechecking for its calculus. [Rossberg, Russo, and Dreyer, 2014](https://research.google/pubs/f-ing-modules/)

**Project inference:** distinguish immutable artifact identity, binding identity, and runtime instance identity. Reusing a content digest can safely reuse bytes; it does not imply it is safe to reuse a stateful object, credentials, a network client, or a random generator. A recipe can be deterministic while each invocation creates fresh state. The Python implementation should use ordinary names such as `artifact_digest` and `instance_id` unless it implements the stronger semantics denoted by the ML vocabulary.

### First-class modules

Rossberg's 1ML unifies the module and core layers: structures resemble records, and functions, functors, and type constructors share one construct. It supports dynamic module selection with a typed interpretation in Fω. The original ICFP paper appeared in 2015, a special-effects extension in 2016, and a revised journal version in 2018. Type inference is explicitly not complete. [1ML author project and papers](https://people.mpi-sws.org/~rossberg/1ml/)

**Project inference:** Python already permits passing modules, callables, and objects as values. We can represent a resolved member as a first-class object carrying an immutable descriptor and explicit bindings, then load it only when needed. There is little initial value in inventing new Python syntax or reproducing an entire dependent module calculus. Python dynamism does not confer 1ML's static guarantees.

### Recursive and mixin modules

Recursive composition introduces subtleties beyond a dependency graph. Dreyer's 2007 recursive-module calculus addresses the “double vision” problem: a module can see a recursive partner through both an abstract interface and implementation-related information. Its solution uses a two-pass typing account. [Dreyer, 2007](https://people.mpi-sws.org/~dreyer/papers/recmod/main-long.pdf)

MixML combines ML hierarchy and abstraction with mixin recursive linking. Modules may contain both defined exports and declared but unfilled imports. Linking satisfies the missing components; it is not equivalent to inheritance by text concatenation. The expanded paper provides a declarative type system and a three-pass checking algorithm. [Rossberg and Dreyer, 2013; conference predecessor 2008](https://research.google/pubs/mixin-up-the-ml-module-system/)

**Project inference:** member dependency holes are valuable immediately. General recursive linking should wait until the system specifies initialization order and what may be used before initialization finishes. Rejecting cycles with a concrete cycle path is a principled initial boundary. Independent publication does not require every lexical definition to be independently initializable.

### Modular implicits and selection

White, Bour, and Yallop's modular implicits infer module parameters from type information and elaborate them into explicit first-class functors. The work supports inheritance, constraints, associated types, and higher-kinded cases through existing module machinery. Its origin is the ML/OCaml 2014 meeting, with post-proceedings published in 2015. [White, Bour, and Yallop, 2015](https://arxiv.org/abs/1512.01895)

**Project inference:** an agent may use problem context to propose bindings, but execution should consume an explicit, inspectable resolution. Semantic search can rank candidates; it cannot establish uniqueness or compatibility. If multiple implementations satisfy a request, selection should record the chosen member and reasons. An automatic resolver should either follow declared policy or return an ambiguity diagnostic, rather than conceal an arbitrary provider choice.

## Modern continuations

### Familia, FPOP, and Persimmon

Familia unifies interfaces, type classes, and family polymorphism, including retroactive constraint modeling and module-level inheritance with further binding. Its authors give a sound core calculus and examples of extensible software. [Zhang and Myers, OOPSLA 2017](https://www.cs.cornell.edu/andru/papers/familia/)

FPOP applies family polymorphism to extensible definitions and proofs in Coq. The PLDI 2023 paper includes a prototype plugin, a dependent type theory, consistency and canonicity results, and case studies. Its significance here is that extension can encompass correctness evidence as well as executable code. [Jin, Amin, and Zhang, 2023](https://cs.uwaterloo.ca/~yizhou/papers/fpop-pldi2023.pdf)

Persimmon adds nested family polymorphism, extensible variants, and extensible pattern matching to a functional setting. Related nested components remain aligned when a family is extended; relative paths are adjusted consistently in inherited code. It provides a Scala translation and proves progress and preservation. This directly addresses larger coherent extension units rather than only isolated functions. [Kravchuk-Kirilyuk et al., OOPSLA 2024](https://cs.uwaterloo.ca/~yizhou/papers/persimmon-oopsla2024.pdf)

**Project inference:** future family extensions should be able to inherit context, schemas, behavioral tests, and composition recipes, then override selected members as one reviewed change. A manifest inheritance feature alone would not implement Persimmon: Python cannot automatically make arbitrary old code handle new variants correctly. The near-term translation is to track compatibility obligations and rerun inherited contract suites.

### Phase distinction and abstraction, 2021

Sterling and Harper give a modal account of the static/dynamic phase distinction and computational effects in module systems. Their central theorem generalizes parametricity to proof-relevant, phase-sensitive structures. The first arXiv version is dated 2020; the JACM publication is October 2021. [Sterling and Harper, 2021](https://doi.org/10.1145/3474834)

**Project inference:** metadata discovery, constraint resolution, artifact construction, and execution should be distinguishable operations. Describing a module should not require importing it. A build that executes code should be represented as a build action with dependencies and results. This is an engineering application of phase separation, not an implementation of the paper's modalities.

### Mω and transparent ascription, 2024

*Fulfilling OCaml Modules with Transparency* formalizes a large subset of OCaml modules with both applicative and generative functors. It adds transparent ascription and uses an intermediate Mω representation with quantifiers, translating into Fω. Transparent existential types provide a more precise account of abstraction inside applicative functors. [Blaudeau, Rémy, and Radanne, OOPSLA 2024](https://clement.blaudeau.net/assets/pdf/blaudeau_ocaml_modules.pdf)

**Project inference:** schema projection and private implementation details deserve explicit rules. A public signature may intentionally expose an alias or hide a representation. Do not infer those visibility choices from the implementation source when publishing a package. The paper also illustrates that even established module implementations have edge cases that require careful specification.

### ZipML and signature avoidance, 2025

ZipML tackles signature avoidance: inferred interfaces may depend on names that have gone out of scope. It introduces floating fields tracked with signature zippers, retains necessary hidden information, and simplifies it without losing sharing. Lazy strengthening and inlining avoid duplication of signature structures. Correctness follows through elaboration into Mω. [Blaudeau, Rémy, and Radanne, POPL 2025](https://clement.blaudeau.net/assets/pdf/zipml.final.pdf)

**Project inference:** do not recursively inline every dependency's full metadata into every algorithm descriptor. Maintain shared references and expose a bounded, relevant view to an agent. Internal resolver state may need more provenance than the public summary displays. This analogy should not be presented as a performance result for our registry; our scaling must be measured independently.

### Staging and module functors, 2024

Chiang, Yallop, White, and Xie study module functors combined with MacoCaml's compile-time code generation. They make evaluation-order choices explicit and establish type soundness, elaboration soundness, and phase distinction. [ICFP 2024 paper](https://www.cl.cam.ac.uk/~jdy22/papers/staged-compilation-with-module-functors.pdf)

**Project inference:** agent-generated adapters and algorithm specialization should be explicit derivations with source inputs, parameters, tool versions, and validation results. A generated adapter is a new artifact. It should not silently alter the meaning of an already published member during loading.

### Adjacent 2026 work

*Extensible Data Types with Ad-Hoc Polymorphism* combines rows with type classes, including constraints across row fields, folding, and splitting. Its authors mechanize the soundness development in Lean 4 and evaluate table operations. This is adjacent work on extensible interfaces and generic operations, rather than a replacement module system or package-distribution architecture. [Toohey, Chen, Jamalzadeh, and Xie, POPL 2026](https://doi.org/10.1145/3776662)

**Project inference:** interface extension cannot be reduced to adding JSON fields. Generic consumers can require properties of every field or variant; future contracts may need richer constraint languages. For now, explicit schema versions and executable examples are a tractable boundary.

## Proposed semantics for the first implementation

The following recommendations are our synthesis, not results claimed by the papers.

| Concept | Proposed representation | Required practical guarantee |
| --- | --- | --- |
| Family | Stable family ID, purpose, problem vocabulary, scope, member index | Discovery can inspect it without importing member code |
| Member | Stable ID and version, source selection, exports, capability metadata | Each algorithm can be built and addressed independently |
| Signature | Versioned interface ID, operation descriptions, data schemas, behavioral examples | Compatibility claims are inspectable; same family does not imply interchangeability |
| Requirement | Named dependency slot plus signature and capability constraints | Missing or incompatible bindings fail before invoking the algorithm |
| Composition | Explicit map from slots to members, configuration, sharing constraints | The selected software graph is reproducible and explainable |
| Artifact | Immutable payload digest plus provenance and build metadata | The installed bytes can be checked against what was selected |
| Lock | Concrete member versions and artifact identities for the transitive closure | Resolution is frozen; later catalog changes cannot silently alter it |
| Evidence | Test result, benchmark conditions, dataset identity, source references | Claims are attributable and falsifiable |
| Runtime instance | Loaded exports plus explicit dependency bindings | Artifact reuse is distinct from sharing mutable state |

For `mari`, first inventory algorithm entry points and their real source dependencies. Copy the project, preserve license and provenance, and publish members that contain or require everything they actually use. An individually addressable wrapper that always installs the whole original monolith would demonstrate namespacing, but would not satisfy the stated small-unit distribution objective. Some shared internals should be separately versioned support members; others can be bundled into a member's closure. Either choice needs an explicit ownership and upgrade policy.

A useful acceptance example is two implementations of one ranker interface and a composition that can bind either. Another is one algorithm whose import closure is much smaller than the original distribution, installed and executed in a fresh environment. A third is a deliberate incompatibility: the loader should explain which signature or shared representation requirement failed before using the selected callable.

The initial release should state its guarantees narrowly: manifest/schema validation, explicit binding checks, reproducible resolution, payload verification, and tested callable behavior. Ordinary Python annotations, structural protocols, JSON Schema, and a passing test suite do not prove ML-style static soundness or semantic substitution for all programs. A future formal model can specify the resolver and linking calculus separately from arbitrary Python execution.

## Research and implementation boundaries

This review followed the ML-module lineage and modern family-polymorphism work through author pages, institution-hosted papers, publisher records, and conference pages. It inspected abstracts and relevant introductions/mechanisms; it did not independently verify all formal proofs, evaluate every artifact, or conduct exhaustive citation snowballing. Search results with misleading crawl dates were checked against dates in the papers. Future OOPSLA 2026 listings were not treated as completed conference evidence at this research date.

Coverage is strongest for ML signatures/functors, abstract type identity, first-class modules, recursive/mixin linking, family polymorphism, and phase separation. Other important strands need a separate review: Racket units and gradual contracts; Scala/DOT and path-dependent types; proof-carrying components; algebraic effects and capability modules; WebAssembly component interfaces; Unison-style content-addressed definitions; software product lines; package solver theory; Python packaging standards; and supply-chain provenance. Those areas should not be silently represented as covered by this document.

There is no basis here for claiming that “all modern module research” has been read, that this architecture is novel in every respect, or that millions of independent Python distributions already scale well under it. The useful claim is narrower: the design choices are informed by a verified sample of the relevant foundations and recent work, with concrete experiments needed to validate the distribution hypothesis.

## Bibliography

Dates below are publication dates or years, not web crawl timestamps. Paper titles are retained for identification. Links lead to author/institution copies, publisher records, or original project pages.

1. David MacQueen. **Modules for Standard ML.** ACM Symposium on LISP and Functional Programming, 1984, pp. 198–207. DOI: 10.1145/800055.802036. [Original 1983 draft and archive](https://smlfamily.github.io/history/index.html).
2. David MacQueen, Robert Harper, and John Reppy. **The History of Standard ML.** Proceedings of the ACM on Programming Languages 4, HOPL, Article 86, June 2020. [Author PDF](https://www.cs.cmu.edu/~rwh/papers/history/main.pdf).
3. Robert Harper and Benjamin C. Pierce. **Design Considerations for ML-Style Module Systems.** In *Advanced Topics in Types and Programming Languages*, chapter 8, MIT Press, 2005. [Author frontmatter and contents](https://www.cis.upenn.edu/~bcpierce/attapl/frontmatter.pdf). This review verified the contents, not the entire chapter.
4. Xavier Leroy. **Manifest Types, Modules, and Separate Compilation.** POPL 1994, pp. 109–122. [Publisher record](https://doi.org/10.1145/174675.176926).
5. Robert Harper and Mark Lillibridge. **A Type-Theoretic Approach to Higher-Order Modules with Sharing.** POPL 1994, pp. 123–137; technical report predecessor October 1993. [Authors' institutional bibliography](https://www.cs.cmu.edu/~fox/publications.html).
6. Mark Lillibridge. **Translucent Sums: A Foundation for Higher-Order Module Systems.** PhD thesis, Carnegie Mellon University, CMU-CS-97-122, May 1997. [Thesis PDF](https://www.cs.cmu.edu/~rwh/students/lillibridge.pdf).
7. Xavier Leroy. **Applicative Functors and Fully Transparent Higher-Order Modules.** POPL 1995, pp. 142–153. [Author PDF](https://xavierleroy.org/publi/applicative-functors.pdf).
8. Erik Ernst. **Family Polymorphism.** ECOOP 2001, LNCS 2072, pp. 303–326, June 2001. DOI: 10.1007/3-540-45337-7_17. [Author institution record](https://pure.au.dk/portal/en/publications/family-polymorphism/).
9. Derek Dreyer. **A Type System for Recursive Modules.** ICFP 2007, pp. 289–302. [Author PDF](https://people.mpi-sws.org/~dreyer/papers/recmod/main-long.pdf).
10. Andreas Rossberg and Derek Dreyer. **Mixin' Up the ML Module System.** ACM Transactions on Programming Languages and Systems 35(1), 2013; ICFP predecessor 2008. [Author research record](https://research.google/pubs/mixin-up-the-ml-module-system/).
11. Andreas Rossberg, Claudio Russo, and Derek Dreyer. **F-ing Modules.** Journal of Functional Programming 24(5), 2014. [Author research record](https://research.google/pubs/f-ing-modules/).
12. Scott Kilpatrick, Derek Dreyer, Simon Peyton Jones, and Simon Marlow. **Backpack: Retrofitting Haskell with Interfaces.** POPL 2014, pp. 19–31. DOI: 10.1145/2535838.2535884. [Author PDF](https://people.mpi-sws.org/~dreyer/papers/backpack/paper.pdf).
13. Leo White, Frédéric Bour, and Jeremy Yallop. **Modular Implicits.** EPTCS 198, pp. 22–63, 2015; ML/OCaml 2014 post-proceedings. DOI: 10.4204/EPTCS.198.2. [Author preprint](https://arxiv.org/abs/1512.01895).
14. Andreas Rossberg. **1ML — Core and Modules United.** ICFP 2015; expanded Journal of Functional Programming 28 version, 2018. **1ML with Special Effects.** WadlerFest 2016. [Author project with all versions](https://people.mpi-sws.org/~rossberg/1ml/).
15. Yizhou Zhang and Andrew C. Myers. **Familia: Unifying Interfaces, Type Classes, and Family Polymorphism.** PACMPL 1, OOPSLA, Article 70, October 2017. DOI: 10.1145/3133894. [Author project](https://www.cs.cornell.edu/andru/papers/familia/).
16. Jonathan Sterling and Robert Harper. **Logical Relations as Types: Proof-Relevant Parametricity for Program Modules.** Journal of the ACM 68(6), Article 41, October 2021; arXiv predecessor October 2020. [Publisher record](https://doi.org/10.1145/3474834).
17. Ende Jin, Nada Amin, and Yizhou Zhang. **Extensible Metatheory Mechanization via Family Polymorphism.** PACMPL 7, PLDI, Article 172, June 2023. DOI: 10.1145/3591286. [Author PDF](https://cs.uwaterloo.ca/~yizhou/papers/fpop-pldi2023.pdf).
18. Clément Blaudeau, Didier Rémy, and Gabriel Radanne. **Fulfilling OCaml Modules with Transparency.** PACMPL 8, OOPSLA1, Article 101, April 2024. DOI: 10.1145/3649818. [Author PDF](https://clement.blaudeau.net/assets/pdf/blaudeau_ocaml_modules.pdf). The paper's first-author name is typeset in reversed order in some records; the author's own name is Clément Blaudeau.
19. Anastasiya Kravchuk-Kirilyuk, Gary Feng, Jonas Iskander, Yizhou Zhang, and Nada Amin. **Persimmon: Nested Family Polymorphism with Extensible Variant Types.** PACMPL 8, OOPSLA1, Article 119, April 2024. DOI: 10.1145/3649836. [Author PDF](https://cs.uwaterloo.ca/~yizhou/papers/persimmon-oopsla2024.pdf).
20. Tsung-Ju Chiang, Jeremy Yallop, Leo White, and Ningning Xie. **Staged Compilation with Module Functors.** PACMPL 8, ICFP, Article 260, August 2024. DOI: 10.1145/3674649. [Author PDF](https://www.cl.cam.ac.uk/~jdy22/papers/staged-compilation-with-module-functors.pdf).
21. Clément Blaudeau, Didier Rémy, and Gabriel Radanne. **Avoiding Signature Avoidance in ML Modules with Zippers.** PACMPL 9, POPL, Article 66, January 2025. DOI: 10.1145/3704902. [Author PDF](https://clement.blaudeau.net/assets/pdf/zipml.final.pdf).
22. Matthew Toohey, Yanning Chen, Ara Jamalzadeh, and Ningning Xie. **Extensible Data Types with Ad-Hoc Polymorphism.** PACMPL 10, POPL, Article 20, January 8, 2026. DOI: 10.1145/3776662. [Publisher record](https://doi.org/10.1145/3776662).
