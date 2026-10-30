"""Generate the adversarial mock claim set under mockclaims/.

Each claim targets one rule of the policy.  Documents are written as files,
oracle.json holds what the model would have answered (document reading, fact
observations with quotes, judgments), expected.json holds the outcome we
require.  Run:  poetry run python scripts/make_mockclaims.py
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1] / "mockclaims"


# --------------------------------------------------------------------------- helpers
def lines_of(text: str) -> list[str]:
    return [ln.rstrip() for ln in text.splitlines() if ln.strip()]


def find_line(lines: list[str], needle: str) -> tuple[int, str]:
    for i, ln in enumerate(lines, start=1):
        if needle in ln:
            return i, ln
    raise SystemExit(f"quote {needle!r} not found in:\n" + "\n".join(lines))


def booking(name, ref, route, depart, paid, passengers, purchased, cover, extra=""):
    text = f"""# Booking confirmation {ref}
EuroTrips Travel GmbH
Lead passenger: {name}
Route: {route}
Departure: {depart}
Passengers: {passengers}
Total paid: EUR {paid:,.2f}
Booked and paid on {purchased}
CFSR cover period: {cover[0]} to {cover[1]}
{extra}"""
    facts = {
        "booking_value": (f"Total paid: EUR {paid:,.2f}", paid, 0.98),
        "passengers": (f"Passengers: {passengers}", passengers, 0.97),
        "purchase_date": ("Booked and paid on", purchased, 0.95),
        "policy_period": ("CFSR cover period", {"start": cover[0], "end": cover[1]}, 0.95),
    }
    return text, facts


def medical(hospital, patient, admitted, diagnosis, discharged=None, lang="en"):
    if lang == "es":
        text = f"""# Certificado médico
{hospital}
Paciente: {patient}
Fecha de ingreso: {admitted}
Diagnóstico: {diagnosis}
{'Fecha de alta: ' + discharged if discharged else ''}
El paciente no está en condiciones de viajar.
Dr. M. Ortega, Colegiado 28/4471"""
    else:
        text = f"""# Medical certificate
{hospital}
Patient: {patient}
Admitted on {admitted}
Diagnosis: {diagnosis}
{'Discharged on ' + discharged if discharged else ''}
The patient is not fit to travel.
Dr. H. Lindqvist, registration 55012"""
    return text


def police(station, reporter, date, incident, ref):
    return f"""# Police report {ref}
{station}
Reporting person: {reporter}
Date of incident: {date}
Incident: {incident}
Report filed and signed by the duty officer."""


def render_image(path: Path, lines: list[str], fmt: str = "PNG", font_size: int | None = None) -> None:
    """Text drawn as an image, saved in ``fmt`` whatever the filename says (the real claims
    have WebP files and JPEGs named .webp).  ``font_size`` picks a legible scalable font."""
    font = ImageFont.load_default(size=font_size) if font_size else None
    step = int(font_size * 1.6) if font_size else 28
    img = Image.new("RGB", (1100 if font_size else 900, 40 + step * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        draw.text((20, 20 + step * i), ln, fill="black", font=font)
    img.save(path, format=fmt)


MATCH_DEFAULTS = {
    "cancellation_reason": ("no", "The claim does not describe a cancelled or rescheduled trip for a covered reason."),
    "effects_lost": ("no", "No personal belongings are reported lost, stolen or damaged."),
    "missed_departure": ("no", "No missed departure or connection is described."),
    "cancellation_proof_fits": ("unknown", "Not evaluated: the cancellation coverage did not match."),
    "prior_knowledge": ("no", "Nothing indicates the claimant knew of the incident before purchase."),
    "suspicious_documents": ("no", "The documents are consistent with each other and show no alteration signs."),
    # gadget
    "device_stolen": ("no", "No theft is described."),
    "device_damaged": ("no", "No accidental damage is described."),
    "unattended": ("no", "The claimant states the device was with them."),
}


class Claim:
    def __init__(self, id, policy, description, submitted=None, expected=None, note=""):
        self.id, self.policy, self.description = id, policy, description
        self.submitted, self.expected, self.note = submitted, expected or {}, note
        self.docs: dict[str, dict] = {}
        self.facts: dict[str, list] = {}
        self.judgments: dict[str, dict] = {}

    def doc(self, filename, text=None, image_lines=None, raw=None, image_format="PNG", font_size=None, **reading):
        self.docs[filename] = {"text": text, "image_lines": image_lines, "raw": raw, "reading": reading,
                               "image_format": image_format, "font_size": font_size}
        return self

    def fact(self, name, document, quote, value, confidence=0.95, review=None):
        self.facts.setdefault(name, []).append((document, quote, value, confidence, review))
        return self

    def facts_from(self, document, facts: dict, only=None):
        for name, (quote, value, conf) in facts.items():
            if only is None or name in only:
                self.fact(name, document, quote, value, conf)
        return self

    def judge(self, check_id, answer, rationale, document=None, quote=None, confidence=0.9, review=None):
        self.judgments[check_id] = {"answer": answer, "rationale": rationale, "document": document,
                                    "quote": quote, "confidence": confidence, "review": review}
        return self

    def write(self, root: Path, needed_judgments: list[str]) -> None:
        d = root / self.id
        d.mkdir(parents=True)
        (d / "description.txt").write_text(self.description.strip() + "\n")
        meta = {"policy": self.policy, "submitted_at": self.submitted, "note": self.note}
        (d / "claim.json").write_text(json.dumps(meta, indent=2) + "\n")
        (d / "expected.json").write_text(json.dumps(self.expected, indent=2) + "\n")

        doc_lines: dict[str, list[str]] = {"description": lines_of(self.description)}
        oracle_docs = {}
        for filename, spec in self.docs.items():
            if spec["raw"] is not None:
                (d / filename).write_bytes(spec["raw"])
                continue  # unreadable: never reaches the reader
            if spec["image_lines"] is not None:
                render_image(d / filename, spec["image_lines"], spec["image_format"], spec["font_size"])
                doc_lines[filename] = spec["image_lines"]
                oracle_docs[filename] = {**spec["reading"], "lines": spec["image_lines"]}
            else:
                (d / filename).write_text(spec["text"].strip() + "\n")
                doc_lines[filename] = lines_of(spec["text"])
                oracle_docs[filename] = dict(spec["reading"])

        oracle_facts = {}
        for name, observations in self.facts.items():
            out = []
            for document, quote, value, conf, review in observations:
                line, full = find_line(doc_lines[document], quote)
                out.append({"document": document, "line": line, "quote": full, "value": value, "confidence": conf,
                            **({"review": review} if review else {})})
            oracle_facts[name] = out

        oracle_judgments = {}
        for check_id in needed_judgments:
            j = self.judgments.get(check_id)
            if j is None:
                answer, rationale = MATCH_DEFAULTS[check_id]
                j = {"answer": answer, "rationale": rationale, "document": None, "quote": None, "confidence": 0.9}
            entry = {"answer": j["answer"], "rationale": j["rationale"], "confidence": j["confidence"],
                     **({"review": j["review"]} if j.get("review") else {})}
            if j["document"]:
                line, full = find_line(doc_lines[j["document"]], j["quote"])
                entry.update(document=j["document"], line=line, quote=full)
            oracle_judgments[check_id] = entry

        oracle = {"documents": oracle_docs, "facts": oracle_facts, "judgments": oracle_judgments}
        (d / "oracle.json").write_text(json.dumps(oracle, indent=2, ensure_ascii=False) + "\n")


CFSR_JUDGMENTS = ["prior_knowledge", "suspicious_documents", "cancellation_reason", "cancellation_proof_fits",
                  "effects_lost", "missed_departure"]
GADGET_JUDGMENTS = ["unattended", "device_stolen", "device_damaged"]

claims: list[Claim] = []
COVER = ("2025-02-01", "2025-04-10")


# --------------------------------------------------------------------------- CFSR claims
def c01():
    c = Claim("01-clean-medical-cancellation", "cfsr", """
