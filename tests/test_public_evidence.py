from __future__ import annotations

import copy
import hmac

import pytest
from test_evidence import TASK, attestation
from test_evidence import assembly as assembly

from module_families import evidence as e
from module_families.registry import canonical_bytes
from module_families.signing import PrivateEvaluatorKey

pytest.importorskip("cryptography")


def signed(assembly, private):
    record = attestation(assembly)
    record["payload"]["format"] = "module-families-ed25519-attestation-1"
    record["signature"] = private.sign(canonical_bytes(record["payload"])).hex()
    record["sha256"] = e._digest({k: v for k, v in record.items() if k != "sha256"})
    return record


def test_public_key_verifies_without_signing_authority(assembly, tmp_path):
    private = PrivateEvaluatorKey.generate()
    public = private.public_key()
    record = signed(assembly, private)
    assert not hasattr(public, "sign")
    store = e.EvidenceStore(tmp_path / "store.sqlite")
    store.add(record, {"ci": public})
    assert store.match(assembly, {"tasks": [TASK]}, {"ci": public})["accepted"]
    with pytest.raises(e.EvidenceError, match="signature"):
        e.verify_attestation(
            record, {"ci": PrivateEvaluatorKey.generate().public_key()}
        )
    changed = copy.deepcopy(record)
    changed["payload"]["task_sha256"] = "9" * 64
    changed["sha256"] = e._digest({k: v for k, v in changed.items() if k != "sha256"})
    with pytest.raises(e.EvidenceError, match="signature"):
        e.verify_attestation(changed, {"ci": public})


def test_public_key_cannot_be_used_as_hmac_secret(assembly):
    private = PrivateEvaluatorKey.generate()
    record = attestation(assembly)
    record["signature"] = hmac.new(
        private.public_key().raw, canonical_bytes(record["payload"]), "sha256"
    ).hexdigest()
    record["sha256"] = e._digest({k: v for k, v in record.items() if k != "sha256"})
    with pytest.raises(e.EvidenceError, match="signature"):
        e.verify_attestation(record, {"ci": private.public_key()})
    with pytest.raises(e.EvidenceError, match="explicit public key"):
        e.verify_attestation(
            signed(assembly, private), {"ci": private.public_key().raw}
        )


@pytest.mark.parametrize(
    "raw", [b"", b"x" * 31, b"x" * 33, "x" * 32, bytearray(b"x" * 32)]
)
def test_key_constructors_reject_wrong_type_or_length(raw):
    from module_families.signing import PublicEvaluatorKey

    for cls in [PrivateEvaluatorKey, PublicEvaluatorKey]:
        with pytest.raises(ValueError, match="32 bytes"):
            cls(raw)


def test_private_key_repr_does_not_expose_seed():
    private = PrivateEvaluatorKey(b"a" * 32)
    assert private.raw.hex() not in repr(private)
    assert repr(private.raw) not in repr(private)


@pytest.mark.parametrize(
    "signature", ["", "0" * 127, "0" * 129, "g" * 128, "AB" * 64, None]
)
def test_malformed_public_signatures_are_rejected_before_crypto(assembly, signature):
    private = PrivateEvaluatorKey.generate()
    record = signed(assembly, private)
    record["signature"] = signature
    record["sha256"] = e._digest({k: v for k, v in record.items() if k != "sha256"})
    with pytest.raises(e.EvidenceError, match="signature"):
        e.verify_attestation(record, {"ci": private.public_key()})


@pytest.mark.parametrize(
    "format",
    [
        "module-families-evaluator-attestation-1",
        "module-families-ed25519-attestation-2",
        "ed25519",
    ],
)
def test_signature_protocol_cannot_be_relabelled(assembly, format):
    private = PrivateEvaluatorKey.generate()
    record = signed(assembly, private)
    record["payload"]["format"] = format
    record["sha256"] = e._digest({k: v for k, v in record.items() if k != "sha256"})
    with pytest.raises(e.EvidenceError):
        e.verify_attestation(record, {"ci": private.public_key()})


def test_evaluator_identity_is_signed(assembly):
    private = PrivateEvaluatorKey.generate()
    record = signed(assembly, private)
    record["payload"]["evaluator"] = "another"
    record["sha256"] = e._digest({k: v for k, v in record.items() if k != "sha256"})
    with pytest.raises(e.EvidenceError, match="signature"):
        e.verify_attestation(record, {"another": private.public_key()})


def test_cli_public_key_parser_preserves_protocol(monkeypatch, assembly):
    import json

    from module_families.cli import trust_keys
    from module_families.signing import PublicEvaluatorKey

    private = PrivateEvaluatorKey.generate()
    monkeypatch.setenv(
        "TEST_EVIDENCE_KEYS",
        json.dumps({"ci": {"ed25519": private.public_key().raw.hex()}}),
    )
    keys = trust_keys("TEST_EVIDENCE_KEYS")
    assert isinstance(keys["ci"], PublicEvaluatorKey)
    e.verify_attestation(signed(assembly, private), keys)
    for bad in [
        {"ci": {"ed25519": "not-hex"}},
        {"ci": {"ed25519": "ab" * 31}},
        {"ci": {"ed25519": 23}},
        {"ci": {"ed25519": "ab" * 32, "hmac": "secret"}},
    ]:
        monkeypatch.setenv("TEST_EVIDENCE_KEYS", json.dumps(bad))
        with pytest.raises(ValueError):
            trust_keys("TEST_EVIDENCE_KEYS")


def test_missing_crypto_extra_has_actionable_diagnostic(monkeypatch):
    import builtins

    from module_families.signing import PublicEvaluatorKey

    original = builtins.__import__

    def without_crypto(name, *args, **kwargs):
        if name.startswith("cryptography"):
            raise ImportError("optional dependency unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_crypto)
    with pytest.raises(ValueError, match=r"module-families\[signing\]"):
        PublicEvaluatorKey(b"x" * 32).verify(b"x" * 64, b"message")
