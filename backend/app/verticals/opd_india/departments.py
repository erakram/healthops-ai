"""OPD departments and the keywords that point to them.

Keywords cover English, common Hinglish spellings and Devanagari. They are the fallback when
the Intake Agent is unavailable, and a second opinion when it isn't: the evals compare both.
"""

import re

from app.verticals.opd_india.text import normalize

DEPARTMENTS = {
    "general_medicine": "General Medicine",
    "paediatrics": "Paediatrics",
    "orthopaedics": "Orthopaedics",
    "ent": "ENT",
    "dermatology": "Dermatology",
    "gynaecology": "Obstetrics & Gynaecology",
    "cardiology": "Cardiology",
    "ophthalmology": "Ophthalmology",
}

DEPARTMENTS_HI = {
    "general_medicine": "सामान्य चिकित्सा (मेडिसिन)",
    "paediatrics": "बाल रोग",
    "orthopaedics": "हड्डी रोग",
    "ent": "नाक, कान, गला",
    "dermatology": "त्वचा रोग",
    "gynaecology": "स्त्री एवं प्रसूति रोग",
    "cardiology": "हृदय रोग",
    "ophthalmology": "नेत्र रोग",
}

# Regexes over normalized text (lowercase, no punctuation). \b doesn't work for Devanagari, so those are plain substrings.
KEYWORDS: dict[str, list[str]] = {
    "general_medicine": [
        r"\bfever\b", r"\bbukh?aa?r\b", "बुखार", r"\bchest\b", r"\bcough\b", r"\bkhaa?nsi\b", "खांसी", "खाँसी", r"\bcold\b", r"\bzukaa?m\b",
        r"\bweakness\b", r"\bkamzori\b", "कमजोरी", r"\bdiabet", r"\bsugar\b", r"\bbp\b", r"\bblood pressure\b", r"\bvomit",
        r"\bult(i|ee)\b", "उल्टी", r"\bloose motions?\b", r"\bdiarrh", r"\bdast\b", r"\bstomach\b", r"\bpet\b", "पेट",
        r"\bacidity\b", r"\bgas\b", r"\bheadache\b", r"\bsa?i?r ?dardh?\b", "सिर दर्द", r"\bdizz", r"\bchakk?ar\b", "चक्कर",
        r"\bthyroid\b", r"\bbody ?ache\b", r"\bbadan dard\b", r"\bjaundice\b", r"\bpeeliya\b", r"\burine\b", r"\bpeshab\b",
    ],
    "paediatrics": [r"\bchild\b", r"\bbaby\b", r"\binfant\b", r"\bbach?ch?a\b", "बच्चा", "बच्चे", r"\bvaccin", r"\bteeka\b",
                    r"\bnewborn\b"],
    "orthopaedics": [
        r"\bknee", r"\bghutn", "घुटन", "घुटने", r"\bback ?pain\b", r"\bkamar\b", "कमर", r"\bjoints?\b",
        r"\bjodo?n?\b", r"\bfracture\b", r"\bhaddi\b", "हड्डी", r"\bsprain", r"\bmoch\b", "मोच", r"\bshoulder\b",
        r"\bkandha\b", r"\bneck pain\b", r"\bgardan (mein|main|mei|mai|me|m) dard\b", r"\bankle\b", r"\bwrist\b", r"\bgir (gaya|gayi|gaye)\b",
        r"\bfell\b", r"\bslipped\b", r"\bspine\b", r"\bhip\b",
    ],
    "ent": [r"\bears?\b", r"\bkaa?n\b", "कान", r"\bnose\b", r"\bnaa?k\b", "नाक", r"\bthroat\b", r"\bgala\b", r"\bgale\b",
            "गला", "गले", r"\btonsil", r"\bsinus", r"\bhearing\b", r"\bsunai\b", r"\bnakseer\b", r"\bvoice\b",
            r"\bawaaz\b", r"\bswallow"],
    "dermatology": [r"\bskin\b", r"\btwacha\b", "त्वचा", r"\brash", r"\bdaane\b", "दाने", r"\bitch", r"\bkhujli\b", "खुजली",
                    r"\bacne\b", r"\bpimples?\b", r"\bmuhaa?se\b", r"\bhair ?fall\b", r"\bbaal\b", r"\bfungal\b",
                    r"\bdaa?d\b", r"\bringworm\b", r"\beczema\b", r"\bpsoriasis\b", r"\bdandruff\b"],
    "gynaecology": [r"\bperiods?\b", r"\bmaa?hwari\b", r"\bmenstru", r"\bpregnan", r"\bgarbh", "गर्भ", "पीरियड",
                    r"\bwhite discharge\b", r"\bsafed paani\b", r"\bpcos\b", r"\bpcod\b", r"\bmissed period\b",
                    r"\bdelivery\b", r"\bantenatal\b", r"\bpet se (hoon|hu|hai|hain|thi)\b", r"\bumeed se\b"],
    "cardiology": [r"\bpalpitation", r"\bdhadk?an\b", "धड़कन", "धडकन", r"\bheart\b", r"\bdil\b", "दिल", r"\bstent\b",
                   r"\bangioplasty\b", r"\bbypass\b", r"\becg\b", r"\bcholesterol\b"],
    "ophthalmology": [r"\beyes?\b", r"\baa?nkh", "आंख", "आँख", r"\bvision\b", r"\bnazar\b", r"\bdhundh?la\b",
                      r"\bblurr", r"\bspecs\b", r"\bchashm", r"\bspectacles\b", r"\bcataract\b", r"\bmotiyabind\b",
                      r"\bwatering\b"],
}
_COMPILED = {dept: [re.compile(p) for p in patterns] for dept, patterns in KEYWORDS.items()}

PAEDIATRIC_AGE = 14  # under this, children are seen in Paediatrics...
CHILD_SPECIALTIES = {"ophthalmology", "ent", "orthopaedics"}  # ...unless it's clearly an eye, ENT or bone problem


def keyword_scores(*texts: str) -> dict[str, int]:
    text = " ".join(normalize(t) for t in texts if t)
    scores = {}
    for dept, patterns in _COMPILED.items():
        hits = sum(1 for p in patterns if p.search(text))
        if hits:
            scores[dept] = hits
    return scores


def keyword_department(*texts: str) -> str | None:
    """The department with the most keyword hits; specialties win ties over General Medicine."""
    scores = keyword_scores(*texts)
    scores.pop("paediatrics", None)  # age decides that, not words like "baby"
    if not scores:
        return None
    return max(scores, key=lambda d: (scores[d], d != "general_medicine"))


def department_by_rule(age: float | None, sex: str | None, chosen: str | None, scores: dict[str, int]) -> tuple[str, str] | None:
    """Rules that override the choice: children go to Paediatrics, pregnancy to Gynaecology."""
    if age is not None and age < PAEDIATRIC_AGE and chosen not in CHILD_SPECIALTIES:
        return "paediatrics", f"Patients under {PAEDIATRIC_AGE} are seen in Paediatrics."
    if sex == "F" and age is not None and age >= PAEDIATRIC_AGE and scores.get("gynaecology") and chosen not in ("gynaecology", "ophthalmology", "ent", "dermatology", "orthopaedics"):
        return "gynaecology", "Pregnancy and period problems go to Obstetrics & Gynaecology."
    return None
