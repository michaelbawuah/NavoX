"""Conservative sensitive-topic guardrails, not a complete classifier."""

import re

SENSITIVE = re.compile(
    r"\b(health|hospital|diagnos\w*|medication|therapy|therapist|depress\w*|anxiety|mental|"
    r"trauma\w*|suicid\w*|self.harm|family|parents?|mother|father|sister|brother|abuse|"
    r"pregnan\w*|disabil\w*|cancer|injur\w*|religio\w*|sexual\w*|adderall|ritalin|"
    r"sertraline|prozac|antidepress\w*|bipolar|adhd|autis\w*)\b",
    re.IGNORECASE,
)
