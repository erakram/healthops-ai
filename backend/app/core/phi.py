"""Mask patient identifiers before text goes to an LLM, and restore them in the answer.

Every agent call goes through MaskingLLM (see llm.make_llm). Before sending, identifiers
are replaced with stable tokens ([PERSON_1], [MEMBER_ID_1], [DOB_1], ...); the model reads
and quotes the tokens; afterwards the tokens in its JSON answer are swapped back, so field
values, quotes and evidence boxes work as before. The provider never sees the identifiers.

What's masked: patient and member names (found next to their labels, then every occurrence,
including LAST, FIRST forms), member/subscriber IDs, dates of birth, phone numbers, SSNs and
email addresses. What isn't: dates of service, procedure and diagnosis codes, amounts and
provider names, which the agents need to reason about. That's a deliberate scope, not full
HIPAA Safe Harbor de-identification (which would also remove service dates and locations).
"""

import re
from dataclasses import dataclass, field

from app.core.llm import LLM, LLMResult

_NAME = r"[A-Z][A-Za-z'.-]+(?:,? [A-Z][A-Za-z'.-]+){1,3}"
_NAME_LABELS = re.compile(
    rf"(?:Patient Name|PATIENT'S NAME[^\n]*|Patient|Member Name|Member|Insured|INSURED'S NAME[^\n]*)\s*[:\n]\s*({_NAME})")
_DOB = re.compile(r"(?:DOB|Date of Birth|BIRTH DATE[^\n]*|Birth Date)\s*[:\n]\s*(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})",
                  re.IGNORECASE)
_MEMBER_ID = re.compile(r"\b[A-Z]{3}\d{9}\b")
_MEMBER_LABEL = re.compile(r"(?:Member ID|INSURED'S ID NUMBER|Subscriber ID)\s*[:\n]\s*([A-Z0-9-]{6,20})", re.IGNORECASE)
_PHONE = re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
# Structured payloads (JSON facts sent to the appeal, request and A/R agents): identifiers by key.
_JSON_NAME = re.compile(r'"(?:patient|patient_name|member_name|insured|insured_name|patient_full_name)"\s*:\s*"([^"]+)"')
_JSON_ID = re.compile(r'"(?:member_id|subscriber_id|insured_id)"\s*:\s*"([^"]+)"')
_JSON_DOB = re.compile(r'"(?:patient_dob|dob|date_of_birth|birth_date)"\s*:\s*"([^"]+)"')
_TOKEN = re.compile(r"\[?\b(PERSON|MEMBER_ID|DOB|PHONE|SSN|EMAIL)_(\d+)\b\]?")
_STOP = {"Date", "Of", "Birth", "Member", "Patient", "ID", "Name", "Insurance", "Group", "Visit", "Attending"}


@dataclass
class Masker:
    values: dict[str, str] = field(default_factory=dict)  # original -> token
    counts: dict[str, int] = field(default_factory=dict)

    def _token(self, kind: str, value: str) -> str:
        value = value.strip()
        if value not in self.values:
            self.counts[kind] = self.counts.get(kind, 0) + 1
            self.values[value] = f"[{kind}_{self.counts[kind]}]"
        return self.values[value]

    def _names(self, text: str) -> None:
        found = [m.group(1).strip() for m in _NAME_LABELS.finditer(text)]
        found += [m.group(1).strip() for m in _JSON_NAME.finditer(text)]
        for name in found:
            words = [w.strip(",") for w in name.split()]
            if len(words) < 2 or any(w in _STOP for w in words):
                continue
            token = self._token("PERSON", name)
            if "," in name:  # LAST, FIRST -> also mask First Last and the other casings
                last, _, first = name.partition(",")
                variants = [f"{first.strip()} {last.strip()}"]
            else:
                variants = [f"{words[-1]}, {' '.join(words[:-1])}"]
            for v in variants + [name.upper()] + [v.upper() for v in variants] + [name.title()] + [v.title() for v in variants]:
                self.values.setdefault(v, token)

    def mask(self, text: str) -> str:
        self._names(text)
        for match in _DOB.finditer(text):
            self._token("DOB", match.group(1))
        for match in _JSON_DOB.finditer(text):
            self._token("DOB", match.group(1))
        for match in _JSON_ID.finditer(text):
            self._token("MEMBER_ID", match.group(1))
        for pattern, kind in ((_MEMBER_LABEL, "MEMBER_ID"),):
            for match in pattern.finditer(text):
                self._token(kind, match.group(1))
        for pattern, kind in ((_MEMBER_ID, "MEMBER_ID"), (_SSN, "SSN"), (_PHONE, "PHONE"), (_EMAIL, "EMAIL")):
            for match in pattern.finditer(text):
                self._token(kind, match.group(0))
        for value in sorted(self.values, key=len, reverse=True):  # longest first: "Ann Lee" before "Lee"
            text = text.replace(value, self.values[value])
        return text

    def unmask(self, obj):
        if isinstance(obj, str):
            reverse = {}
            for original, token in self.values.items():
                reverse.setdefault(token, original)  # the first form seen is the one restored
            return _TOKEN.sub(lambda m: reverse.get(f"[{m.group(1)}_{m.group(2)}]", m.group(0)), obj)
        if isinstance(obj, list):
            return [self.unmask(x) for x in obj]
        if isinstance(obj, dict):
            return {k: self.unmask(v) for k, v in obj.items()}
        return obj


class MaskingLLM:
    """Wraps any LLM: identifiers never leave the process."""

    def __init__(self, inner: LLM):
        self.inner = inner
        self.masked_calls = 0
        self.masked_values = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def complete_json(self, system: str, user: str, model: str | None = None) -> LLMResult:
        masker = Masker()
        masked = masker.mask(user)
        self.masked_calls += 1
        self.masked_values += len(set(masker.values.values()))
        result = self.inner.complete_json(system, masked, model)
        return LLMResult(masker.unmask(result.data), result.model, result.input_tokens, result.output_tokens,
                         result.latency_ms)
