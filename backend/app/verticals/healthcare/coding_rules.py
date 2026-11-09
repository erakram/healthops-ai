"""Coding rules: the office visit (E/M) level and the checks a coder would run.

The E/M level follows the 2021 AMA office visit guidelines: the level of medical decision
making (MDM) is set by two of its three elements (problems, data, risk), and time on the
date of the encounter can be used instead; the higher of the two is billed. The model only
reports each element with a quote from the note; this table decides the code, so the level
is explainable and the model can't upcode.
"""

import re
from dataclasses import asdict, dataclass

from app.verticals.healthcare.codesets import (CPT_CODES, EM_BUNDLED_WITH, ESTABLISHED, NEW_PATIENT, TIME_ESTABLISHED,
                                               TIME_NEW, completions, dotted, icd10, siblings)

RANK = {"minimal": 1, "straightforward": 1, "low": 2, "moderate": 3, "high": 4}
LEVEL_NAME = {1: "straightforward", 2: "low", 3: "moderate", 4: "high"}


def em_level(patient_status: str, mdm: dict, time_minutes: int | None) -> dict:
    """mdm: {"problems": {"level": ...}, "data": {...}, "risk": {...}}"""
    table = NEW_PATIENT if patient_status == "new" else ESTABLISHED
    ranks = sorted((RANK.get(str((mdm.get(k) or {}).get("level") or "minimal").lower(), 1)
                    for k in ("problems", "data", "risk")), reverse=True)
    mdm_rank = ranks[1]  # two of the three elements must meet or exceed the level
    time_rank = 0
    if time_minutes:
        for minimum, rank in (TIME_NEW if patient_status == "new" else TIME_ESTABLISHED):
            if time_minutes >= minimum:
                time_rank = rank
                break
    by_time = time_rank > mdm_rank
    rank = max(mdm_rank, time_rank)
    return {"code": table[rank], "patient_status": "new" if patient_status == "new" else "established",
            "mdm_level": LEVEL_NAME[mdm_rank], "time_minutes": time_minutes,
            "time_code": table[time_rank] if time_rank else None, "basis": "time" if by_time else "MDM",
            "explanation": (f"Time: {time_minutes} minutes supports {table[time_rank]}, higher than the "
                            f"{LEVEL_NAME[mdm_rank]} MDM level ({table[mdm_rank]})." if by_time else
                            f"MDM is {LEVEL_NAME[mdm_rank]}: at least two of problems, data and risk meet it"
                            + (f"; time ({time_minutes} min) doesn't support a higher level." if time_minutes else "."))}


@dataclass
class Edit:
    code: str
    severity: str  # high | medium | low
    target: str  # the code it's about
    title: str
    detail: str
    fix: dict | None = None  # {"replace": code} | {"add_modifier": "25"} | {"remove": code}


_SIDES = {"right": "right", "left": "left", "bilateral": "bilateral"}


def _sides_in(text: str) -> set[str]:
    return {side for side in _SIDES if re.search(rf"\b{side}\b", text, re.IGNORECASE)}


