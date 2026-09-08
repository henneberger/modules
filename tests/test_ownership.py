from concurrent.futures import ThreadPoolExecutor
from copy import copy, deepcopy
from threading import Barrier

import pytest

from module_families.ownership import (
    Owned,
    OwnershipError,
    drop_owned,
    invoke,
    move_owned,
)


def spec(usage="linear", mode="move", representation="opaque", identity="resource"):
    return dict(id=identity, usage=usage, mode=mode, representation=representation)


def test_move_invalidates_and_preserves_nominal_owner():
    old = Owned(object(), "resource")
    new = move_owned(old)
    assert not old.valid and new.valid
    with pytest.raises(OwnershipError, match="already been moved"):
        move_owned(old)
    assert new.type_id == "resource"
    assert new.usage == "linear"
    for operation in (copy, deepcopy):
        with pytest.raises(OwnershipError):
            operation(new)


def test_preflight_checks_every_argument_before_consuming():
    value = Owned(object(), "resource")
    wrong = Owned(object(), "wrong")
    with pytest.raises(OwnershipError, match="nominal"):
        invoke(lambda *_: None, [value, wrong], [spec(), spec()], [])
    assert value.valid and wrong.valid
    with pytest.raises(OwnershipError, match="require Owned"):
        invoke(lambda _: None, [object()], [spec()], [])


def test_exception_consumes_moves_but_releases_borrows():
    moved = Owned(object(), "resource")
    borrowed = Owned(object(), "resource")

    def fail(*_):
        assert not moved.valid
        raise RuntimeError("adapter failed")

    with pytest.raises(RuntimeError):
        invoke(fail, [moved, borrowed], [spec(), spec(mode="borrow")], [])
    assert not moved.valid and borrowed.valid
    assert move_owned(borrowed).valid


def test_borrows_allow_repetition_and_prevent_reentrant_move():
    owner = Owned(object(), "resource")

    def use(left, right):
        assert left is right
        with pytest.raises(OwnershipError, match="borrowed"):
            move_owned(owner)
        with pytest.raises(OwnershipError, match="borrowed"):
            invoke(lambda _: None, [owner], [spec()], [])
        return 7

    assert invoke(use, [owner, owner], [spec(mode="borrow")] * 2,
                  [spec("shared", "share", "int", "count")]) == (7,)
    assert owner.valid


@pytest.mark.parametrize("modes", [("move", "move"), ("move", "borrow"), ("borrow", "move")])
def test_alias_conflicts_are_atomic(modes):
    owner = Owned(object(), "resource")
    with pytest.raises(OwnershipError, match="Aliased"):
        invoke(lambda *_: None, [owner, owner], [spec(mode=m) for m in modes], [])
    assert owner.valid


@pytest.mark.parametrize("representation,value", [("int", True), ("float", 1), ("bool", 1), ("str", b"a"), ("bytes", "a")])
def test_shared_primitives_use_strict_representations(representation, value):
    s = spec("shared", "share", representation)
    with pytest.raises(OwnershipError, match="representation"):
        invoke(lambda a: a, [value], [s], [s])


def test_return_validation_precedes_fresh_ownership():
    owner = Owned(object(), "resource")
    with pytest.raises(OwnershipError, match="representation"):
        invoke(lambda x: (x, True), [owner], [spec()],
               [spec(), spec("shared", "share", "int")])
    assert not owner.valid


def test_adapter_can_return_moved_resource_as_new_owner():
    owner = Owned(object(), "resource")
    (new,) = invoke(lambda x: x, [owner], [spec()], [spec()])
    assert not owner.valid and new.valid
    with pytest.raises(OwnershipError, match="already been moved"):
        invoke(lambda _: None, [owner], [spec()], [])


@pytest.mark.parametrize("wrapper", [lambda x: x, lambda x: [x], lambda x: {"nested": [x]}])
def test_borrowed_values_cannot_escape_in_results(wrapper):
    owner = Owned(object(), "resource")
    with pytest.raises(OwnershipError, match="Borrowed"):
        invoke(wrapper, [owner], [spec(mode="borrow")], [spec()])
    assert owner.valid
    assert move_owned(owner).valid


def test_adapter_must_return_raw_values():
    owner = Owned(object(), "resource")
    with pytest.raises(OwnershipError, match="Owned"):
        invoke(lambda: owner, [], [], [spec()])
    with pytest.raises(OwnershipError, match="Owned"):
        invoke(lambda: [owner], [], [], [spec()])
    assert owner.valid


def test_return_arity():
    shared = spec("shared", "share")
    assert invoke(lambda: None, [], [], []) == ()
    with pytest.raises(OwnershipError, match="zero-return"):
        invoke(lambda: (), [], [], [])
    assert invoke(lambda: (1, 2), [], [], [shared]) == ((1, 2),)
    assert invoke(lambda: (1, 2), [], [], [shared, shared]) == (1, 2)
    for result in ([1, 2], (1,), (1, 2, 3)):
        with pytest.raises(OwnershipError, match="exact-arity"):
            invoke(lambda result=result: result, [], [], [shared, shared])