My name is Anna Weber. I had to cancel my trip to Lisbon because I was admitted to hospital with acute appendicitis on 20 March 2025, two days before departure.
I attach the hospital certificate and my booking confirmation. I am asking for a refund of the 1,240 euros I paid.
""", submitted="2025-03-25", expected={"decision": "APPROVE", "payout": 1240.0},
        note="The clean case: covered reason, both proofs, inside period, reported in time.")
    text, bf = booking("Anna Weber", "ET-77120", "Berlin - Lisbon", "2025-03-22", 1240.0, 1, "2025-02-01", COVER)
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.doc("hospital-certificate.md", medical("St. Hedwig Hospital, Berlin", "Anna Weber", "2025-03-20", "acute appendicitis, appendectomy", "2025-03-24"),
          doc_type="medical_report", issuer="St. Hedwig Hospital")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "My name is Anna Weber", "Anna Weber", 0.99)
    c.fact("claimant_name", "hospital-certificate.md", "Patient: Anna Weber", "Anna Weber", 0.95)
    c.fact("incident_date", "hospital-certificate.md", "Admitted on 2025-03-20", "2025-03-20", 0.97)
    c.fact("incident_date", "description", "20 March 2025", "2025-03-20", 0.9)
    c.fact("affected", "description", "My name is Anna Weber", 1, 0.8)
    c.judge("cancellation_reason", "yes", "Hospital admission for appendicitis two days before departure is a medical emergency.",
            "hospital-certificate.md", "Diagnosis: acute appendicitis")
    c.judge("cancellation_proof_fits", "yes", "The certificate names Anna Weber, is dated 20 March and states she is unfit to travel.",
            "hospital-certificate.md", "The patient is not fit to travel.")
    return c


def c02():
    c = Claim("02-incident-before-policy-start", "cfsr", """
I am Tomasz Nowak. I broke my leg skiing on 28 January 2025 and my doctor forbids me to fly, so I cancelled my March trip to Rome.
Booking confirmation and medical certificate attached.
""", submitted="2025-03-01", expected={"decision": "DENY"},
        note="Incident date before the policy start. Everything else is fine, the period check alone must deny.")
    text, bf = booking("Tomasz Nowak", "ET-77231", "Warsaw - Rome", "2025-03-14", 640.0, 1, "2025-02-01", COVER)
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.doc("medical-certificate.md", medical("Zakopane District Hospital", "Tomasz Nowak", "2025-01-28", "tibia fracture, cast for 8 weeks"),
          doc_type="medical_report", issuer="Zakopane District Hospital")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "I am Tomasz Nowak", "Tomasz Nowak", 0.99)
    c.fact("incident_date", "medical-certificate.md", "Admitted on 2025-01-28", "2025-01-28", 0.97)
    c.fact("incident_date", "description", "28 January 2025", "2025-01-28", 0.9)
    c.fact("affected", "description", "I am Tomasz Nowak", 1, 0.8)
    c.judge("cancellation_reason", "yes", "A fracture with a medical flying ban is a medical emergency.", "medical-certificate.md", "Diagnosis: tibia fracture")
    c.judge("cancellation_proof_fits", "yes", "Certificate names the claimant and the injury date.", "medical-certificate.md", "Patient: Tomasz Nowak")
    # a careful model might flag this too; we leave prior_knowledge "no" because the injury is not the thing he knew about
    # before purchase: he bought the cover 4 days after the injury, so a strong model may answer "yes".  Either way: DENY.
    return c


def c03():
    c = Claim("03-late-reporting-31-days", "cfsr", """
Claimant: Marie Dubois. I was summoned for jury duty on 3 April 2025 and could not travel to Vienna on 5 April. Summons and booking attached. Sorry for the delay in reporting, I was away.
""", submitted="2025-05-11", expected={"decision": "DENY"},
        note="Submitted 31 days after the policy ended. Boundary partner of claim 04.")
    text, bf = booking("Marie Dubois", "ET-77302", "Paris - Vienna", "2025-04-05", 390.0, 1, "2025-02-01", COVER)
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.doc("jury-summons.md", """# Tribunal judiciaire de Paris - Convocation
Mme Marie Dubois
Vous êtes convoquée en qualité de jurée le 3 avril 2025 à 9h00.
La présence est obligatoire.""", doc_type="jury_summons", issuer="Tribunal judiciaire de Paris", language="fr")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "Claimant: Marie Dubois", "Marie Dubois", 0.99)
    c.fact("incident_date", "jury-summons.md", "le 3 avril 2025", "2025-04-03", 0.95)
    c.fact("affected", "description", "Claimant: Marie Dubois", 1, 0.8)
    c.judge("cancellation_reason", "yes", "Jury duty is explicitly listed as a covered reason.", "jury-summons.md", "en qualité de jurée")
    c.judge("cancellation_proof_fits", "yes", "The summons names the claimant and the date, two days before departure.", "jury-summons.md", "Mme Marie Dubois")
    return c


def c04():
    c = c03()
    c.id, c.submitted = "04-late-reporting-boundary-30-days", "2025-05-10"
    c.expected = {"decision": "APPROVE", "payout": 390.0}
    c.note = "Same claim as 03, submitted exactly 30 days after the policy ended: inside the limit."
    return c


def c05():
    c = Claim("05-theft-without-police-report", "cfsr", """
I'm Luca Ferri. My bag with my passport was stolen on the metro on 2 March 2025, so I could not board my flight to Dublin on 4 March and had to cancel. I did not report it to the police because I had no time. I wrote down what happened, see attached. Booking attached.
""", submitted="2025-03-10", expected={"decision": "DENY"},
        note="Covered reason (theft) but the required police report is missing: both cancellation and personal effects fail on proof.")
    text, bf = booking("Luca Ferri", "ET-77410", "Milan - Dublin", "2025-03-04", 310.0, 1, "2025-02-01", COVER)
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.doc("my-statement.md", """# What happened
On 2 March 2025 on the M1 metro my backpack was taken while I was standing.
It contained my passport, wallet and headphones.
Written by me, Luca Ferri.""", doc_type="other", issuer=None, summary="claimant's own written statement, not an official report")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "I'm Luca Ferri", "Luca Ferri", 0.99)
    c.fact("incident_date", "my-statement.md", "On 2 March 2025", "2025-03-02", 0.9)
    c.fact("affected", "description", "I'm Luca Ferri", 1, 0.8)
    c.judge("cancellation_reason", "yes", "Theft of a passport preventing departure is a theft/criminal incident.", "my-statement.md", "my backpack was taken")
    c.judge("cancellation_proof_fits", "no", "The only supporting document is the claimant's own statement; there is no police report.", "my-statement.md", "Written by me")
    c.judge("effects_lost", "yes", "The bag and its contents were stolen during the trip.", "my-statement.md", "It contained my passport")
    return c


def c06():
    c = Claim("06-incident-dates-disagree", "cfsr", """
Sofia Marin here. I fell ill on 20 March 2025 and was hospitalised, so I cancelled my trip to Prague departing 22 March. Certificate and booking attached.
""", submitted="2025-03-28", expected={"decision": "DENY"},
        note="The description says 20 March, the certificate says 12 March. Cross-document disagreement is itself a reason to deny.")
    text, bf = booking("Sofia Marin", "ET-77455", "Madrid - Prague", "2025-03-22", 520.0, 1, "2025-02-01", COVER)
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.doc("certificate.md", medical("Hospital Clínico San Carlos", "Sofia Marin", "2025-03-12", "pneumonia", "2025-03-19"),
          doc_type="medical_report", issuer="Hospital Clínico San Carlos")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "Sofia Marin here", "Sofia Marin", 0.99)
    c.fact("incident_date", "certificate.md", "Admitted on 2025-03-12", "2025-03-12", 0.95)
    c.fact("incident_date", "description", "20 March 2025", "2025-03-20", 0.9)
    c.fact("affected", "description", "Sofia Marin here", 1, 0.8)
    c.judge("cancellation_reason", "yes", "Hospitalisation is a medical emergency.", "certificate.md", "Diagnosis: pneumonia")
    c.judge("cancellation_proof_fits", "no", "The certificate shows admission on 12 March and discharge on 19 March, before the stated 20 March illness and before departure.",
            "certificate.md", "Discharged on 2025-03-19")
    c.judge("suspicious_documents", "yes", "The claimant's dates contradict the certificate: illness on 20 March vs admission 12 March and discharge 19 March.",
            "certificate.md", "Admitted on 2025-03-12")
    return c


def c07():
    c = Claim("07-policy-period-unknown", "cfsr", """
Peter Hall. Hospitalised with a kidney stone on 15 March 2025, missed my trip to Oslo on 16 March. Please see the attached documents.
""", submitted="2025-03-20", expected={"decision": "UNCERTAIN"},
        note="A genuine referral: no policy period or purchase date anywhere. The coverage is otherwise eligible.")
    c.doc("booking-confirmation.md", """# Booking confirmation ET-77510
