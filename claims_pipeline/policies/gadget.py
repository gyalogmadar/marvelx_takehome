"""Gadget Cover - a second, deliberately different policy.

It exists to show that a new policy is a new file of this size and nothing
else: an excess, a cap, a 48-hour reporting window, no trip concept.
"""
from pathlib import Path

from claims_pipeline.components import (
    Coverage, Date, Money, Period, Policy, Text, all_of, days_between_at_most, formula, judged, within_period,
)

TEXT = (Path(__file__).parent / "texts" / "gadget.md").read_text()

gadget = Policy(
    name="gadget",
    title="Gadget Cover (demo)",
    text=TEXT,
    facts=[
        Text("claimant_name", "full name of the policyholder"),
        Date("incident_date", "the date the device was stolen or damaged"),
        Period("policy_period", "the policy start and end dates"),
        Date("claim_submitted", "the date the claim was submitted"),
        Money("item_value", "the purchase price of the device, in EUR"),
        Money("repair_cost", "the quoted repair cost, in EUR"),
    ],
    doc_types={
        "police_report": "police report or crime reference",
        "purchase_receipt": "receipt or invoice proving purchase of the device",
        "repair_quote": "a repair shop's quote or invoice",
        "other": "anything else",
        "unreadable": "a document whose content cannot be read",
    },
    coverages=[
        Coverage(
            name="theft",
            title="Theft",
            matches=judged("device_stolen", "Was the insured device stolen?"),
            proofs=all_of("police_report", "purchase_receipt"),
            payout=formula("max(0, min(item_value, 1000) - 50)"),
        ),
        Coverage(
            name="accidental_damage",
            title="Accidental Damage",
            matches=judged("device_damaged", "Was the insured device accidentally damaged?"),
            proofs=all_of("repair_quote", "purchase_receipt"),
            payout=formula("max(0, min(repair_cost, item_value) - 50)"),
        ),
    ],
    exclusions=[
        within_period("incident_date", "policy_period"),
        days_between_at_most("incident_date", "claim_submitted", 2, id="late_reporting",
                             description="claim reported within 48 hours of the incident"),
        judged("unattended", "Was the device left unattended in a public place when the incident happened?", expect="no"),
    ],
)
