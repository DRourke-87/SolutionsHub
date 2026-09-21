"""Editable form copy and content rules.

The sensitive-data wording is held here rather than in the templates so it can be replaced in one
place once Corporate Security (Karyn Dukette) confirms the final language.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- sensitive data
SENSITIVE_DATA_SHORT = (
    "Do not enter or upload sensitive information. This system is not approved for export-controlled "
    "or otherwise protected data."
)

SENSITIVE_DATA_DISCLAIMER = (
    "SolutionsHub is an unclassified marketing intake tool. Do not enter, paste or upload any "
    "export-controlled (ITAR / EAR), Controlled Unclassified Information (CUI), Federal Contract "
    "Information (FCI), classified, proprietary customer, personal or otherwise sensitive data in this "
    "form or its attachments. Describe your solution at a level suitable for public marketing material."
)

SENSITIVE_DATA_ATTACHMENT_NOTE = (
    "Check every file before you attach it: no ITAR / EAR, CUI, FCI, classified, customer-proprietary "
    "or personal data, and no markings or headers that carry them."
)

SENSITIVE_DATA_CONFIRMATION = (
    "I confirm that this submission and any attached files contain no ITAR / EAR, CUI, FCI, classified, "
    "customer-proprietary, personal or otherwise sensitive information."
)

# --------------------------------------------------------------------------- word limits
WORD_LIMITS = {
    "customer_challenge": 300,
    "technical_description": 300,
    "key_differentiators": 200,
}

_WORD_RE = re.compile(r"[^\s]+")


def count_words(text: str | None) -> int:
    return len(_WORD_RE.findall(text or ""))


def word_limit_errors(values: dict[str, str | None], labels: dict[str, str]) -> list[str]:
    """One error per field that runs over its limit."""
    errors: list[str] = []
    for field, limit in WORD_LIMITS.items():
        n = count_words(values.get(field))
        if n > limit:
            errors.append(f"{labels.get(field, field)} is {n} words; please keep it to {limit} words or fewer.")
    return errors