EuroTrips Travel GmbH
Lead passenger: Peter Hall
Route: London - Oslo
Departure: 2025-03-16
Passengers: 1
Total paid: EUR 275.00""", doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.doc("discharge-summary.md", medical("Royal Free Hospital", "Peter Hall", "2025-03-15", "renal colic", "2025-03-17"),
          doc_type="medical_report", issuer="Royal Free Hospital")
    c.fact("claimant_name", "description", "Peter Hall", "Peter Hall", 0.99)
    c.fact("incident_date", "discharge-summary.md", "Admitted on 2025-03-15", "2025-03-15", 0.97)
    c.fact("booking_value", "booking-confirmation.md", "Total paid", 275.0, 0.98)
    c.fact("passengers", "booking-confirmation.md", "Passengers: 1", 1, 0.97)
    c.fact("affected", "description", "Peter Hall", 1, 0.8)
    c.judge("prior_knowledge", "unknown", "The purchase date is not stated anywhere, so the order of events cannot be established.", confidence=0.5)
    c.judge("cancellation_reason", "yes", "Hospital admission the day before departure.", "discharge-summary.md", "Diagnosis: renal colic")
    c.judge("cancellation_proof_fits", "yes", "Names the claimant, covers the date.", "discharge-summary.md", "Patient: Peter Hall")
    return c


def c08():
    c = Claim("08-altered-booking-image", "cfsr", """
Ines Costa. Appendix surgery 10 March 2025, cancelled trip to Athens on 12 March. Attached: the hospital letter and a photo of my booking.
""", submitted="2025-03-18", expected={"decision": "DENY"},
        note="The booking photo shows two alteration signs: it is untrusted, so the booking proof is missing and the suspicious-documents exclusion fires.")
    c.doc("hospital-letter.md", medical("Hospital de Santa Maria", "Ines Costa", "2025-03-10", "appendicitis", "2025-03-13"),
          doc_type="medical_report", issuer="Hospital de Santa Maria")
    c.doc("booking-photo.png", image_lines=[
        "Booking confirmation ET-77588", "EuroTrips Travel GmbH", "Lead passenger: Ines Costa",
        "Route: Lisbon - Athens", "Departure: 2025-03-12", "Passengers: 1", "Total paid: EUR 2,180.00",
        "Booked and paid on 2025-02-01", "CFSR cover period: 2025-02-01 to 2025-04-10",
    ], doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH",
        alteration_signs=["the total amount is in a different font and weight from the rest of the line",
                          "the last digit of the departure date is overwritten"])
    c.fact("claimant_name", "description", "Ines Costa", "Ines Costa", 0.99)
    c.fact("incident_date", "hospital-letter.md", "Admitted on 2025-03-10", "2025-03-10", 0.97)
    c.fact("booking_value", "booking-photo.png", "Total paid", 2180.0, 0.6)
    c.fact("passengers", "booking-photo.png", "Passengers: 1", 1, 0.9)
    c.fact("purchase_date", "booking-photo.png", "Booked and paid on", "2025-02-01", 0.9)
    c.fact("policy_period", "booking-photo.png", "CFSR cover period", {"start": "2025-02-01", "end": "2025-04-10"}, 0.9)
    c.fact("affected", "description", "Ines Costa", 1, 0.8)
    c.judge("cancellation_reason", "yes", "Surgery two days before departure.", "hospital-letter.md", "Diagnosis: appendicitis")
    c.judge("cancellation_proof_fits", "yes", "The letter fits the claimant and date.", "hospital-letter.md", "Patient: Ines Costa")
    c.judge("suspicious_documents", "yes", "The booking photo shows a retyped total and an overwritten date digit.", "booking-photo.png", "Total paid")
    return c


def c09():
    c = Claim("09-prior-knowledge", "cfsr", """
Jonas Berg. I had surgery on 5 March 2025 and could not travel to Barcelona on 8 March. Attaching the surgery letter and booking.
""", submitted="2025-03-12", expected={"decision": "DENY"},
        note="The surgery was scheduled in a letter dated 20 January, before the 1 February purchase: prior knowledge.")
    text, bf = booking("Jonas Berg", "ET-77601", "Stockholm - Barcelona", "2025-03-08", 710.0, 1, "2025-02-01", COVER)
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.doc("surgery-letter.md", """# Karolinska University Hospital
Letter dated 2025-01-20
Patient: Jonas Berg
Your elective knee arthroscopy is scheduled for 2025-03-05.
Expect two weeks of restricted mobility after the procedure.""", doc_type="medical_report", issuer="Karolinska University Hospital")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "Jonas Berg", "Jonas Berg", 0.99)
    c.fact("incident_date", "surgery-letter.md", "scheduled for 2025-03-05", "2025-03-05", 0.95)
    c.fact("affected", "description", "Jonas Berg", 1, 0.8)
    c.judge("prior_knowledge", "yes", "The surgery was scheduled on 20 January, twelve days before the cover was bought on 1 February.",
            "surgery-letter.md", "Letter dated 2025-01-20", confidence=0.95)
    c.judge("cancellation_reason", "yes", "Surgery preventing travel is a medical reason.", "surgery-letter.md", "knee arthroscopy")
    c.judge("cancellation_proof_fits", "yes", "Names the claimant and the date.", "surgery-letter.md", "Patient: Jonas Berg")
    return c


def c10():
    c = Claim("10-voluntary-change", "cfsr", """
Hi, Emma Fischer. I decided not to go to Copenhagen on 15 March 2025 after all because my friend could no longer join me and it would not have been fun alone. Booking attached, please refund.
""", submitted="2025-03-16", expected={"decision": "DENY"},
        note="Not a covered reason. No coverage matches, so the denial comes from the match questions, not from an exclusion.")
    text, bf = booking("Emma Fischer", "ET-77644", "Hamburg - Copenhagen", "2025-03-15", 180.0, 1, "2025-02-01", COVER)
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "Emma Fischer", "Emma Fischer", 0.99)
    c.fact("incident_date", "description", "15 March 2025", "2025-03-15", 0.6)
    c.fact("affected", "description", "Emma Fischer", 1, 0.8)
    c.judge("cancellation_reason", "no", "A friend dropping out is a voluntary change of plans, not a listed covered reason.", "description", "my friend could no longer join")
    return c


def c11():
    c = Claim("11-personal-effects-two-people", "cfsr", """
Omar and Leila Haddad. On 9 March 2025 at Lyon Part-Dieu station both our suitcases were stolen while we waited for the train to Marseille. We continued the trip. Police report attached. We claim for our lost belongings.
""", submitted="2025-03-15", expected={"decision": "APPROVE", "payout": 200.0},
        note="Personal effects, two people affected: flat 100 per person. The trip was not cancelled, so the other coverages do not apply.")
    text, bf = booking("Omar Haddad", "ET-77702", "Lyon - Marseille", "2025-03-09", 98.0, 2, "2025-02-01", COVER)
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.doc("police-report.md", police("Commissariat de Police Lyon 3e", "Omar Haddad and Leila Haddad", "2025-03-09",
                                     "theft of two suitcases from the platform at Lyon Part-Dieu", "PV-2025-03-0914"),
          doc_type="police_report", issuer="Commissariat de Police Lyon 3e")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "Omar and Leila Haddad", "Omar Haddad", 0.9)
    c.fact("incident_date", "police-report.md", "Date of incident: 2025-03-09", "2025-03-09", 0.97)
    c.fact("affected", "police-report.md", "Reporting person: Omar Haddad and Leila Haddad", 2, 0.9)
    c.fact("affected", "description", "both our suitcases", 2, 0.85)
    c.judge("effects_lost", "yes", "Two suitcases stolen at the station during the trip, confirmed by police.", "police-report.md", "theft of two suitcases")
    return c


def c12():
    c = Claim("12-missed-connection-formula", "cfsr", """