def test_concurrent_consumption_has_exactly_one_winner():
    owner = Owned(object(), "resource")
    barrier = Barrier(2)
    calls = []

    def compete():
        barrier.wait(timeout=5)
        try:
            invoke(lambda value: calls.append(value), [owner], [spec()], [])
        except OwnershipError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(compete) for _ in range(2)]
        outcomes = [future.result(timeout=5) for future in futures]
    assert sorted(outcomes) == [False, True]
    assert len(calls) == 1


def test_affine_drop_and_borrow_guard():
    linear = Owned(object(), "resource")
    with pytest.raises(OwnershipError, match="Only affine"):
        drop_owned(linear)
    owner = Owned(object(), "resource", "affine")

    def borrowed(_):
        with pytest.raises(OwnershipError, match="borrowed"):
            drop_owned(owner)

    invoke(borrowed, [owner], [spec("affine", "borrow")], [])
    drop_owned(owner)
    assert not owner.valid and linear.valid
    with pytest.raises(OwnershipError):
        drop_owned(owner)


def test_return_cannot_duplicate_owner_or_share_owned_output():
    raw = object()
    for returns in ([spec(), spec()], [spec(), spec("shared", "share")]):
        with pytest.raises(OwnershipError, match="duplicate"):
            invoke(lambda: (raw, raw), [], [], returns)


def test_contract_validation_does_not_consume():
    owner = Owned(3, "resource")
    for parameter in (
        spec(mode="share"), spec(representation="list"),
        spec(usage="affine"), spec(representation="str"),
    ):
        with pytest.raises(OwnershipError):
            invoke(lambda _: None, [owner], [parameter], [])
        assert owner.valid
    with pytest.raises(OwnershipError, match="arity"):
        invoke(lambda: None, [], [spec()], [])


def test_other_thread_cannot_consume_during_borrow():
    from threading import Event

    owner = Owned(object(), "resource")
    entered, finish, attempted = Event(), Event(), Event()

    def borrow(_):
        entered.set()
        assert finish.wait(timeout=5)
        assert owner.valid

    def consume():
        attempted.set()
        return move_owned(owner)

    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(invoke, borrow, [owner], [spec(mode="borrow")], [])
        assert entered.wait(timeout=5)
        writer = pool.submit(consume)
        assert attempted.wait(timeout=5)
        assert not writer.done()
        finish.set()
        assert reader.result(timeout=5) == ()
        assert writer.result(timeout=5).valid
    assert not owner.valid


def test_nested_checked_function_preserves_ownership_and_signature():
    import inspect

    from module_families.ownership import mark_checked

    resource = spec()

    def inner(value):
        (local,) = invoke(lambda raw: raw, [value], [resource], [resource])
        return move_owned(local)

    signature = inspect.signature(inner)
    assert mark_checked(inner, [resource], [resource]) is inner
    assert inspect.signature(inner) == signature

    def outer(value):
        (local,) = invoke(lambda raw: raw, [value], [resource], [resource])
        (result,) = invoke(inner, [local], [resource], [resource])
        assert not local.valid
        return move_owned(result)

    mark_checked(outer, [resource], [resource])
    owner = Owned(object(), "resource")
    (result,) = invoke(outer, [owner], [dict(resource, name="input")], [resource])
    assert not owner.valid and result.valid


def test_checked_marker_mismatch_rejected_before_invocation():
    from module_families.ownership import mark_checked

    calls = []
    function = mark_checked(lambda x: calls.append(x), [spec()], [])
    owner = Owned(object(), "resource")
    with pytest.raises(OwnershipError, match="signature"):
        invoke(function, [owner], [spec(identity="other")], [])
    assert not calls and owner.valid
    with pytest.raises(OwnershipError, match="borrow"):
        mark_checked(lambda x: None, [spec(mode="borrow")], [])


def test_checked_shared_returns_and_single_opaque_tuple():
    from module_families.ownership import mark_checked

    shared = spec("shared", "share")
    function = mark_checked(lambda: (1, 2), [], [shared])
    assert invoke(function, [], [], [shared]) == ((1, 2),)
    integer = spec("shared", "share", "int")
    function = mark_checked(lambda: True, [], [integer])
    with pytest.raises(OwnershipError, match="representation"):
        invoke(function, [], [], [integer])


def test_checked_resource_result_validation():
    from module_families.ownership import mark_checked

    stale = Owned(object(), "resource")
    move_owned(stale)
    invalid = [stale, Owned(object(), "other"), Owned(object(), "resource", "affine"), object()]
    for result in invalid:
        function = mark_checked(lambda result=result: result, [], [spec()])
        with pytest.raises(OwnershipError):
            invoke(function, [], [], [spec()])
    result = Owned(True, "resource")
    integer = spec(representation="int")
    function = mark_checked(lambda: result, [], [integer])
    with pytest.raises(OwnershipError, match="representation"):
        invoke(function, [], [], [integer])
    function = mark_checked(lambda: (result, result), [], [spec(), spec()])
    with pytest.raises(OwnershipError, match="duplicate"):
        invoke(function, [], [], [spec(), spec()])
