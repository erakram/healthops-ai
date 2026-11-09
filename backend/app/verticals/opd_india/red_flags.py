"""Red flags: signs that mean the emergency department now, not an OPD token.

Rules only, run on the complaint as typed (English, Hinglish or Devanagari) and on the
symptoms the Intake Agent extracted in English. The agent can add a flag (with a quote from
the complaint); it can never clear one. A flagged patient gets no routine token unless a
casualty doctor clears them and staff record that.
"""

import re
from dataclasses import dataclass

from app.verticals.opd_india.text import normalize


@dataclass(frozen=True)
class RedFlag:
    id: str
    label: str
    action: str  # what the desk should do


def _any(*patterns: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns))


CHEST = _any(r"\bchest (pain|tightness|heaviness|pressure)\b", r"\bpain in (the |my )?chest\b",
             r"\b(seene|chhati|chaati|chhaati) (me|mein|main|par) (dard|bhaari|bhari|dabav|dabaav)", "सीने में दर्द",
             "छाती में दर्द", "सीने में भारीपन")
CARDIAC_WITH = _any(r"\bsweat", r"\bpas(ee|i)n", "पसीना", r"\bleft arm\b", r"\bjaw\b", r"\bbaye?n? haath\b",
                    r"\bbreath", r"\bsaa?ns\b", "सांस", "साँस", r"\bghabrahat\b", "घबराहट", r"\bradiat")
STROKE = _any(r"\bface (droop|drooping|tedha)\b", r"\bslurred\b", r"\bweakness (on|in) one side\b",
              r"\bone side (of the body )?(weak|numb)", r"\b(muh|munh|mooh) tedha\b", r"\bbolne (me|mein|main) (dikkat|taklif|pareshani)",
              r"\bzubaa?n (ladkhada|latak)", r"\blakwa\b", "लकवा", r"\bparalys", r"\bsudden(ly)? (weak|numb)",
              r"\b(haath|hath) pair (sunn|kaam nahi)", "मुंह टेढ़ा", "बोलने में दिक्कत", r"\bstroke\b")
BREATHING = _any(r"\bcan ?not breathe\b", r"\bcant breathe\b", r"\bunable to breathe\b", r"\bgasping\b",
                 r"\bsaa?ns (nahi|nahin) (aa|le)", r"\bsaa?ns lene (me|mein|main) (bahut|bohot|zyada) (dikkat|taklif)",
                 r"\bblue lips\b", r"\b(neele|nile) (hoth|honth)", "सांस नहीं", "साँस नहीं", r"\bbreathless(ness)? at rest\b",
                 r"\bchoking\b")
BLEEDING = _any(r"\bvomit(ing|ed)? blood\b", r"\bblood in (vomit|the vomit)\b", r"\bkhoo?n ki ult(i|ee)\b", "खून की उल्टी",
                r"\bblack (stool|motion|potty)", r"\b(kaa?li|kaala) (potty|tatti|latrine)", r"\bheavy bleeding\b",
                r"\bbleeding (not|won t|wont) stop", r"\bkhoo?n (band nahi|ruk nahi|nahi ruk)", r"\bbahut khoo?n\b",
                r"\bcoughing (up )?blood\b", r"\bkhaa?nsi (me|mein|main) khoo?n\b")
UNCONSCIOUS = _any(r"\bunconscious\b", r"\bbehosh\b", "बेहोश", r"\bfainted\b", r"\bpassed out\b", r"\bseizures?\b",
                   r"\bfits\b", r"\bdaura\b", r"\bdaure\b", r"\bmirgi\b", r"\bconvuls", r"\bjhatke\b", "दौरा", "झटके",
                   r"\bnot responding\b", r"\bhosh (nahi|nahin)\b")
POISON = _any(r"\bpoison", r"\bzeh?a?e?r\b", "ज़हर", "जहर", r"\bsnake ?bite\b", r"\bsaa?n?p (ne )?(kaata|kata|kaat)",
              r"\bpesticide\b", r"\bkeetnashak\b", r"\boverdose\b", r"\bbitten by (a )?snake\b", r"\bdog bite\b",
              r"\bkutte ne kaa?ta\b")
TRAUMA = _any(r"\broad accident\b", r"\baccident\b", r"\bhead injury\b", r"\b(sir|sar) (pe|par|me|mein) chot\b",
              r"\bhit by (a )?(car|bike|truck|bus)\b", r"\bfell from (height|the roof|terrace)\b", r"\bchhat se gir")
