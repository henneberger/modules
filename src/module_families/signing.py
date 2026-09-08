"""Public evaluator identities using the cryptography Ed25519 implementation."""

from __future__ import annotations

from dataclasses import dataclass, field


def _ed25519():
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as error:
        raise ValueError(
            "install module-families[signing] for Ed25519 evidence"
        ) from error
    return ed25519


@dataclass(frozen=True)
class PublicEvaluatorKey:
    raw: bytes

    def __post_init__(self):
        if not isinstance(self.raw, bytes) or len(self.raw) != 32:
            raise ValueError("Ed25519 public key must contain 32 bytes")

    def verify(self, signature, message):
        ed25519 = _ed25519()
        from cryptography.exceptions import InvalidSignature

        try:
            ed25519.Ed25519PublicKey.from_public_bytes(self.raw).verify(
                signature, message
            )
        except (InvalidSignature, ValueError) as error:
            raise ValueError("evaluator signature mismatch") from error


@dataclass(frozen=True)
class PrivateEvaluatorKey:
    raw: bytes = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.raw, bytes) or len(self.raw) != 32:
            raise ValueError("Ed25519 private key must contain 32 bytes")

    @classmethod
    def generate(cls):
        return cls(_ed25519().Ed25519PrivateKey.generate().private_bytes_raw())

    def public_key(self):
        key = _ed25519().Ed25519PrivateKey.from_private_bytes(self.raw)
        return PublicEvaluatorKey(key.public_key().public_bytes_raw())

    def sign(self, message):
        return _ed25519().Ed25519PrivateKey.from_private_bytes(self.raw).sign(message)
