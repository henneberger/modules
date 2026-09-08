# Mixin modules: implementation handoff

**Status: 0.3.0 implements nonrecursive mixin linking through open TOML module graphs.** See the [module build guide](module-build.md) for shipped semantics. `resolve-module` selects/checks nodes; `build-module` emits fixed Python wiring and a publishable constructor retaining open ports. Explicit projection/renaming, partial graph reuse, nominal checks, semantic indices, and shared node instances work. General recursive mixin linking remains unimplemented.

The [whole-system proposal](build-time-module-composition.md) develops the broader direction. The sections below retain the original research handoff; the next section distinguishes the shipped subset from remaining work.

## Implemented subset and remaining handoff

The nonrecursive implementation lowers graphs to ordinary module factories. An open graph can be independently published and selected in another graph or closed through synthesis. Existing Python factories receive selected modules explicitly. Runtime preparation checks call shapes and inputs; generated calls initialize each named node once, in a build-checked order. Tests cover nested publication, order-sensitive enrichment, shared/fresh instances, semantic-index rejection, explicit export renaming, and offline replay.

Remaining work: delayed recursive ports and eager-initialization analysis; coherent nested family extension; inferred lifecycle/cleanup; stronger behavioral capability rules; arbitrary topology synthesis; and reviewed transformations for source that cannot accept explicit module dependencies. The current parser still rejects `kind = "mixin"` in family member declarations: authored graph modules have their own TOML grammar and lower to existing `functor` or `module-factory` members.

In the ML module-system sense, a mixin is a partially defined module: it exports some components while requiring others. Linking fragments can fill each other's holes, merge their exports, and preserve relationships among types. The remaining holes form the linked module's requirements. See the existing [recursive and mixin module research](research-theory.md#recursive-and-mixin-modules), especially MixML and Backpack. Ordinary Python class inheritance does not establish these module-level semantics.

## Original handoff: required behavior

- A first-class representation of open module fragments and their unfilled value/type components.
- Checked export merging, explicit renaming and hiding, and deterministic conflict rules.
- Late binding through the final linked module, so an inherited operation can use another fragment's implementation.
- A distinction between legal recursive function references and unsafe initialization cycles.
- Synthesis over fragment links, with links and ordering committed to the program lock.

Constructor expressions remain trees, but compiled graphs now encapsulate shared nodes and explicit export projections. Neither route rebinds an existing function's globals. Compiler support for recursive Python definitions is separate from recursive module initialization.

## Suggested first implementation

Start with **explicit, nonrecursive linking**. Add an open-fragment intermediate representation containing defined exports, required ports, exact signatures, and shared type identities. Specify collision behavior before adding syntax: reject duplicate providers unless the user explicitly renames or selects one; never use implicit last-writer-wins overrides. Unfilled ports should remain visible until a final closed program is requested.

Lower a fragment to a factory with explicit dependencies and an export mapping, reusing `contracts.py`, `composition.py`, and the existing artifact compiler. Preserve source semantics: code that needs late binding must use an explicit dependency parameter or a reviewed transformation. Do not silently rewrite arbitrary Python globals or rely on class MRO.

Then extend `planning.py`, `synthesis.py`, and `assemblies.py` with a link expression and its validation rules. Record fragment identities, port wiring, renames, conflict resolutions, and shared types in the assembly lock. Effects and behavioral capabilities remain obligations with evidence; merging capability labels does not prove useful combined behavior. Only after the nonrecursive case works should recursive linking add an initialization analysis and well-defined delayed references.

The whole-system proposal sketches TOML syntax, but no grammar has shipped. The current parser should continue rejecting `kind = "mixin"` until its semantics and implementation exist. There is no compatibility requirement for this extension.

## Acceptance example and tests

Use existing HTTP client software as a candidate: one fragment supplies transport, another retry policy, and another request logging. A goal should find and link fragments satisfying a client signature without a handwritten constructor tree. Keep retry applicability explicit, including which failures and operations permit another attempt.

Verify that missing ports and ambiguous providers produce diagnostics; duplicate exports require an explicit decision; shared request/response types retain identity; wrapper order is recorded and observably testable; every dependency is prepared before a closed binding is exposed; and the locked composition replays offline. Add a late-binding example where an operation resolves another operation through the final module, plus an initialization cycle that is rejected before executing factories.

The original plan informed the shipped nonrecursive subset. Recursive linking and the broader HTTP acceptance experiment remain a handoff.
