"""PII protection: HMAC-SHA256 of canonical phone, salt from env.

Design choice (documented in SUBMISSION.md): customer_name is PII and is
required by the schema, but it appears in NO report. We validate its presence
during ingestion and then discard it. That is stronger than encryption: the
at-rest risk is eliminated, not just mitigated.

The phone must be stored in a matchable form (events link by phone). We use
HMAC-SHA256 with a secret salt from the environment (never in the DB). Same
canonical phone -> same hash -> joins work. An attacker with a DB dump but
without PII_SALT cannot reverse the hashes even for a 10-digit phone space.
"""

import hashlib
import hmac
import os


def get_salt() -> bytes:
    salt = os.environ.get("PII_SALT")
    if not salt:
        raise RuntimeError(
            "PII_SALT environment variable must be set. "
            "Set it once per environment; never commit it."
        )
    return salt.encode()


def phone_hash(canonical_phone: str) -> str:
    return hmac.new(get_salt(), canonical_phone.encode(), hashlib.sha256).hexdigest()