Family Rossi (Marco, Giulia and our two children). On the way to Milan Centrale on 3 March 2025 our taxi was hit by a van; Giulia and I were taken to the emergency room and the four of us missed the train to Zurich. Accident report from the police and booking attached. Only the two of us were injured; the children were fine.
""", submitted="2025-03-06", expected={"decision": "UNCERTAIN"},
        note="Missed connection formula: 50% x 800 / 4 passengers x affected. Four missed the train, two were "
             "injured: 'affected' reads either way (400 or 200), so the extractor flags it and a person decides.")
    text, bf = booking("Marco Rossi", "ET-77750", "Milan - Zurich", "2025-03-03", 800.0, 4, "2025-02-01", COVER)
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.doc("accident-report.md", police("Polizia Locale di Milano", "Marco Rossi", "2025-03-03",
                                       "road traffic collision, taxi struck by a van on Viale Lunigiana; two passengers injured", "INC-2025-0303-17"),
          doc_type="incident_report", issuer="Polizia Locale di Milano")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "Family Rossi", "Marco Rossi", 0.9)
    c.fact("incident_date", "accident-report.md", "Date of incident: 2025-03-03", "2025-03-03", 0.97)
    c.fact("affected", "description", "the four of us missed the train to Zurich", 4, 0.6,
           review="Four travellers missed the train but only two were injured; 'affected' could be 4 or 2.")
    c.judge("missed_departure", "yes", "A traffic accident en route caused the family to miss the train; the policy's own example.", "accident-report.md", "road traffic collision")
    return c


def c13():
    c = c12()
    c.id = "13-missed-connection-passengers-unknown"
    c.expected = {"decision": "UNCERTAIN"}
    c.note = "Same accident, but the booking does not state the passenger count, so the formula cannot be computed."
    text = c.docs["booking-confirmation.md"]["text"].replace("Passengers: 4", "Passengers: see attached list")
    c.docs["booking-confirmation.md"]["text"] = text
    c.facts["passengers"] = []
    return c


def c14():
    c = Claim("14-jury-duty", "cfsr", """
Daniel Okafor. I received a jury summons for 24 March 2025 and had to cancel my trip to Edinburgh (departing 23 March, returning 27 March). Summons and booking attached.
""", submitted="2025-03-26", expected={"decision": "APPROVE", "payout": 560.0},
        note="Jury duty, the first listed covered reason.")
    text, bf = booking("Daniel Okafor", "ET-77811", "Manchester - Edinburgh", "2025-03-23", 560.0, 1, "2025-02-01", COVER)
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.doc("jury-summons.md", """# HM Courts & Tribunals Service - Jury Summons
Mr Daniel Okafor
You are summoned to attend Manchester Crown Court for jury service starting 24 March 2025.
Attendance is compulsory. Failure to attend may result in a fine.""", doc_type="jury_summons", issuer="HM Courts & Tribunals Service")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "Daniel Okafor", "Daniel Okafor", 0.99)
    c.fact("incident_date", "jury-summons.md", "starting 24 March 2025", "2025-03-24", 0.97)
    c.fact("affected", "description", "Daniel Okafor", 1, 0.8)
    c.judge("cancellation_reason", "yes", "Jury duty is listed as a covered reason.", "jury-summons.md", "jury service")
    c.judge("cancellation_proof_fits", "yes", "Summons names the claimant and falls inside the trip dates.", "jury-summons.md", "Mr Daniel Okafor")
    return c


def c15():
    c = Claim("15-spanish-medical-certificate", "cfsr", """
Carmen Ruiz. Ingresé en el hospital el 2 de marzo de 2025 con una crisis asmática grave y tuve que cancelar mi viaje a Berlín del 4 de marzo. Adjunto certificado médico y reserva.
""", submitted="2025-03-09", expected={"decision": "APPROVE", "payout": 430.0},
        note="Everything in Spanish. Language must not change the outcome.")
    text, bf = booking("Carmen Ruiz", "ET-77860", "Sevilla - Berlin", "2025-03-04", 430.0, 1, "2025-02-01", COVER)
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.doc("certificado-medico.md", medical("Hospital Universitario Virgen del Rocío", "Carmen Ruiz", "2025-03-02", "crisis asmática grave", "2025-03-05", lang="es"),
          doc_type="medical_report", issuer="Hospital Universitario Virgen del Rocío", language="es")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "Carmen Ruiz", "Carmen Ruiz", 0.99)
    c.fact("incident_date", "certificado-medico.md", "Fecha de ingreso: 2025-03-02", "2025-03-02", 0.97)
    c.fact("incident_date", "description", "2 de marzo de 2025", "2025-03-02", 0.9)
    c.fact("affected", "description", "Carmen Ruiz", 1, 0.8)
    c.judge("cancellation_reason", "yes", "A severe asthma attack requiring admission two days before departure is a medical emergency.",
            "certificado-medico.md", "Diagnóstico: crisis asmática grave")
    c.judge("cancellation_proof_fits", "yes", "The certificate names Carmen Ruiz, gives the admission date and states she is unfit to travel.",
            "certificado-medico.md", "El paciente no está en condiciones de viajar.")
    return c


def c16():
    c = Claim("16-theft-causing-missed-train", "cfsr", """
Nora Lind. On 11 March 2025 my handbag with my ticket and wallet was snatched at Brussels-Midi; while I was at the police station I missed my train to Amsterdam and bought a new ticket later. I claim for the missed train and my stolen belongings. Police report and booking attached.
""", submitted="2025-03-14", expected={"decision": "APPROVE", "payout": 250.0},
        note="Two coverages at once: personal effects (100) plus missed connection (50% x 300 / 1 x 1 = 150). Payouts add.")
    text, bf = booking("Nora Lind", "ET-77903", "Brussels - Amsterdam", "2025-03-11", 300.0, 1, "2025-02-01", COVER)
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.doc("police-report.md", police("Police Fédérale, Gare de Bruxelles-Midi", "Nora Lind", "2025-03-11",
                                     "snatch theft of a handbag containing wallet, phone and rail ticket", "PV-BXL-2025-1103"),
          doc_type="police_report", issuer="Police Fédérale")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "Nora Lind", "Nora Lind", 0.99)
    c.fact("incident_date", "police-report.md", "Date of incident: 2025-03-11", "2025-03-11", 0.97)
    c.fact("affected", "description", "Nora Lind", 1, 0.8)
    c.judge("effects_lost", "yes", "Handbag and contents stolen during the trip, police confirmed.", "police-report.md", "snatch theft")
    c.judge("missed_departure", "yes", "The theft and the police visit caused her to miss the booked train.", "description", "I missed my train")
    return c


def c17():
    c = Claim("17-weather-cancellation", "cfsr", """
Felix Brandt. My flight to Dublin on 7 March 2025 was cancelled by the airline because of storm Hanna and I could not travel. Airline notice and booking attached. I would like my money back.
""", submitted="2025-03-10", expected={"decision": "UNCERTAIN"},
        note="The policy lists personal emergencies and says nothing about weather. Whether a storm counts is an "
             "interpretation of the wording: the judge flags it, and the claim is referred even though no listed "
             "proof is attached.")
    text, bf = booking("Felix Brandt", "ET-77950", "Frankfurt - Dublin", "2025-03-07", 240.0, 1, "2025-02-01", COVER)
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.doc("airline-notice.md", """# Flight cancellation notice
Dear Felix Brandt,
Flight EW 9410 Frankfurt - Dublin on 2025-03-07 has been cancelled due to severe weather (storm Hanna).
Rebooking and refund options are available through our website.""", doc_type="incident_report", issuer="Eurowings")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "Felix Brandt", "Felix Brandt", 0.99)
    c.fact("incident_date", "airline-notice.md", "has been cancelled", "2025-03-07", 0.95)
    c.fact("affected", "description", "Felix Brandt", 1, 0.8)
    c.judge("cancellation_reason", "unknown", "Weather is neither listed as a covered reason nor excluded; 'other specified personal emergencies' does not clearly include it.",
            "airline-notice.md", "severe weather", confidence=0.5,
            review="Whether a storm that cancels the flight is an 'other specified personal emergency' is for an adjuster.")
    c.judge("cancellation_proof_fits", "yes", "The notice names the claimant and the date.", "airline-notice.md", "Dear Felix Brandt")
    return c


def c18():
    c = Claim("18-unreadable-police-report", "cfsr", """
