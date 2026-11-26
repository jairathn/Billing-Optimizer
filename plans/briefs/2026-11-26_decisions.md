# Unit decisions: the owner's decisions 2, 3 and 4 of 2026-11-26 (HANDOFF section 3b)

Decision 1 (Vercel analyses only on a press) is built by the hook unit. This unit builds the other three, on branch
`wip/decisions` from the integration tip.

## Decision 2: rule 8, patient-group denials that are not deductible, coinsurance or copay
The owner: "Needs to bill the patient. But can be marked done too if user desires."
- Today archive_rules.py rule 8 raises an item for a line the payer answered with PR-288 (referral absent), PR-27 (coverage
  ended), PR-26 (before coverage), PR-200 (lapse), PR-49 (routine service) or PR-204 (not covered by the plan), and for
  CO-147; about 58 to 66 such items ($4.2k) sit on Work now waiting on this ruling.
- For the patient-group reasons: the item stays on Work now with the next step "Bill the patient $X", X being the amount
  the payer put on the patient for that line (never more than the charge less what any payer paid). For PR-27, PR-26 and
  PR-200 the step also says to ask the patient for current insurance (a newer plan pays more than a patient does, and
  the claim then goes to that plan within its filing window). For PR-288 it also offers getting a retro referral from the
  primary care office and resubmitting.
- Two actions: "Billed the patient" (the item is worked: it leaves Work now, its history says when and who, and it waits
  for EMA's patient payment, closing as paid when EMA shows it) and "Done" (counts it done, with who and when). Both are
  a person's decision: no run ever overwrites them. They follow the hotfix guarantees (act once on their own item,
  seen_status, strict JSON true for any override).
- CO-147 is a contractual reason, never billed to the patient: unchanged.
- Medicare and Medicaid: a patient-group reason from these payers follows the same rule (the payer itself assigned the
  amount to the patient). Do not invent ABN logic.

## Decision 3: the recovery when a payer denies as primary and later pays as secondary
The owner: "whatever you think is best for getting our money." Decided: the recovered amount is what actually arrived, in
both places that record it today (archive rule 3 sets recovered_cents to 0 when counts_as_recovery is false; the
settled-as-secondary closure in detect.py records the payment), and the Results tab attributes it like any recovery. If
what arrived is less than the service is owed (the payer's allowed amount less the other payers' payments and the
patient's share, sized as the item would be), the remainder stays listed (no dollar floor, PLAN decision 9) with its proof.

## Decision 4: codes a payer almost never pays (rule 4.2)
The owner: "Keep pursuing them and find the reason they aren't paying, so that we can adapt our notes to get them paid if
possible."
- Nothing is accepted by default (already true: fold_payer_policy defaults to "pursue"); keep it so, and make sure the 20
  code-change items ($7,150) of this kind and every payer-policy pattern stay pursued.
- Research (a separate agent, with the private world): for each payer and code on that list, from the remittances and
  EMA: lines answered, paid, the denial reasons (CARC and RARC), modifiers, place of service, the codes billed with it, the
  diagnoses of paid and unpaid lines where the 837s carry them; then the payer's own published rule for that code (CMS
  manuals, NCCI, the LCD or article, the payer's medical or reimbursement policy) with its exact title, link, date and the
  quoted sentence, each checked twice. Verdict per pair: fixable by the note (what it must show), fixable by the claim
  (modifier, code, diagnosis order, a separate claim), or never payable under the rule (say which rule). Facts about payers
  only in the repository: no practice data in the data file.
- Product: each item of such a pair shows "Why this payer does not pay this code" with the evidence from the store (computed
  live, not stored in the repository) and the researched rule with its source; "What the note or claim must show" goes
  into the appeal packet's checklist and the note review's prompt input; a never-payable verdict says so plainly and keeps
  the item pursued until the owner accepts the pattern on the policy page.
