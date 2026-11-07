"""Claim adjustment reason codes (CARC) and remark codes (RARC) used on remittances.

These are the standard X12 code sets payers put on an 835. Only the codes this pack
handles are listed; an unknown code still works, it is just triaged as "other" and
left for a person.

Group codes say who carries the adjustment:
    CO  contractual obligation: the provider writes it off unless it's appealed
    PR  patient responsibility: the patient can be billed
    OA  other adjustment
    PI  payer-initiated reduction
"""

from dataclasses import dataclass

GROUPS = {"CO": "Contractual obligation", "PR": "Patient responsibility", "OA": "Other adjustment",
          "PI": "Payer-initiated reduction", "CR": "Correction and reversal"}


@dataclass(frozen=True)
class Carc:
    code: str
    description: str
    category: str  # see CATEGORIES
    is_denial: bool = True  # False for routine adjustments such as the contractual write-down


CATEGORIES = {
    "patient_responsibility": "Patient responsibility",
    "authorization": "Authorization",
    "medical_necessity": "Medical necessity",
    "coding_bundling": "Coding and bundling",
    "timely_filing": "Timely filing",
    "eligibility": "Eligibility",
    "missing_information": "Missing information",
    "duplicate": "Duplicate",
    "other": "Other",
}

CARCS: dict[str, Carc] = {c.code: c for c in [
    Carc("1", "Deductible amount", "patient_responsibility"),
    Carc("2", "Coinsurance amount", "patient_responsibility"),
    Carc("3", "Co-payment amount", "patient_responsibility"),
    Carc("16", "Claim/service lacks information or has submission/billing error(s)", "missing_information"),
    Carc("18", "Exact duplicate claim/service", "duplicate"),
    Carc("27", "Expenses incurred after coverage terminated", "eligibility"),
    Carc("29", "The time limit for filing has expired", "timely_filing"),
    Carc("45", "Charge exceeds fee schedule/maximum allowable or contracted/legislated fee arrangement",
         "other", is_denial=False),
    Carc("50", "These are non-covered services because this is not deemed a 'medical necessity' by the payer",
         "medical_necessity"),
    Carc("97", "The benefit for this service is included in the payment/allowance for another service/procedure "
               "that has already been adjudicated", "coding_bundling"),
    Carc("197", "Precertification/authorization/notification/pre-treatment absent", "authorization"),
    Carc("198", "Precertification/notification/authorization/pre-treatment exceeded", "authorization"),
]}

RARCS = {
    "M15": "Separately billed services/tests have been bundled as they are considered components of the same "
           "procedure. Separate payment is not allowed.",
    "M62": "Missing/incomplete/invalid treatment authorization code.",
    "M127": "Missing patient medical record for this service.",
    "N115": "This decision was based on a Local Coverage Determination (LCD).",
    "N211": "Alert: You may not appeal this decision.",
    "N30": "Patient ineligible for this service.",
    "MA130": "Your claim contains incomplete and/or invalid information, and no appeal rights are afforded "
             "because the claim is unprocessable.",
}

# CLP02 claim status codes on an 835.
CLAIM_STATUS = {"1": "Processed as primary", "2": "Processed as secondary", "3": "Processed as tertiary",
                "4": "Denied", "19": "Processed as primary, forwarded", "22": "Reversal of previous payment"}


def describe(group: str, code: str) -> str:
    carc = CARCS.get(code)
    return f"{group}-{code}: {carc.description if carc else 'Unlisted reason code'}"


def category_of(code: str) -> str:
    carc = CARCS.get(code)
    return carc.category if carc else "other"


def is_denial(group: str, code: str) -> bool:
    """The contractual write-down (CO-45) is routine; everything else needs someone to look."""
    carc = CARCS.get(code)
    return carc.is_denial if carc else True