Aiko Tanaka. My luggage was stolen at Gare du Nord on 5 March 2025 and I cancelled the rest of my trip to Lille. The police report is attached as a PDF, together with my booking.
""", submitted="2025-03-09", expected={"decision": "UNCERTAIN"},
        note="The police report could not be read. The proof may exist, so this is a referral, not a denial.")
    text, bf = booking("Aiko Tanaka", "ET-78002", "Paris - Lille", "2025-03-06", 95.0, 1, "2025-02-01", COVER)
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH")
    c.doc("police-report.pdf", raw=b"%PDF-1.4\n\xff\xfe\x00\x01\x02binary\x80\x81\x82\n%%EOF\n")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "Aiko Tanaka", "Aiko Tanaka", 0.99)
    c.fact("incident_date", "description", "5 March 2025", "2025-03-05", 0.85)
    c.fact("affected", "description", "Aiko Tanaka", 1, 0.8)
    c.judge("cancellation_reason", "yes", "Theft leading to cancellation.", "description", "My luggage was stolen")
    c.judge("cancellation_proof_fits", "unknown", "The police report could not be read, so it cannot be checked against the claim.", confidence=0.3)
    c.judge("effects_lost", "yes", "Luggage stolen during the trip.", "description", "My luggage was stolen")
    return c


# --------------------------------------------------------------------------- Gadget claims
def receipt(name, item, price, date):
    return f"""# Receipt
TechHaus Berlin
Customer: {name}
Item: {item}
Price: EUR {price:,.2f}
Date: {date}"""


def g01():
    c = Claim("g01-theft-clean", "gadget", """
Ben Adler. My phone was stolen from my jacket pocket on the tram on 10 June 2025. Police report and receipt attached.
""", submitted="2025-06-11", expected={"decision": "APPROVE", "payout": 950.0},
        note="Theft: min(1200, 1000) - 50 = 950.")
    c.doc("receipt.md", receipt("Ben Adler", "Phone X12 256GB", 1200.0, "2025-01-15") + "\nGadget Cover period: 2025-01-15 to 2026-01-14",
          doc_type="purchase_receipt", issuer="TechHaus Berlin")
    c.doc("police-report.md", police("Polizei Berlin, Abschnitt 34", "Ben Adler", "2025-06-10", "pickpocketing of a mobile phone on tram M10", "PB-2025-061044"),
          doc_type="police_report", issuer="Polizei Berlin")
    c.fact("claimant_name", "description", "Ben Adler", "Ben Adler", 0.99)
    c.fact("incident_date", "police-report.md", "Date of incident: 2025-06-10", "2025-06-10", 0.97)
    c.fact("item_value", "receipt.md", "Price: EUR 1,200.00", 1200.0, 0.98)
    c.fact("policy_period", "receipt.md", "Gadget Cover period", {"start": "2025-01-15", "end": "2026-01-14"}, 0.95)
    c.judge("device_stolen", "yes", "Pickpocketed on the tram, police confirmed.", "police-report.md", "pickpocketing")
    return c


def g02():
    c = g01()
    c.id, c.submitted = "g02-theft-reported-late", "2025-06-14"
    c.expected = {"decision": "DENY"}
    c.note = "Same theft reported four days later: outside the 48-hour window."
    return c


def g03():
    c = Claim("g03-accidental-damage", "gadget", """
Mira Sato. I dropped my tablet on 2 July 2025 and the screen cracked. Repair quote and the purchase receipt are attached.
""", submitted="2025-07-03", expected={"decision": "APPROVE", "payout": 130.0},
        note="Accidental damage: min(180, 900) - 50 = 130.")
    c.doc("receipt.md", receipt("Mira Sato", "Tablet Air 11", 900.0, "2025-03-01") + "\nGadget Cover period: 2025-03-01 to 2026-02-28",
          doc_type="purchase_receipt", issuer="TechHaus Berlin")
    c.doc("repair-quote.md", """# Repair quote Q-5520
ScreenFix Hamburg
Device: Tablet Air 11, cracked display
Quoted repair cost: EUR 180.00
Date: 2025-07-03""", doc_type="repair_quote", issuer="ScreenFix Hamburg")
    c.fact("claimant_name", "description", "Mira Sato", "Mira Sato", 0.99)
    c.fact("incident_date", "description", "2 July 2025", "2025-07-02", 0.9)
    c.fact("item_value", "receipt.md", "Price: EUR 900.00", 900.0, 0.98)
    c.fact("repair_cost", "repair-quote.md", "Quoted repair cost", 180.0, 0.98)
    c.fact("policy_period", "receipt.md", "Gadget Cover period", {"start": "2025-03-01", "end": "2026-02-28"}, 0.95)
    c.judge("device_damaged", "yes", "Dropped, screen cracked, repair quoted.", "repair-quote.md", "cracked display")
    return c


def g04():
    c = Claim("g04-left-unattended", "gadget", """
Ben Adler. I left my laptop on the café table while I went to the counter and when I came back on 20 June 2025 it was gone. Police report and receipt attached.
""", submitted="2025-06-20", expected={"decision": "DENY"},
        note="Theft with full proof, but the device was left unattended in a public place.")
    c.doc("receipt.md", receipt("Ben Adler", "Laptop Pro 14", 1800.0, "2025-01-15") + "\nGadget Cover period: 2025-01-15 to 2026-01-14",
          doc_type="purchase_receipt", issuer="TechHaus Berlin")
    c.doc("police-report.md", police("Polizei Berlin, Abschnitt 34", "Ben Adler", "2025-06-20", "theft of a laptop left on a café table", "PB-2025-062011"),
          doc_type="police_report", issuer="Polizei Berlin")
    c.fact("claimant_name", "description", "Ben Adler", "Ben Adler", 0.99)
    c.fact("incident_date", "police-report.md", "Date of incident: 2025-06-20", "2025-06-20", 0.97)
    c.fact("item_value", "receipt.md", "Price: EUR 1,800.00", 1800.0, 0.98)
    c.fact("policy_period", "receipt.md", "Gadget Cover period", {"start": "2025-01-15", "end": "2026-01-14"}, 0.95)
    c.judge("device_stolen", "yes", "Taken from the table, police confirmed.", "police-report.md", "theft of a laptop")
    c.judge("unattended", "yes", "The claimant left the laptop on the table and walked away.", "description", "I left my laptop on the café table", confidence=0.95)
    return c



# --------------------------------------------------------------------------- B: the blind-run learnings
# Shaped like the real claims rather than like a tidy test: no stated cover period (the period
# runs from purchase to the end of the trip), names written differently on each document,
# certificates as photos in WebP or as JPEGs named .webp.  Each claim isolates one failure the
# blind run showed; most come in pairs that differ in one detail, so the decomposed pipeline,
# the end-to-end call and Clef can be compared claim by claim.
def trip_booking(name, ref, route, depart, ret, paid, passengers, purchased, travellers=None, stamp=None):
    """A booking with travel dates but no stated cover period, as in the real claims.  ``stamp``
    adds the intake system's "Current date is" line, the claim's submission date."""
    text = f"""{'**Current date is: ' + stamp + '**' if stamp else ''}
# Booking confirmation {ref}
SkyLine Travel
Lead passenger: {name}
{'Travellers: ' + travellers if travellers else ''}
Route: {route}
Outbound: {depart}
{'Return: ' + ret if ret else 'One-way ticket'}
Passengers: {passengers}
Total paid: EUR {paid:,.2f}
Booked and paid on {purchased}"""
    facts = {
        "booking_value": (f"Total paid: EUR {paid:,.2f}", paid, 0.98),
        "passengers": (f"Passengers: {passengers}", passengers, 0.97),
        "purchase_date": ("Booked and paid on", purchased, 0.95),
        # no cover period is written: from purchase to the last day of the trip
        "policy_period": ("Booked and paid on", {"start": purchased, "end": ret or depart}, 0.8),
    }
    return text, facts


def medical_photo(hospital, patient, admitted, diagnosis, lang="fr"):
    """A photographed certificate, line by line as a reader would transcribe it."""
    if lang == "fr":
        return [hospital, "CERTIFICAT MEDICAL", f"Je soussigne certifie avoir examine {patient}",
                f"le {admitted}. Diagnostic : {diagnosis}.", "Etat de sante incompatible avec un voyage.",
                "Dr P. Lemaire - RPPS 10003345678", "Cachet et signature"]
    return [hospital, "CERTIFICADO MEDICO", f"Paciente: {patient}", f"Fecha de atencion: {admitted}",
            f"Diagnostico: {diagnosis}", "Reposo absoluto. No apto para viajar.", "Dra. L. Castillo - Reg. 44120"]


def cancellation(c, reason_doc, reason_quote, fits_quote, fits_doc=None):
    c.judge("cancellation_reason", "yes", "A medical emergency prevented the trip.", reason_doc, reason_quote)
    c.judge("cancellation_proof_fits", "yes", "The certificate fits the claimant, the reason and the date.",
            fits_doc or reason_doc, fits_quote)
    return c


def b01():
    c = Claim("b01-name-case-and-order", "cfsr", """
