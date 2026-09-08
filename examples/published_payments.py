"""Build, publish, discover, lock, and compose an independent payment family.

Run from the repository root: PYTHONPATH=src python examples/published_payments.py
Pass --work-dir PATH to retain the ordinary wheels, repository, and locked files.
Only verified generated artifacts are imported; payments_demo is never imported.
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from module_families.compiler import build_family
from module_families.contracts import ContractError, Functor, Requirement, Signature
from module_families.registry import Registry
from module_families.runtime import atomic_import, load


def charge_contract(amount, *, idempotency_key):
    raise NotImplementedError("contract prototype")


def refund_contract(receipt):
    raise NotImplementedError("contract prototype")


def count_contract():
    raise NotImplementedError("contract prototype")


def record_contract(receipt):
    raise NotImplementedError("contract prototype")


def composed_service(repository, work, *, ledger_outside=False):
    """Import and link a type library and behavior-enriching module factories."""
    selected = {
        "types_library": "types.library",
        "raw": "services.flaky",
        "ledger_store": "ledgers.memory",
        "retry": "wrappers.retry",
        "ledger": "wrappers.ledger",
        "idempotency": "wrappers.idempotency",
    }
    locked = {
        alias: repository.lock("payments", "fixture." + name, "0.1.0")
        for alias, name in selected.items()
    }

    def link(exports):
        type_names = ("Money", "Receipt", "PaymentError", "TransientPaymentError")
        library_signature = Signature("payments.types", "1", types=type_names)
        library = library_signature.seal(exports["types_library"]())
        operations = {
            "charge": charge_contract,
            "effect_count": count_contract,
            "attempt_count": count_contract,
        }
        service_signature = Signature(
            "payments.charge-service", "1", operations, type_names
        )
        ledger_signature = Signature(
            "payments.ledger",
            "1",
            {"record": record_contract, "entries": count_contract},
            type_names,
        )
        service_requirement = Requirement(service_signature, types=dict(library))
        ledger_requirement = Requirement(ledger_signature, types=dict(library))

        def provider(alias, signature):
            card = locked[alias]["member"]
            return signature.seal(
                exports[alias](),
                provides=card["provides"],
                identity=f"artifact:{card['sha256']}",
            )

        def wrapper(alias, parameters, sharing=()):
            return Functor(
                f"payments.{alias}@{locked[alias]['member']['sha256']}",
                parameters,
                service_signature,
                exports[alias],
                sharing=sharing,
            )

        raw = provider("raw", service_signature)
        ledger_store = provider("ledger_store", ledger_signature)
        retry = wrapper("retry", {"processor": service_requirement})(processor=raw)
        shared_types = tuple(
            (f"processor.{name}", f"ledger.{name}") for name in type_names
        )
        add_ledger = wrapper(
            "ledger",
            {"processor": service_requirement, "ledger": ledger_requirement},
            shared_types,
        )
        add_idempotency = wrapper("idempotency", {"processor": service_requirement})
        if ledger_outside:
            service = add_ledger(
                processor=add_idempotency(processor=retry), ledger=ledger_store
            )
        else:
            service = add_idempotency(
                processor=add_ledger(processor=retry, ledger=ledger_store)
            )
        audited_signature = Signature(
            "payments.audited-charge-service",
            "1",
            {**operations, "entries": count_contract},
            type_names,
        )
        expose_audit = Functor(
            "payments.expose-audit@1",
            {"processor": service_requirement, "ledger": ledger_requirement},
            audited_signature,
            lambda processor, ledger: {**dict(processor), "entries": ledger.entries},
            sharing=shared_types,
        )
        return expose_audit(processor=service, ledger=ledger_store)

    bundle = atomic_import(repository, locked, work / "atomic-environments", link=link)
    service = bundle.value
    amount = service.Money(2500, "EUR")
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(
            pool.map(
                lambda _: service.charge(amount, idempotency_key="concurrent-order"),
                range(20),
            )
        )
    assert all(receipt is receipts[0] for receipt in receipts)
    assert (
        service.attempt_count() == 2
    )  # One pre-effect transient failure, then success.
    assert service.effect_count() == 1
    expected_entries = len(receipts) if ledger_outside else 1
    assert service.entries() == (receipts[0],) * expected_entries
    try:
        service.charge(service.Money(9999, "EUR"), idempotency_key="concurrent-order")
    except service.PaymentError:
        pass
    else:
        raise AssertionError("composition accepted inconsistent request-key reuse")
    recipe = (
        "ledger(idempotency(retry(P)), L)"
        if ledger_outside
        else "idempotency(ledger(retry(P), L))"
    )
    print(
        f"{recipe}: 20 concurrent calls, 2 attempts, 1 charge effect, {expected_entries} ledger entries"
    )
    return {
        "recipe": recipe,
        "bundle_identity": bundle.identity,
        "composition_identity": service.identity,
        "instance_id": service.instance_id,
        "module_factories": sorted(selected.values()),
        "calls": len(receipts),
        "attempts": service.attempt_count(),
        "charge_effects": service.effect_count(),
        "ledger_entries": len(service.entries()),
        "same_receipt_object": all(receipt is receipts[0] for receipt in receipts),
        "scope": "live process, fake provider whose transient failures occur before effects",
    }


def run(work: Path) -> dict:
    root = Path(__file__).resolve().parents[1]
    build = work / "wheels"
    index = build_family(root / "tests/fixtures/payments/family.toml", build)
    repository = Registry(work / "repository")
    publication = repository.publish(build / "index.json")
    candidates = repository.search("", family="payments", contract="payments.processor")
    assert {candidate["id"] for candidate in candidates} == {
        "fixture.processors.alpha",
        "fixture.processors.beta",
    }
    environment = work / "environment"
    locks = {}

    def member(identifier):
        lock = repository.lock("payments", "fixture." + identifier, "0.1.0")
        assert lock["external_requirements"] == []
        locks[identifier] = lock
        (work / "locks").mkdir(parents=True, exist_ok=True)
        (work / "locks" / f"{identifier}.json").write_text(
            json.dumps(lock, indent=2) + "\n"
        )
        return load(repository, lock, environment)

    Money = member("types.Money")
    Receipt = member("types.Receipt")
    processor_signature = Signature(
        "payments.processor", "1", {"charge": charge_contract}, ("Money", "Receipt")
    )
    checkout_signature = Signature(
        "payments.checkout", "1", {"charge": charge_contract}, ("Money", "Receipt")
    )
    checkout = Functor(
        "checkout.published@1",
        {
            "processor": Requirement(
                processor_signature, types={"Money": Money, "Receipt": Receipt}
            )
        },
        checkout_signature,
        lambda processor: dict(processor),
    )
    receipts = []
    providers = []
    for card in candidates:
        constructor = member(card["local_id"])
        provider = processor_signature.seal(
            constructor(),
            provides=card["provides"],
            identity=f"artifact:{card['sha256']}",
        )
        assert provider.Money is Money and provider.Receipt is Receipt
        application = checkout(processor=provider)
        amount = Money(1250, "USD")
        receipt = application.charge(amount, idempotency_key="order-42")
        assert application.charge(amount, idempotency_key="order-42") is receipt
        try:
            application.charge(Money(1300), idempotency_key="order-42")
        except ValueError:
            pass
        else:
            raise AssertionError("processor accepted inconsistent key reuse")
        receipts.append(receipt)
        providers.append(provider)
        print(
            f"Published {card['id']}: {receipt.processor} charged {receipt.amount.cents} minor units"
        )

    refund = member("refunds.full")
    refund_signature = Signature(
        "payments.refund", "1", {"refund": refund_contract}, ("Receipt",)
    )
    refund_view = refund_signature.seal(
        {"refund": refund, "Receipt": Receipt},
        provides=locks["refunds.full"]["member"]["provides"],
    )
    assert refund_view.refund(receipts[0]).amount == receipts[0].amount
    try:
        checkout(processor=refund_view)
    except ContractError as exc:
        print(f"Rejected another contract in the same family: {exc}")
    else:
        raise AssertionError("refund contract was accepted as a processor")

    composition = composed_service(repository, work)
    ledger_outside = composed_service(repository, work, ledger_outside=True)
    assert composition["bundle_identity"] == ledger_outside["bundle_identity"]
    assert composition["composition_identity"] != ledger_outside["composition_identity"]
    assert composition["instance_id"] != ledger_outside["instance_id"]
    assert not any(
        name == "payments_demo" or name.startswith("payments_demo.")
        for name in sys.modules
    )
    summary = {
        "family": publication["family"],
        "members": len(index["members"]),
        "artifacts": len(index["artifacts"]),
        "processor_candidates": len(candidates),
        "shared_money_identity": providers[0].Money is providers[1].Money is Money,
        "shared_receipt_identity": providers[0].Receipt
        is providers[1].Receipt
        is Receipt,
        "loaded_original_package": False,
        "external_requirements": [],
        "composition": composition,
        "composition_order": {
            "ledger_outside": ledger_outside,
            "same_bundle_identity": composition["bundle_identity"]
            == ledger_outside["bundle_identity"],
            "different_composition_identities": composition["composition_identity"]
            != ledger_outside["composition_identity"],
            "fresh_instances": composition["instance_id"]
            != ledger_outside["instance_id"],
        },
        "selected_member_closure_sizes": {
            name: len(lock["artifacts"]) for name, lock in locks.items()
        },
    }
    (work / "report.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    if args.work_dir is not None:
        args.work_dir.mkdir(parents=True, exist_ok=True)
        run(args.work_dir.resolve())
    else:
        with TemporaryDirectory(prefix="published-payments-") as temporary:
            run(Path(temporary))


if __name__ == "__main__":
    main()