SELF_HARM = _any(r"\bsuicid", r"\bkhudkushi\b", r"\baatmahatya\b", "आत्महत्या", r"\bwant to die\b",
                 r"\bend (my|his|her) life\b", r"\bmarna chaht(a|i|e)\b", r"\bkill (my|him|her)self\b")
PREGNANT = _any(r"\bpregnan", r"\bgarbh", "गर्भ", r"\bmonths? pregnant\b", r"\bexpecting\b", r"\bpet se\b")
PREG_DANGER = _any(r"\bbleed", r"\bkhoo?n\b", "खून", r"\bsevere (pain|abdominal)\b", r"\btez dard\b", r"\bbahut dard\b",
                   r"\bfits\b", r"\bconvuls", r"\bswelling\b", r"\bsoojan\b", r"\bbaby not moving\b", r"\bbachcha hil nahi")
FEVER = _any(r"\bfever\b", r"\bbukh?aa?r\b", "बुखार", r"\btemperature\b", r"\bhigh temp")
NECK_STIFF = _any(r"\bstiff neck\b", r"\bneck (is )?stiff", r"\bgardan (akad|jakad)", r"\bneck rigid")
CHILD_DANGER = _any(r"\bnot (drinking|feeding|eating)\b", r"\bdoodh (nahi|nahin) (pi|pee|le)", r"\bletharg", r"\bvery sleepy\b",
                    r"\bsust\b", r"\bjhatke\b", r"\bconvuls", r"\bfits\b", r"\bnot passed urine\b", r"\bpeshab (nahi|nahin)",
                    r"\bbreathing fast\b", r"\btez saa?ns\b")

FLAGS = {
    "cardiac": RedFlag("cardiac", "Possible heart attack", "Send to Emergency now; ECG within 10 minutes."),
    "stroke": RedFlag("stroke", "Possible stroke", "Send to Emergency now and note the time symptoms started."),
    "breathing": RedFlag("breathing", "Severe breathing difficulty", "Send to Emergency now."),
    "bleeding": RedFlag("bleeding", "Serious bleeding", "Send to Emergency now."),
    "unconscious": RedFlag("unconscious", "Fainting, fits or unconsciousness", "Send to Emergency now."),
    "poison": RedFlag("poison", "Poisoning or bite", "Send to Emergency now; bring the container or describe the snake."),
    "trauma": RedFlag("trauma", "Accident or head injury", "Send to Emergency (casualty) for assessment."),
    "self_harm": RedFlag("self_harm", "Risk of self-harm", "Stay with the patient; Emergency and the duty psychiatrist."),
    "pregnancy": RedFlag("pregnancy", "Danger sign in pregnancy", "Send to Emergency / labour room now."),
    "meningitis": RedFlag("meningitis", "Fever with a stiff neck", "Send to Emergency now."),
    "sick_child": RedFlag("sick_child", "Danger sign in a young child", "Send to Paediatric Emergency now."),
    "agent": RedFlag("agent", "Danger sign noted by the Intake Agent", "Ask the casualty doctor to see the patient first."),
}


def check(complaint: str, symptoms: list[str] | None = None, age: float | None = None) -> list[dict]:
    """Red flags found, each with the words that triggered it."""
    text = normalize(" ".join([complaint or "", *(symptoms or [])]))
    found: list[tuple[str, str]] = []

    def hit(flag_id: str, match: re.Match | None) -> None:
        if match and flag_id not in {f for f, _ in found}:
            found.append((flag_id, match.group(0)))

    chest = CHEST.search(text)
    if chest and (CARDIAC_WITH.search(text) or age is None or age >= 30):
        hit("cardiac", chest)
    hit("stroke", STROKE.search(text))
    hit("breathing", BREATHING.search(text))
    hit("bleeding", BLEEDING.search(text))
    hit("unconscious", UNCONSCIOUS.search(text))
    hit("poison", POISON.search(text))
    hit("trauma", TRAUMA.search(text))
    hit("self_harm", SELF_HARM.search(text))
    if PREGNANT.search(text):
        hit("pregnancy", PREG_DANGER.search(text))
    fever = FEVER.search(text)
    if fever:
        hit("meningitis", NECK_STIFF.search(text))
    if age is not None and age < 5:
        if fever and age < 0.25:
            hit("sick_child", fever)  # any fever under 3 months
        if fever or CHILD_DANGER.search(text):
            hit("sick_child", CHILD_DANGER.search(text))
    return [{"id": f, "label": FLAGS[f].label, "action": FLAGS[f].action, "matched": words} for f, words in found]