Evelyne Kacou Meitiale
I could not travel to Abidjan on 12 April 2025: I was admitted to hospital with malaria on 9 April. Certificate and booking attached.
""", submitted="2025-04-20", expected={"decision": "APPROVE", "payout": 624.0},
        note="The booking writes KACOU MEITIALE Evelyne, the certificate Mme KACOU Evelyne: same person, no contradiction.")
    text, bf = trip_booking("KACOU MEITIALE Evelyne", "SL-40112", "Paris - Abidjan", "2025-04-12", "2025-04-26", 624.0, 1, "2025-01-05")
    c.doc("booking.md", text, doc_type="booking_confirmation", issuer="SkyLine Travel")
    c.doc("certificat.md", medical("Hopital Bichat, Paris", "Mme KACOU Evelyne", "2025-04-09", "acces palustre", lang="en"),
          doc_type="medical_report", issuer="Hopital Bichat", language="en")
    c.facts_from("booking.md", bf)
    c.fact("claimant_name", "description", "Evelyne Kacou Meitiale", "Evelyne Kacou Meitiale", 0.99)
    c.fact("claimant_name", "booking.md", "Lead passenger", "KACOU MEITIALE Evelyne", 0.95)
    c.fact("claimant_name", "certificat.md", "Patient:", "Mme KACOU Evelyne", 0.9)
    c.fact("incident_date", "certificat.md", "Admitted on 2025-04-09", "2025-04-09", 0.97)
    c.fact("affected", "description", "Evelyne Kacou Meitiale", 1, 0.8)
    return cancellation(c, "certificat.md", "Diagnosis: acces palustre", "Patient: Mme KACOU Evelyne")


def b02():
    c = Claim("b02-added-middle-name", "cfsr", """
Cristina Carhuayo Freire. My flight to Lima on 25 January 2025 was cancelled on my side: I was hospitalised with pneumonia on 23 January.
""", submitted="2025-02-03", expected={"decision": "APPROVE", "payout": 88.0},
        note="The booking carries the full name Cristina Vanessa Carhuayo Freire; the letter is signed without the middle name.")
    text, bf = trip_booking("Cristina Vanessa Carhuayo Freire", "SL-40177", "Cusco - Lima", "2025-01-25", None, 88.0, 1, "2024-11-04")
    c.doc("booking.md", text, doc_type="booking_confirmation", issuer="SkyLine Travel")
    c.doc("medical.md", medical("Clinica San Pablo, Cusco", "Cristina Vanessa Carhuayo Freire", "2025-01-23", "neumonia", lang="es"),
          doc_type="medical_report", issuer="Clinica San Pablo", language="es")
    c.facts_from("booking.md", bf)
    c.fact("claimant_name", "description", "Cristina Carhuayo Freire", "Cristina Carhuayo Freire", 0.99)
    c.fact("claimant_name", "booking.md", "Lead passenger", "Cristina Vanessa Carhuayo Freire", 0.95)
    c.fact("incident_date", "medical.md", "Fecha de ingreso: 2025-01-23", "2025-01-23", 0.97)
    c.fact("affected", "description", "Cristina Carhuayo Freire", 1, 0.8)
    return cancellation(c, "medical.md", "Diagnóstico: neumonia", "Paciente: Cristina Vanessa Carhuayo Freire")


def b03():
    c = Claim("b03-ill-the-day-before-departure", "cfsr", """
Amy Ndiaye. I was taken to the emergency department on 14 March 2025 with a kidney infection, the evening before my flight to Dakar on 15 March. I had to cancel.
""", submitted="2025-03-20", expected={"decision": "APPROVE", "payout": 489.0},
        note="No cover period is written. Cover runs from purchase (10 Feb) to the return (22 Mar): the day before departure is inside it.")
    text, bf = trip_booking("Amy NDIAYE", "SL-40230", "Lyon - Dakar", "2025-03-15", "2025-03-22", 489.0, 1, "2025-02-10")
    c.doc("booking.md", text, doc_type="booking_confirmation", issuer="SkyLine Travel")
    c.doc("certificate.md", medical("Hopital Edouard Herriot, Lyon", "Amy NDIAYE", "2025-03-14", "acute pyelonephritis"),
          doc_type="medical_report", issuer="Hopital Edouard Herriot")
    c.facts_from("booking.md", bf)
    c.fact("claimant_name", "description", "Amy Ndiaye", "Amy Ndiaye", 0.99)
    c.fact("claimant_name", "certificate.md", "Patient: Amy NDIAYE", "Amy NDIAYE", 0.95)
    c.fact("incident_date", "certificate.md", "Admitted on 2025-03-14", "2025-03-14", 0.97)
    c.fact("affected", "description", "Amy Ndiaye", 1, 0.8)
    return cancellation(c, "certificate.md", "Diagnosis: acute pyelonephritis", "Patient: Amy NDIAYE")


def b04():
    c = Claim("b04-ill-before-the-booking", "cfsr", """
Amy Ndiaye. I fell ill with a kidney infection on 30 January 2025 and I am still not fit to fly to Dakar on 15 March, so I cancelled.
""", submitted="2025-03-10", expected={"decision": "DENY"},
        note="Partner of b03: the same booking, but the illness started before the 10 February purchase, before cover began.")
    text, bf = trip_booking("Amy NDIAYE", "SL-40230", "Lyon - Dakar", "2025-03-15", "2025-03-22", 489.0, 1, "2025-02-10")
    c.doc("booking.md", text, doc_type="booking_confirmation", issuer="SkyLine Travel")
    c.doc("certificate.md", medical("Hopital Edouard Herriot, Lyon", "Amy NDIAYE", "2025-01-30", "acute pyelonephritis, relapsing"),
          doc_type="medical_report", issuer="Hopital Edouard Herriot")
    c.facts_from("booking.md", bf)
    c.fact("claimant_name", "description", "Amy Ndiaye", "Amy Ndiaye", 0.99)
    c.fact("incident_date", "certificate.md", "Admitted on 2025-01-30", "2025-01-30", 0.97)
    c.fact("incident_date", "description", "30 January 2025", "2025-01-30", 0.9)
    c.fact("affected", "description", "Amy Ndiaye", 1, 0.8)
    c.judge("prior_knowledge", "yes", "The illness began on 30 January, before the booking and its cover were bought on 10 February.",
            "certificate.md", "Admitted on 2025-01-30", confidence=0.9)
    return cancellation(c, "certificate.md", "Diagnosis: acute pyelonephritis", "Patient: Amy NDIAYE")


def b05():
    c = Claim("b05-certificate-as-webp", "cfsr", """
Louis Martin. Hospitalised on 3 June 2025 with a broken wrist, I had to cancel my trip to Porto on 5 June. Photo of the certificate attached.
""", submitted="2025-06-09", expected={"decision": "APPROVE", "payout": 312.0},
        note="The certificate is a real WebP photo: a local server must receive it converted, or the reader sees no image.")
    text, bf = trip_booking("Louis Martin", "SL-40311", "Nantes - Porto", "2025-06-05", "2025-06-09", 312.0, 1, "2025-04-02")
    lines = medical_photo("CHU de Nantes - Service des urgences", "M. Louis MARTIN", "03/06/2025", "fracture du poignet droit")
    c.doc("booking.md", text, doc_type="booking_confirmation", issuer="SkyLine Travel")
    c.doc("certificat medical.webp", image_lines=lines, image_format="WEBP", font_size=22,
          doc_type="medical_report", issuer="CHU de Nantes", language="fr")
    c.facts_from("booking.md", bf)
    c.fact("claimant_name", "description", "Louis Martin", "Louis Martin", 0.99)
    c.fact("incident_date", "certificat medical.webp", "le 03/06/2025", "2025-06-03", 0.9)
    c.fact("affected", "description", "Louis Martin", 1, 0.8)
    return cancellation(c, "certificat medical.webp", "Diagnostic : fracture du poignet droit", "examine M. Louis MARTIN")


def b06():
    c = Claim("b06-jpeg-named-webp", "cfsr", """
