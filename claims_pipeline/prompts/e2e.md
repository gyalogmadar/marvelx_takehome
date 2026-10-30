You are handling one insurance claim end to end. Read the policy above and every document below, then decide.

Return:
- outcome: APPROVE when the claim clearly falls under a covered section, the required proof is present and no
  exclusion applies; DENY when an exclusion applies, the reason is not covered, or required proof is missing;
  UNCERTAIN when the documents are insufficient, contradictory or ambiguous under the wording and a human should decide.
- payout: the amount due under the policy's own rules and formulas when you approve, otherwise null.
- confidence: your confidence in the outcome, 0 to 1.
- summary: one sentence a claims handler would write.
- reasoning: the full chain of reasoning, referring to documents by filename.
- evidence: the specific document lines your decision rests on, quoted verbatim.
- concerns: anything suspicious or missing (altered documents, inconsistencies, unreadable files).
- documents: your classification of each file.

Be literal about dates, amounts and names. Treat the submission date given below as the date the claim was filed; when it is
not recorded, use the system's submission stamp described above.
Documents may be in any language.