def coding_edits(icd_codes: list[str], cpt_lines: list[dict], note_text: str, separate_em_documented: bool) -> list[dict]:
    """Checks on a set of codes for one encounter. cpt_lines: [{code, modifiers, units}]"""
    edits: list[Edit] = []
    codes = icd10()
    note_sides = _sides_in(note_text)

    if not icd_codes:
        edits.append(Edit("NO-DIAGNOSIS", "high", "", "No diagnosis code", "Every claim needs at least one ICD-10-CM code."))
    for raw in dict.fromkeys(icd_codes):
        code = dotted(raw)
        if code not in codes:
            more = completions(code)
            if more:
                edits.append(Edit("ICD-INCOMPLETE", "high", code, f"{code} isn't billable: it needs more characters",
                                  "Billable codes under it: " + "; ".join(f"{c} {d}" for c, d in more[:4]),
                                  {"replace": more[0][0]}))
            else:
                edits.append(Edit("ICD-INVALID", "high", code, f"{code} isn't an ICD-10-CM code",
                                  "It isn't in the FY2026 code set."))
            continue
        description = codes[code]
        code_sides = _sides_in(description)
        if code_sides and note_sides and not code_sides & note_sides:
            other = next((c for c, d in siblings(code) if _sides_in(d) & note_sides), None)
            edits.append(Edit("LATERALITY-MISMATCH", "high", code, f"{code} says {', '.join(sorted(code_sides))}; "
                              f"the note says {', '.join(sorted(note_sides))}", f"{code}: {description}.",
                              {"replace": other} if other else None))
        if "unspecified" in description.lower():
            better = [(c, d) for c, d in siblings(code)
                      if "unspecified" not in d.lower() and (_sides_in(d) & note_sides)]
            if better:
                edits.append(Edit("UNSPECIFIED-CODE", "low", code, f"{code} is unspecified, but the note is more specific",
                                  "Consider: " + "; ".join(f"{c} {d}" for c, d in better[:3]), {"replace": better[0][0]}))
    if len(icd_codes) != len(set(map(dotted, icd_codes))):
        edits.append(Edit("DUPLICATE-DIAGNOSIS", "low", "", "A diagnosis code is listed twice", "Remove the duplicate."))

    em = [l for l in cpt_lines if CPT_CODES.get(l["code"], (None, 0, 1, False))[3]]
    minor = [l for l in cpt_lines if l["code"] in EM_BUNDLED_WITH]
    for line in cpt_lines:
        entry = CPT_CODES.get(line["code"])
        if entry is None:
            edits.append(Edit("CPT-UNKNOWN", "high", line["code"], f"CPT {line['code']} isn't in the code list",
                              "Only codes in the practice's licensed CPT list can be billed."))
            continue
        if (line.get("units") or 1) > entry[2]:
            edits.append(Edit("UNITS-EXCEEDED", "high", line["code"], f"{line['units']} units of {line['code']} "
                              f"exceeds the daily maximum of {entry[2]}", "Payers deny units over the medically "
                              "unlikely edit (MUE) limit.", {"units": entry[2]}))
    if len(em) > 1:
        edits.append(Edit("MULTIPLE-EM", "high", em[1]["code"], "More than one office visit code",
                          "Only one E/M visit is billed per provider per day.", {"remove": em[1]["code"]}))
    for visit in em:
        has_25 = "25" in (visit.get("modifiers") or [])
        if has_25 and minor and not separate_em_documented:
            edits.append(Edit("MODIFIER-25-UNSUPPORTED", "high", visit["code"], "Modifier 25 isn't supported by the note",
                              "Modifier 25 claims a separately identifiable visit, which the note doesn't document.",
                              {"remove": visit["code"]}))
        elif minor and not has_25 and separate_em_documented:
            edits.append(Edit("MODIFIER-25-MISSING", "medium", visit["code"], f"{visit['code']} needs modifier 25",
                              f"It's billed with {', '.join(l['code'] for l in minor)} the same day; the note documents a "
                              "separately identifiable visit, so add modifier 25 or the payer bundles it.",
                              {"add_modifier": "25"}))
        elif minor and not has_25:
            edits.append(Edit("EM-BUNDLED", "medium", visit["code"], f"{visit['code']} would be bundled into the procedure",
                              "The note doesn't document a problem managed separately from the procedure. Remove the "
                              "visit code, or ask the provider to document the separate problem.", {"remove": visit["code"]}))
        elif has_25 and not minor:
            edits.append(Edit("MODIFIER-25-UNNEEDED", "low", visit["code"], "Modifier 25 without a same-day procedure",
                              "Modifier 25 only applies when a minor procedure is billed the same day."))
    return [asdict(e) for e in edits]