Lucia Gomez. Me atendieron de urgencia el 18 de mayo de 2025 por una apendicitis y no pude viajar a Madrid el 19 de mayo.
""", submitted="2025-05-25", expected={"decision": "APPROVE", "payout": 145.0},
        note="The certificate is a JPEG with a .webp name: it must be sent as the JPEG it is.")
    text, bf = trip_booking("Lucia Gomez", "SL-40388", "Valencia - Madrid", "2025-05-19", "2025-05-21", 145.0, 1, "2025-04-20")
    lines = medical_photo("Hospital Clinico Universitario de Valencia", "Lucia Gomez", "18/05/2025", "apendicitis aguda", lang="es")
    c.doc("booking.md", text, doc_type="booking_confirmation", issuer="SkyLine Travel")
    c.doc("Spanish_medical_9.webp", image_lines=lines, image_format="JPEG", font_size=22,
          doc_type="medical_report", issuer="Hospital Clinico Universitario de Valencia", language="es")
    c.facts_from("booking.md", bf)
    c.fact("claimant_name", "description", "Lucia Gomez", "Lucia Gomez", 0.99)
    c.fact("incident_date", "Spanish_medical_9.webp", "Fecha de atencion", "2025-05-18", 0.9)
    c.fact("affected", "description", "Lucia Gomez", 1, 0.8)
    return cancellation(c, "Spanish_medical_9.webp", "Diagnostico: apendicitis aguda", "Paciente: Lucia Gomez")


def b07():
    c = Claim("b07-certificate-for-someone-else", "cfsr", """
Marcos Junes. I missed my trip to Guayaquil on 20 September 2025 because of a medical emergency. The medical certificate is attached.
""", submitted="2025-09-24", expected={"decision": "DENY"},
        note="The certificate is for a different person who is not on the booking: the proof does not fit, and the documents contradict each other.")
    text, bf = trip_booking("Marcos Junes", "SL-40420", "Quito - Guayaquil", "2025-09-20", "2025-09-23", 45.0, 1, "2025-09-14")
    lines = medical_photo("Hospital Metropolitano, Quito", "Yacaro Tuano", "19/09/2025", "gastroenteritis aguda", lang="es")
    c.doc("booking.md", text, doc_type="booking_confirmation", issuer="SkyLine Travel")
    c.doc("certificado.webp", image_lines=lines, image_format="WEBP", font_size=22,
          doc_type="medical_report", issuer="Hospital Metropolitano", language="es")
    c.facts_from("booking.md", bf)
    c.fact("claimant_name", "description", "Marcos Junes", "Marcos Junes", 0.99)
    c.fact("incident_date", "certificado.webp", "Fecha de atencion", "2025-09-19", 0.9)
    c.fact("affected", "description", "Marcos Junes", 1, 0.8)
    c.judge("cancellation_reason", "yes", "A medical emergency is claimed.", "description", "medical emergency")
    c.judge("cancellation_proof_fits", "no", "The certificate is for Yacaro Tuano, who is neither the claimant nor a traveller on the booking.",
            "certificado.webp", "Paciente: Yacaro Tuano")
    c.judge("suspicious_documents", "yes", "The certificate names a different person from the claimant and the booking.",
            "certificado.webp", "Paciente: Yacaro Tuano")
    return c


def b08():
    c = Claim("b08-certificate-for-travelling-child", "cfsr", """
Laura Bianchi. My daughter Sofia (7) was admitted to hospital with appendicitis on 2 August 2025, so we cancelled our holiday to Crete on 4 August.
""", submitted="2025-08-07", expected={"decision": "APPROVE", "payout": 1380.0},
        note="Partner of b07: the certificate names someone else, but she is the travelling child on the booking.")
    text, bf = trip_booking("Laura Bianchi", "SL-40501", "Milan - Heraklion", "2025-08-04", "2025-08-14", 1380.0, 2, "2025-05-30",
                            travellers="Laura Bianchi, Sofia Bianchi (child, 7)")
    c.doc("booking.md", text, doc_type="booking_confirmation", issuer="SkyLine Travel")
    c.doc("certificate.md", medical("Ospedale Niguarda, Milano", "Sofia Bianchi (age 7)", "2025-08-02", "acute appendicitis"),
          doc_type="medical_report", issuer="Ospedale Niguarda")
    c.facts_from("booking.md", bf)
    c.fact("claimant_name", "description", "Laura Bianchi", "Laura Bianchi", 0.99)
    c.fact("claimant_name", "booking.md", "Lead passenger", "Laura Bianchi", 0.95)
    c.fact("incident_date", "certificate.md", "Admitted on 2025-08-02", "2025-08-02", 0.97)
    c.fact("affected", "description", "My daughter Sofia", 2, 0.8)
    return cancellation(c, "certificate.md", "Diagnosis: acute appendicitis", "Sofia Bianchi (age 7)")


def b09():
    c = Claim("b09-one-altered-digit", "cfsr", """
Ines Costa. Appendix surgery on 10 March 2025, I cancelled my trip to Athens on 12 March. Attached: hospital letter and a photo of my booking.
""", submitted="2025-03-18", expected={"decision": "UNCERTAIN"},
        note="One alteration sign: the booking still counts as proof, but the red flag refers the otherwise approvable claim to a human.")
    c.doc("hospital-letter.md", medical("Hospital de Santa Maria", "Ines Costa", "2025-03-10", "appendicitis", "2025-03-13"),
          doc_type="medical_report", issuer="Hospital de Santa Maria")
    c.doc("booking-photo.png", image_lines=[
        "Booking confirmation SL-40588", "SkyLine Travel", "Lead passenger: Ines Costa", "Route: Lisbon - Athens",
        "Outbound: 2025-03-12", "Return: 2025-03-19", "Passengers: 1", "Total paid: EUR 2,780.00",
        "Booked and paid on 2025-02-01"], font_size=22,
        doc_type="booking_confirmation", issuer="SkyLine Travel",
        alteration_signs=["the first digit of the total amount is in a different weight from the rest of the line"])
    c.fact("claimant_name", "description", "Ines Costa", "Ines Costa", 0.99)
    c.fact("incident_date", "hospital-letter.md", "Admitted on 2025-03-10", "2025-03-10", 0.97)
    c.fact("booking_value", "booking-photo.png", "Total paid", 2780.0, 0.7)
    c.fact("passengers", "booking-photo.png", "Passengers: 1", 1, 0.9)
    c.fact("purchase_date", "booking-photo.png", "Booked and paid on", "2025-02-01", 0.9)
    c.fact("policy_period", "booking-photo.png", "Booked and paid on", {"start": "2025-02-01", "end": "2025-03-19"}, 0.8)
    c.fact("affected", "description", "Ines Costa", 1, 0.8)
    c.judge("suspicious_documents", "yes", "The first digit of the booking total looks retyped.", "booking-photo.png", "Total paid")
    return cancellation(c, "hospital-letter.md", "Diagnosis: appendicitis", "Patient: Ines Costa")


def b10():
    c = Claim("b10-letter-dated-late", "cfsr", """
Nantes, 30 May 2025
Claire Petit. I was admitted to hospital with appendicitis on 2 April 2025 and could not take my flight to Rome on 4 April (return 12 April). Please refund the trip.
""", submitted=None, expected={"decision": "DENY"},
        note="No submission date in the metadata, as in the real claims. The letter is dated 30 May, 48 days after the trip ended.")
    text, bf = trip_booking("Claire Petit", "SL-40640", "Nantes - Rome", "2025-04-04", "2025-04-12", 520.0, 1, "2025-02-15")
    c.doc("booking.md", text, doc_type="booking_confirmation", issuer="SkyLine Travel")
    c.doc("certificate.md", medical("CHU de Nantes", "Claire Petit", "2025-04-02", "acute appendicitis"),
          doc_type="medical_report", issuer="CHU de Nantes")
    c.facts_from("booking.md", bf)
    c.fact("claimant_name", "description", "Claire Petit", "Claire Petit", 0.99)
    c.fact("claim_submitted", "description", "Nantes, 30 May 2025", "2025-05-30", 0.85)
    c.fact("incident_date", "certificate.md", "Admitted on 2025-04-02", "2025-04-02", 0.97)
    c.fact("affected", "description", "Claire Petit", 1, 0.8)
    return cancellation(c, "certificate.md", "Diagnosis: acute appendicitis", "Patient: Claire Petit")


def b11():
    c = Claim("b11-known-condition-relapse", "cfsr", """
