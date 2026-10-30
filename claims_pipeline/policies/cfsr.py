"""Cancellation For Specific Reason (CFSR) - the benchmark policy.

Read this file against texts/cfsr.md: each coverage and exclusion below is one
clause there.  Anything only this policy needs stays in this file.
"""
from pathlib import Path

from claims_pipeline.components import (
    Coverage, Date, Int, Money, Name, Period, Policy,
    agrees_across_documents, all_of, any_of, days_between_at_most, formula, judged, within_period,
)

TEXT = (Path(__file__).parent / "texts" / "cfsr.md").read_text()

cfsr = Policy(
    name="cfsr",
    title="Cancellation For Specific Reason",
    text=TEXT,
    facts=[
        Name("claimant_name", "full name of the person claiming"),
        Date("incident_date", "the date the covered event happened: illness onset or admission, theft, accident, or the court date on a summons"),
        Date("purchase_date", "the date the policy (or the booking it came with) was bought"),
        # A cancellation policy covers from purchase until the trip is over: an illness the day
        # before departure is inside the period, not outside it.
        Period("policy_period", "the dates the cover runs. If a policy period is written, use it. Otherwise the "
                                "cover starts on the purchase (booking) date and ends on the last day of the trip "
                                "(return flight, check-out, or the travel date of a one-way trip)"),
        Date("claim_submitted", "the date the claim was submitted; a \"Current date is\" line at the top of a "
                                "document is our system's submission stamp"),
        Money("booking_value", "the total amount paid for the booking, in EUR"),
        Int("passengers", "number of passengers on the booking"),
        Int("affected", "number of people affected by the incident (default 1 if clearly a single traveller)"),
    ],
    doc_types={
        "medical_report": "medical certificate, hospital letter, discharge summary, doctor's note",
        "police_report": "police report or crime reference from any police force",
        "jury_summons": "jury duty summons or court order to appear",
        "incident_report": "accident, breakdown, airline or rail incident report explaining a delay",
        "airline_acknowledgement": "airline or carrier acknowledgement of lost or damaged baggage",
        "booking_confirmation": "booking confirmation, itinerary, ticket, boarding pass, or payment receipt for the trip",
        "other": "anything that does not fit the types above",
        "unreadable": "a document whose content cannot be read",
    },
    coverages=[
        Coverage(
            name="cancellation",
            title="Trip Cancellation or Rescheduling",
            matches=judged("cancellation_reason",
                           "Was the trip cancelled or rescheduled because of jury duty, a medical emergency, "
                           "a theft or criminal incident, or another specified personal emergency?"),
            proofs=all_of(any_of("medical_report", "police_report", "jury_summons"), "booking_confirmation"),
            checks=[
                judged("cancellation_proof_fits",
                       "Does the supporting document confirm the stated reason, name the claimant (or a travelling companion), "
                       "and cover the incident date?", facts=("incident_date", "claimant_name")),
                agrees_across_documents("incident_date"),
            ],
            payout=formula("booking_value"),  # "up to the total bill amount"
        ),
        Coverage(
            name="personal_effects",
            title="Personal Effects",
            matches=judged("effects_lost", "Were the claimant's personal belongings lost, stolen or damaged during the trip?"),
            proofs=any_of("police_report", "airline_acknowledgement"),
            payout=formula("100 * affected"),
        ),
        Coverage(
            name="missed_connection",
            title="Missed Departure or Missed Connection",
            matches=judged("missed_departure",
                           "Did the claimant miss a scheduled departure or connection because of an external covered cause, "
                           "such as a traffic accident en route?"),
            proofs=all_of(any_of("incident_report", "police_report", "medical_report"), "booking_confirmation"),
            payout=formula("0.5 * booking_value / passengers * affected"),
        ),
    ],
    exclusions=[
        judged("prior_knowledge",
               "Did the claimant know about the incident, or the reason for it, before purchasing the policy?",
               expect="no", facts=("incident_date", "purchase_date")),
        within_period("incident_date", "policy_period"),
        days_between_at_most("policy_period.end", "claim_submitted", 30,
                             id="late_reporting", description="claim submitted within 30 days of the policy ending"),
    ],
    # Not grounds for denial in the wording: a suspicious document refers the claim to a human.
    red_flags=[
        judged("suspicious_documents",
               "Do any of the documents show signs of being altered or fabricated, or contradict each other on a "
               "material point (who, when, what happened, how much)? Differences in how a name is written "
               "(capitals, word order, a middle or second family name, accents) are not contradictions.",
               expect="no"),
    ],
)
