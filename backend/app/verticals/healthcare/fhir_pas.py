"""Prior authorization over FHIR, shaped after HL7 Da Vinci PAS (Prior Authorization Support).

PAS is the FHIR exchange CMS requires payers to support for prior authorization
(CMS-0057-F). A request is a Bundle holding a Claim with use "preauthorization" plus the
Patient, Coverage, insurer, provider and supporting documents; the answer is a Bundle
holding a ClaimResponse whose item carries a review action (X12 code A1 approved, A3
denied, A4 pended) and, when approved, the authorization number and period.

This is a simplified profile: the resource shapes and code systems follow PAS, but it
isn't validated against the full implementation guide. Quotes the provider relies on
travel as supportingInfo, and the clinical documents as DocumentReference attachments,
so the payer can check every quote against the records itself.
"""

import base64
from datetime import datetime

CPT = "http://www.ama-assn.org/go/cpt"
ICD10 = "http://hl7.org/fhir/sid/icd-10-cm"
NPI = "http://hl7.org/fhir/sid/us-npi"
REVIEW_ACTION = "http://hl7.org/fhir/us/davinci-pas/StructureDefinition/extension-reviewAction"
X12_306 = "https://codesystem.x12.org/005010/306"
SUPPORTING_INFO = "http://hl7.org/us/davinci-pas/CodeSystem/PASSupportingInfoType"
CITATION_SOURCE = "urn:healthops:citation-source"
ACTIONS = {"A1": "approved", "A3": "denied", "A4": "pended"}


class FhirError(ValueError):
    pass


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def build_request(order: dict, citations: list[dict], documents: list[dict], letter: str, now: datetime) -> dict:
    """order: {request_number, attempt, order_number, urgency, scheduled_date, patient: {name, dob, member_id},
    payer: {name, id}, provider: {name, npi}, services: [{cpt, description, units}], diagnosis_codes}
    citations: [{source, quote}]; documents: [{title, text}]"""
    patient, payer, provider = order["patient"], order["payer"], order["provider"]
    supporting = [{
        "sequence": i,
        "category": {"coding": [{"system": SUPPORTING_INFO, "code": "additionalInformation"}]},
        "valueString": c["quote"],
        "extension": [{"url": CITATION_SOURCE, "valueString": c["source"]}],
    } for i, c in enumerate(citations, start=1)]
    docs = [{"title": "Medical necessity summary", "text": letter}] + documents
    supporting += [{
        "sequence": len(citations) + i,
        "category": {"coding": [{"system": SUPPORTING_INFO, "code": "attachment"}]},
        "valueReference": {"reference": f"urn:uuid:document-{i}"},
    } for i in range(1, len(docs) + 1)]

    claim = {
        "resourceType": "Claim",
        "identifier": [{"system": "urn:healthops:order", "value": order["order_number"]},
                       {"system": "urn:healthops:authorization-request", "value": order["request_number"]}],
        "status": "active",
        "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type", "code": "professional"}]},
        "use": "preauthorization",
        "patient": {"reference": "urn:uuid:patient"},
        "created": now.isoformat(),
        "insurer": {"reference": "urn:uuid:insurer"},
        "provider": {"reference": "urn:uuid:provider"},
        "priority": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/processpriority",
                                 "code": "stat" if order["urgency"] == "urgent" else "normal"}]},
        "insurance": [{"sequence": 1, "focal": True, "coverage": {"reference": "urn:uuid:coverage"}}],
        "diagnosis": [{"sequence": i, "diagnosisCodeableConcept": {"coding": [{"system": ICD10, "code": code}]}}
                      for i, code in enumerate(order["diagnosis_codes"], start=1)],
        "supportingInfo": supporting,
        "item": [{"sequence": i, "productOrService": {"coding": [{"system": CPT, "code": s["cpt"], "display": s["description"]}]},
                  "servicedDate": order["scheduled_date"], "quantity": {"value": s.get("units") or 1}}
                 for i, s in enumerate(order["services"], start=1)],
    }
    entries = [
        ("claim", claim),
        ("patient", {"resourceType": "Patient", "name": [{"text": patient["name"]}], "birthDate": patient["dob"],
                     "identifier": [{"system": "urn:healthops:member-id", "value": patient["member_id"]}]}),
        ("coverage", {"resourceType": "Coverage", "status": "active", "subscriberId": patient["member_id"],
                      "beneficiary": {"reference": "urn:uuid:patient"}, "payor": [{"reference": "urn:uuid:insurer"}]}),
        ("insurer", {"resourceType": "Organization", "name": payer["name"],
                     "identifier": [{"system": "urn:healthops:payer-id", "value": payer["id"]}]}),
        ("provider", {"resourceType": "Organization", "name": provider["name"],
                      "identifier": [{"system": NPI, "value": provider["npi"]}]}),
    ]
    entries += [(f"document-{i}", {"resourceType": "DocumentReference", "status": "current", "description": d["title"],
                                   "content": [{"attachment": {"contentType": "text/plain", "title": d["title"],
                                                               "data": _b64(d["text"])}}]})
                for i, d in enumerate(docs, start=1)]
    return {"resourceType": "Bundle", "type": "collection", "timestamp": now.isoformat(),
            "identifier": {"system": "urn:healthops:pas", "value": f"{order['request_number']}-{order['attempt']}"},
            "entry": [{"fullUrl": f"urn:uuid:{name}", "resource": resource} for name, resource in entries]}


