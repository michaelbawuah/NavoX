import json
from hashlib import sha256


def canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return sha256(encoded).hexdigest()


def payload_hash(payload: dict[str, object]) -> str:
    return canonical_hash(payload)


def action_security_hash(
    *,
    provider: str,
    action_type: str,
    payload: dict[str, object],
) -> str:
    return canonical_hash(
        {
            "provider": provider,
            "action_type": action_type,
            "payload": payload,
        }
    )
