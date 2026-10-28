# Mock claims (scripted backend)

**Decisions:** 22 / 22 correct (100.0%).  **Payouts:** 9 / 9 exact.  **Model cost:** $0.0000.

## Confusion matrix (rows: expected, columns: actual)

| expected \ actual | APPROVE | DENY | UNCERTAIN |
|---|---|---|---|
| APPROVE | 9 | 0 | 0 |
| DENY | 0 | 9 | 0 |
| UNCERTAIN | 0 | 0 | 4 |

## Per outcome

| outcome | expected | predicted | precision | recall |
|---|---|---|---|---|
| APPROVE | 9 | 9 | 1.0 | 1.0 |
| DENY | 9 | 9 | 1.0 | 1.0 |
| UNCERTAIN | 4 | 4 | 1.0 | 1.0 |

## Model calls per component

| component | calls | input tok | cache read | output tok | cost USD | latency ms | errors |
|---|---|---|---|---|---|---|---|
| read_document | 42 | 0 | 0 | 0 | 0.0000 | 0 | 0 |
| extract_facts | 22 | 0 | 0 | 0 | 0.0000 | 0 | 0 |
| judge | 114 | 0 | 0 | 0 | 0.0000 | 0 | 0 |
| explain | 22 | 0 | 0 | 0 | 0.0000 | 0 | 0 |

## Claims

| claim | expected | actual | ok | payout exp | payout act | confidence | summary |
|---|---|---|---|---|---|---|---|
| 01-clean-medical-cancellation | APPROVE | APPROVE | yes | 1,240.00 | 1,240.00 | 0.90 | Approved under Trip Cancellation or Rescheduling. Payout 1,240.00 EUR. |
| 02-incident-before-policy-start | DENY | DENY | yes | - | - | 0.95 | Denied. Incident date 2025-01-28 (medical-certificate.md) is outside policy period 2025-02-01 to 2025-04-10 (booking-confirmation.md). |
| 03-late-reporting-31-days | DENY | DENY | yes | - | - | 0.95 | Denied. Claim submitted 2025-05-11 (metadata) is 31 days after policy period end 2025-04-10 (booking-confirmation.md); limit is 30. |
| 04-late-reporting-boundary-30-days | APPROVE | APPROVE | yes | 390.00 | 390.00 | 0.90 | Approved under Trip Cancellation or Rescheduling. Payout 390.00 EUR. |
| 05-theft-without-police-report | DENY | DENY | yes | - | - | 1.00 | Denied. Medical_report: missing; police_report: missing; jury_summons: missing; booking_confirmation: booking-confirmation.md. The only s... |
| 06-incident-dates-disagree | DENY | DENY | yes | - | - | 0.90 | Denied. The claimant's dates contradict the certificate: illness on 20 March vs admission 12 March and discharge 19 March. |
| 07-policy-period-unknown | UNCERTAIN | UNCERTAIN | yes | - | - | 0.90 | Referred for review. The claim is otherwise eligible; the open exclusion(s) decide the outcome. |
| 08-altered-booking-image | DENY | DENY | yes | - | - | 0.90 | Denied. The booking photo shows a retyped total and an overwritten date digit. |
| 09-prior-knowledge | DENY | DENY | yes | - | - | 0.95 | Denied. The surgery was scheduled on 20 January, twelve days before the cover was bought on 1 February. |
| 10-voluntary-change | DENY | DENY | yes | - | - | 0.90 | Denied. The stated reason is not covered by any section of the policy. A friend dropping out is a voluntary change of plans, not a listed... |
| 11-personal-effects-two-people | APPROVE | APPROVE | yes | 200.00 | 200.00 | 0.90 | Approved under Personal Effects. Payout 200.00 EUR. |
| 12-missed-connection-formula | APPROVE | APPROVE | yes | 200.00 | 200.00 | 0.90 | Approved under Missed Departure or Missed Connection. Payout 200.00 EUR. |
| 13-missed-connection-passengers-unknown | UNCERTAIN | UNCERTAIN | yes | - | - | 0.90 | Referred for review. Payout for Missed Departure or Missed Connection: cannot compute payout, missing: passengers. |
| 14-jury-duty | APPROVE | APPROVE | yes | 560.00 | 560.00 | 0.90 | Approved under Trip Cancellation or Rescheduling. Payout 560.00 EUR. |
| 15-spanish-medical-certificate | APPROVE | APPROVE | yes | 430.00 | 430.00 | 0.90 | Approved under Trip Cancellation or Rescheduling. Payout 430.00 EUR. |
| 16-theft-causing-missed-train | APPROVE | APPROVE | yes | 250.00 | 250.00 | 0.90 | Approved under Personal Effects, Missed Departure or Missed Connection. Payout 250.00 EUR. |
| 17-weather-cancellation | UNCERTAIN | UNCERTAIN | yes | - | - | 0.90 | Referred for review. Was the trip cancelled or rescheduled because of jury duty, a medical emergency, a theft or criminal incident, or an... |
| 18-unreadable-police-report | UNCERTAIN | UNCERTAIN | yes | - | - | 0.85 | Referred for review. Required proof: (medical_report or police_report or jury_summons) and booking_confirmation: medical_report: missing,... |
| g01-theft-clean | APPROVE | APPROVE | yes | 950.00 | 950.00 | 0.90 | Approved under Theft. Payout 950.00 EUR. |
| g02-theft-reported-late | DENY | DENY | yes | - | - | 0.97 | Denied. Claim submitted 2025-06-14 (metadata) is 4 days after incident date 2025-06-10 (police-report.md); limit is 2. |
| g03-accidental-damage | APPROVE | APPROVE | yes | 130.00 | 130.00 | 0.90 | Approved under Accidental Damage. Payout 130.00 EUR. |
| g04-left-unattended | DENY | DENY | yes | - | - | 0.95 | Denied. The claimant left the laptop on the table and walked away. |