def _resource(bundle: dict, kind: str) -> dict:
    if bundle.get("resourceType") != "Bundle":
        raise FhirError("Not a FHIR Bundle")
    for entry in bundle.get("entry") or []:
        if (entry.get("resource") or {}).get("resourceType") == kind:
            return entry["resource"]
    raise FhirError(f"Bundle has no {kind}")


def read_request(bundle: dict) -> dict:
    """What the payer reads from a PAS request."""
    claim = _resource(bundle, "Claim")
    if claim.get("use") != "preauthorization":
        raise FhirError("Claim.use must be 'preauthorization'")
    coverage = _resource(bundle, "Coverage")
    documents = {}
    for entry in bundle["entry"]:
        resource = entry["resource"]
        if resource.get("resourceType") == "DocumentReference":
            attachment = resource["content"][0]["attachment"]
            documents[entry["fullUrl"]] = {"title": attachment.get("title"),
                                           "text": base64.b64decode(attachment.get("data") or "").decode()}
    quotes = [{"quote": s["valueString"], "source": next((e["valueString"] for e in s.get("extension", [])
                                                           if e["url"] == CITATION_SOURCE), None)}
              for s in claim.get("supportingInfo", []) if "valueString" in s]
    return {
        "order_number": next(i["value"] for i in claim["identifier"] if i["system"] == "urn:healthops:order"),
        "request_number": next(i["value"] for i in claim["identifier"]
                               if i["system"] == "urn:healthops:authorization-request"),
        "member_id": coverage["subscriberId"],
        "urgent": claim["priority"]["coding"][0]["code"] == "stat",
        "cpts": [i["productOrService"]["coding"][0]["code"] for i in claim["item"]],
        "scheduled_date": claim["item"][0].get("servicedDate"),
        "quotes": quotes, "documents": list(documents.values()),
    }


def build_response(request: dict, decision: dict, now: datetime) -> dict:
    """decision: {action: A1|A3|A4, auth_number?, start?, end?, disposition, notes: [str]}"""
    review = [{"url": "code", "valueCodeableConcept": {"coding": [{"system": X12_306, "code": decision["action"]}]}}]
    if decision.get("auth_number"):
        review.insert(0, {"url": "number", "valueString": decision["auth_number"]})
    response = {
        "resourceType": "ClaimResponse",
        "status": "active",
        "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type", "code": "professional"}]},
        "use": "preauthorization",
        "patient": {"reference": "urn:uuid:patient"},
        "created": now.isoformat(),
        "insurer": {"reference": "urn:uuid:insurer"},
        "request": {"identifier": {"system": "urn:healthops:authorization-request", "value": request["request_number"]}},
        "outcome": "queued" if decision["action"] == "A4" else "complete",
        "disposition": decision["disposition"],
        "item": [{"itemSequence": i, "extension": [{"url": REVIEW_ACTION, "extension": review}],
                  "adjudication": [{"category": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/adjudication",
                                                             "code": "submitted"}]}}]}
                 for i in range(1, len(request["cpts"]) + 1)],
        "processNote": [{"number": i, "type": "display", "text": note} for i, note in enumerate(decision.get("notes", []), 1)],
    }
    if decision.get("auth_number"):
        response["preAuthRef"] = decision["auth_number"]
        response["preAuthPeriod"] = {"start": decision["start"], "end": decision["end"]}
    return {"resourceType": "Bundle", "type": "collection", "timestamp": now.isoformat(),
            "entry": [{"fullUrl": "urn:uuid:claim-response", "resource": response}]}


def read_response(bundle: dict) -> dict:
    """What the provider reads from the payer's answer."""
    response = _resource(bundle, "ClaimResponse")
    codes = set()
    for item in response.get("item") or []:
        for ext in item.get("extension") or []:
            if ext.get("url") == REVIEW_ACTION:
                for part in ext.get("extension") or []:
                    if part.get("url") == "code":
                        codes.add(part["valueCodeableConcept"]["coding"][0]["code"])
    if not codes:
        raise FhirError("ClaimResponse has no review action")
    # The least favourable line decides the request: denied over pended over approved.
    action = next(code for code in ("A3", "A4", "A1") if code in codes)
    period = response.get("preAuthPeriod") or {}
    return {"decision": ACTIONS[action], "review_action": action, "outcome": response.get("outcome"),
            "auth_number": response.get("preAuthRef"), "valid_from": period.get("start"), "valid_to": period.get("end"),
            "disposition": response.get("disposition"), "notes": [n["text"] for n in response.get("processNote") or []]}