Paul Weber. My back gave out on 2 March 2025 and I could not fly to Tenerife on 6 March. Medical report attached.
""", submitted="2025-03-08", expected={"decision": "DENY"},
        note="Prior knowledge without the word: the report says he has been treated for the condition since November 2024; the trip was bought in January.")
    text, bf = trip_booking("Paul Weber", "SL-40702", "Cologne - Tenerife", "2025-03-06", "2025-03-13", 860.0, 1, "2025-01-10")
    c.doc("booking.md", text, doc_type="booking_confirmation", issuer="SkyLine Travel")
    c.doc("medical-report.md", """# Medical report
Orthopaedic practice Dr. S. Brandt, Cologne
Patient: Paul Weber
Under treatment since 2024-11-18 for a recurrent lumbar disc herniation (L4/L5).
Acute relapse on 2025-03-02, strict rest for three weeks.
Not fit to travel until further notice.""", doc_type="medical_report", issuer="Dr. S. Brandt")
    c.facts_from("booking.md", bf)
    c.fact("claimant_name", "description", "Paul Weber", "Paul Weber", 0.99)
    c.fact("incident_date", "medical-report.md", "Acute relapse on 2025-03-02", "2025-03-02", 0.95)
    c.fact("affected", "description", "Paul Weber", 1, 0.8)
    c.judge("prior_knowledge", "yes", "He has been treated for this condition since 18 November 2024, before buying the trip on 10 January.",
            "medical-report.md", "Under treatment since 2024-11-18", confidence=0.85)
    return cancellation(c, "medical-report.md", "Acute relapse on 2025-03-02", "Patient: Paul Weber")


def b12():
    c = Claim("b12-missed-departure-not-cancellation", "cfsr", """
Jan and Eva Novak. On 9 June 2025 our taxi to the airport was hit by another car and we missed our flight to Split. We took a later flight the next day. Police report and booking attached.
""", submitted="2025-06-15", expected={"decision": "APPROVE", "payout": 240.0},
        note="A missed departure, not a cancellation: only the formula applies, 0.5 x 480 / 2 x 2 = 240. A model that also matches cancellation doubles the payout.")
    text, bf = trip_booking("Jan Novak", "SL-40766", "Prague - Split", "2025-06-09", "2025-06-16", 480.0, 2, "2025-03-01",
                            travellers="Jan Novak, Eva Novak")
    c.doc("booking.md", text, doc_type="booking_confirmation", issuer="SkyLine Travel")
    c.doc("police-report.md", police("Policie CR, Praha 6", "Jan Novak", "2025-06-09",
                                     "road traffic collision, taxi carrying two passengers to Vaclav Havel Airport", "PCR-2025-06-0912"),
          doc_type="police_report", issuer="Policie CR")
    c.facts_from("booking.md", bf)
    c.fact("claimant_name", "description", "Jan and Eva Novak", "Jan Novak", 0.9)
    c.fact("incident_date", "police-report.md", "Date of incident: 2025-06-09", "2025-06-09", 0.97)
    c.fact("affected", "description", "Jan and Eva Novak", 2, 0.9)
    c.judge("cancellation_reason", "no", "The trip was not cancelled: they missed the departure and flew the next day.",
            "description", "We took a later flight the next day")
    c.judge("missed_departure", "yes", "A traffic collision on the way to the airport made them miss the flight.",
            "police-report.md", "road traffic collision")
    return c


def b13():
    c = Claim("b13-submission-date-only-in-stamp", "cfsr", """
Hannah Vogel. I was admitted to hospital with a severe kidney stone on 6 April 2025 and could not fly to Vienna on 7 April.
""", submitted=None, expected={"decision": "APPROVE", "payout": 275.0},
        note="As in the real claims: no submission date in the metadata or the letter, only the system's "
             "'Current date is' stamp on the booking record (11 days after the trip).")
    text, bf = trip_booking("Hannah Vogel", "SL-40815", "Hamburg - Vienna", "2025-04-07", "2025-04-10", 275.0, 1, "2025-03-01",
                            stamp="2025-04-21")
    c.doc("internal flight data.md", text, doc_type="booking_confirmation", issuer="SkyLine Travel")
    c.doc("certificate.md", medical("Asklepios Klinik Altona, Hamburg", "Hannah Vogel", "2025-04-06", "renal colic, kidney stone"),
          doc_type="medical_report", issuer="Asklepios Klinik Altona")
    c.facts_from("internal flight data.md", bf)
    c.fact("claim_submitted", "internal flight data.md", "Current date is: 2025-04-21", "2025-04-21", 0.9)
    c.fact("claimant_name", "description", "Hannah Vogel", "Hannah Vogel", 0.99)
    c.fact("incident_date", "certificate.md", "Admitted on 2025-04-06", "2025-04-06", 0.97)
    c.fact("affected", "description", "Hannah Vogel", 1, 0.8)
    return cancellation(c, "certificate.md", "Diagnosis: renal colic, kidney stone", "Patient: Hannah Vogel")


def b14():
    c = Claim("b14-booking-already-refunded", "cfsr", """
Lena Fischer. I broke my ankle on 2 April 2025 and could not travel to Lisbon on 5 April. Booking and doctor's note attached.
""", submitted="2025-04-09", expected={"decision": "UNCERTAIN"},
        note="Everything the rules check is in order, but the booking confirmation also says the agency already "
             "refunded the trip in full. No rule looks for that; the reader must flag it so a person sees it.")
    text, bf = booking("Lena Fischer", "ET-78120", "Munich - Lisbon", "2025-04-05", 520.0, 1, "2025-02-20", COVER,
                       extra="Status: CANCELLED by customer on 2025-04-03. Full refund of EUR 520.00 issued to the original card.")
    c.doc("booking-confirmation.md", text, doc_type="booking_confirmation", issuer="EuroTrips Travel GmbH",
          review="The booking says the trip was cancelled and refunded in full (EUR 520.00) on 2025-04-03.")
    c.doc("doctor-note.md", medical("Klinikum rechts der Isar, Munich", "Lena Fischer", "2025-04-02", "fractured right ankle"),
          doc_type="medical_report", issuer="Klinikum rechts der Isar")
    c.facts_from("booking-confirmation.md", bf)
    c.fact("claimant_name", "description", "Lena Fischer", "Lena Fischer", 0.99)
    c.fact("incident_date", "doctor-note.md", "Admitted on 2025-04-02", "2025-04-02", 0.97)
    c.fact("affected", "description", "Lena Fischer", 1, 0.8)
    return cancellation(c, "doctor-note.md", "Diagnosis: fractured right ankle", "Patient: Lena Fischer")


def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir()
    (ROOT / "README.md").write_text(
        "# Mock claims\n\nGenerated by `scripts/make_mockclaims.py`; edit that script, not these files.\n"
        "Each folder is one adversarial case: `description.txt`, the submitted documents, `claim.json`\n"
        "(policy, submission date, what the case tests), `oracle.json` (what the model would have answered)\n"
        "and `expected.json` (the required decision and payout).\n\n"
        "`01`-`18` and `g01`-`g04` test one rule each, with tidy documents. The `b` claims are shaped like\n"
        "the real claims and target what the first blind run got wrong: no stated cover period, names\n"
        "written differently on each document, certificates as WebP photos or as JPEGs named `.webp`, a\n"
        "certificate for someone else, one altered digit, a letter dated late with no submission date,\n"
        "an unstated prior condition, a missed departure that is not a cancellation, a submission date\n"
        "found only in the intake system's `Current date is` stamp, a booking that says it was already\n"
        "refunded (the reader must flag what no rule looks for). Most come in pairs\n"
        "that differ in one detail, so the strategies can be compared claim by claim.\n")
    all_claims = [c01(), c02(), c03(), c04(), c05(), c06(), c07(), c08(), c09(), c10(), c11(), c12(), c13(),
                  c14(), c15(), c16(), c17(), c18(), g01(), g02(), g03(), g04(),
                  b01(), b02(), b03(), b04(), b05(), b06(), b07(), b08(), b09(), b10(), b11(), b12(), b13(), b14()]
    for c in all_claims:
        c.write(ROOT, CFSR_JUDGMENTS if c.policy == "cfsr" else GADGET_JUDGMENTS)
    print(f"wrote {len(all_claims)} mock claims to {ROOT}")


if __name__ == "__main__":
    main()
