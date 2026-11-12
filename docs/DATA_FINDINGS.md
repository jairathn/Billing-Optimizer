# What the remittances say: where Owensboro Dermatology is losing money

Generated 2026-11-12 from 32,550 unique 835 remittances (192,851 claims, 419,023 service lines), payment dates 2024-01-01 to 2026-10-09, $29,966,776 paid. Detection version 2.1. Dollar figures are the allowed-amount shortfall against the payer's own usual rate (or Medicare where noted), not charges. A finding stays open until a later remittance pays it or a biller marks it.

## The one-paragraph answer

Across the whole archive the detectable, still-unrecovered leakage is about $2,404,188. Classic E/M downcoding, the thing the practice asked about, is $51,806 of it. The money is in bundling ($162,148), denials that were never worked ($633,356), contract rates below Medicare ($897,393 at Medicare parity), underpayment against the payer's own rate ($128,464), and fees plus takebacks ($531,020). A further $1,158,616 across 5,574 lines was initially denied or bundled and then paid on a later remittance, which is the rework the billers did do; those are marked resolved and excluded from the open figures.

## By year and category

Open findings only (resolved and dismissed excluded).

| year | downcoding | bundling | denial | underpayment | fees | contract | total |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2023 | $816 | $3,868 | $88,733 | $2,274 | $0 | $5,515 | $101,205 |
| 2024 | $2,010 | $53,519 | $233,874 | $39,827 | $72,267 | $393,948 | $795,444 |
| 2025 | $3,507 | $34,635 | $204,000 | $56,593 | $197,789 | $278,325 | $774,849 |
| 2026 | $45,474 | $70,126 | $104,387 | $29,751 | $260,964 | $219,189 | $729,892 |

## By signal

| signal | category | what to do | open findings | open dollars | paid later (rework) |
|---|---|---|---:|---:|---:|
| CONTRACT_RATE_GAP | contract | renegotiate | 53 | $633,418 | $0 |
| TAKEBACK | fees | review | 141 | $462,040 | $0 |
| MA_BELOW_MEDICARE | contract | review | 133 | $263,559 | $0 |
| UNDERPAID_VS_OWN_RATE | underpayment | appeal | 2,487 | $124,160 | $0 |
| DENIED_NONCOVERED | denial | review | 1,035 | $107,314 | $99,923 |
| DENIED_AUTH | denial | appeal | 756 | $94,096 | $21,442 |
| DENIED_MEDICAL_NECESSITY | denial | appeal | 692 | $88,674 | $193,020 |
| DENIED_DUPLICATE_UNPAID | denial | review | 373 | $70,566 | $0 |
| FEE_SKIM | fees | opt_out | 1,143 | $68,980 | $0 |
| DENIED_MISSING_INFO | denial | correct_and_resubmit | 483 | $60,053 | $156,166 |
| EM_BUNDLED_MOD57_MAJOR | bundling | appeal | 749 | $60,011 | $36,658 |
| PROCEDURE_BUNDLED | bundling | appeal | 244 | $50,943 | $157,806 |
| CODE_CHANGED | downcoding | review | 164 | $44,365 | $0 |
| DENIED_FREQUENCY | denial | appeal | 325 | $42,427 | $51,093 |
| CLAIM_DENIED_UNPAID | denial | review | 332 | $41,384 | $0 |
| EM_BUNDLED_MOD25 | bundling | appeal | 597 | $38,527 | $39,443 |
| CLAIM_DENIED_UNPAID | denial | appeal | 841 | $35,497 | $0 |
| CLAIM_DENIED_UNPAID | denial | correct_and_resubmit | 147 | $23,355 | $0 |
| DENIED_TIMELY_FILING | denial | review | 143 | $19,872 | $1,053 |
| DENIED_MISSING_MODIFIER | denial | correct_and_resubmit | 212 | $18,887 | $153,995 |
| DENIED_PROVIDER | denial | review | 78 | $10,356 | $17,428 |
| DENIED_CODING | denial | correct_and_resubmit | 66 | $6,787 | $321,950 |
| DENIED_LEVEL_OF_SERVICE | denial | appeal | 18 | $5,406 | $19,433 |
| DENIED_MISSING_PRIMARY | denial | review | 41 | $4,620 | $27,524 |
| MPPR_EXCESS | underpayment | appeal | 140 | $4,304 | $0 |
| DENIED_OTHER | denial | review | 37 | $4,063 | $882 |
| DOWNCODE_SVC06 | downcoding | appeal | 151 | $3,898 | $0 |
| EM_BUNDLED_MOD57_MINOR | bundling | correct_and_resubmit | 38 | $3,788 | $778 |
| EM_BUNDLED_MOD24 | bundling | appeal | 48 | $3,230 | $1,778 |
| EM_BUNDLED_NO_MODIFIER | bundling | correct_and_resubmit | 40 | $2,923 | $8,090 |
| ADDON_BUNDLED | bundling | appeal | 24 | $2,727 | $21,402 |
| DOWNCODE_LEVEL_REDUCED | downcoding | appeal | 55 | $1,826 | $29 |
| DOWNCODE_LEVEL_DENIED | downcoding | appeal | 17 | $1,672 | $194 |
| MPPR_POLICY | contract | renegotiate | 1 | $416 | $0 |
| DOWNCODE_CHARGE | downcoding | appeal | 1 | $24 | $0 |
| DOWNCODE_RATE | downcoding | appeal | 2 | $21 | $0 |

## By payer

| payer | kind | findings | dollars | of which appealable now |
|---|---|---:|---:|---:|
| Anthem / BCBS | commercial | 2,546 | $823,884 | $83,874 |
| Medicare KY (CGS J15) | medicare | 1,299 | $487,500 | $12,165 |
| Humana Medicare Advantage | medicare_advantage | 1,705 | $357,931 | $95,295 |
| UnitedHealthcare Medicare Advantage | medicare_advantage | 1,126 | $208,082 | $17,718 |
| Medicare IN (WPS J8) | medicare | 635 | $170,434 | $6,807 |
| Anthem / BCBS Medicare Advantage | medicare_advantage | 576 | $88,873 | $2,980 |
| UnitedHealthcare | commercial | 437 | $46,370 | $11,643 |
| Medicare Supplement carriers | supplement | 560 | $35,833 | $67 |
| UMWA Health & Retirement Funds | commercial | 654 | $31,310 | $3,525 |
| UHC Integrated Services (Wausau) | commercial | 377 | $25,929 | $2,663 |
| UMR | commercial | 330 | $22,794 | $4,489 |
| Aetna | commercial | 300 | $20,445 | $3,023 |
| Self-funded plan TPAs | commercial | 420 | $18,000 | $529 |
| Humana Medicaid | medicaid | 109 | $11,878 | $3,996 |
| Medicaid KY | medicaid | 38 | $8,727 | $0 |
| Humana | commercial | 38 | $8,082 | $401 |
| Aetna Senior Supplemental (American Continental) | supplement | 35 | $6,519 | $0 |
| Illumifin | other | 39 | $4,544 | $0 |
| Southeastern Indiana Health Organization (Siho) | other | 49 | $4,187 | $0 |
| Medicaid IN | medicaid | 58 | $2,841 | $2,122 |

## Visit downcoding: who and what

229 office visits were downcoded out of 93,878 billed (0.24%), worth $7,664. Provider-level figures are in the dashboard (Downcoding and Who & what tabs) and are kept out of this file.

| payer | downcoded visits | visits billed | per 100 | dollars |
|---|---:|---:|---:|---:|
| Anthem / BCBS | 122 | 30,500 | 0.40 | $2,847 |
| Anthem / BCBS Medicare Advantage | 85 | 3,232 | 2.63 | $3,895 |
| Aetna | 5 | 1,485 | 0.34 | $39 |
| Anthem / BCBS Medicaid | 5 | 379 | 1.32 | $223 |
| WellCare / Ambetter (Centene) Medicaid | 5 | 172 | 2.91 | $194 |
| Medicare KY (CGS J15) | 3 | 23,403 | 0.01 | $220 |
| Freedom Life Insurance Company | 2 | 79 | 2.53 | $21 |
| UnitedHealthcare | 1 | 3,224 | 0.03 | $81 |

| plan named on the remittance | downcoded visits | dollars |
|---|---:|---:|
| KENTUCKY MEDICARE WLP | 84 | $3,816 |
| ITS HOST PPO NATIONAL | 52 | $1,239 |
| ANTHEM BLUE ACCESS PPO | 14 | $335 |
| PPO HRA | 11 | $231 |
| (not reported) | 11 | $579 |
| PPO | 9 | $200 |
| BLUE ACCESS PPO | 8 | $199 |
| GA HSA PPO | 6 | $146 |

| billed and paid | visits | dollars |
|---|---:|---:|
| 99204→99203 | 114 | $2,987 |
| 99214 at 99213 rate | 41 | $1,207 |
| 99214→99213 | 37 | $871 |
| 99214 denied for level | 13 | $1,457 |
| 99214 reduced | 9 | $327 |
| 99204 at 99203 rate | 6 | $279 |
| 99213 denied for level | 5 | $366 |
| 99204 reduced | 2 | $63 |

One provider accounts for 44% of all downcoded visits. Payers keep these lists per provider.

Procedure code changes by the payer (outside office visits):

- Anthem / BCBS 2026: 126 lines, $43,260
- Humana Medicare Advantage 2024: 10 lines, $2
- Humana Medicare Advantage 2025: 12 lines, $0

## Where the fight stands

| status | findings | dollars |
|---|---:|---:|
| open | 10,336 | $975,774 |
| recovered (paid on a later remittance) | 5,574 | $1,158,616 |

Open and still inside the filing window today: 3,236 findings, $253,824.

Active alerts (provider-level alerts are shown in the dashboard only):

- [high] 340 open findings worth $24,458 reach their filing deadline by 2026-12-12: UnitedHealthcare Medicare Advantage $5,840, Anthem / BCBS $4,940, Medicare IN (WPS J8) $3,074, Medicare KY (CGS J15) $3,018
- [high] Humana Medicare Advantage has been climbing on bundling: 144 of 4,408 lines (3.3%) over the last three months against 1.7% over the year before, worth $17,584
- [high] Anthem / BCBS has been climbing on procedure downcodes and code changes: 40 of 5,753 lines (0.7%) over the last three months against 0.3% over the year before, worth $13,224
- [high] No new remittances: the newest payment in the store is dated 2026-10-09 (34 days ago) and the last new file arrived 2026-11-12. Check the TriZetto download and the Dropbox upload.
- [medium] Southeastern Indiana Health Organization (Siho) is ramping up line denials: 5 of 22 lines (22.7%) in the last 28 days against 2.7% over the prior six months, worth $1,762
- [medium] UnitedHealthcare Medicare Advantage has been climbing on bundling: 13 of 788 lines (1.6%) over the last three months against 0.7% over the year before, worth $1,744

## Contract rates versus Medicare

Usual allowed amount per code (most common allowed amount on cleanly paid primary lines) as a percent of the same code on this practice's own Medicare remits, same year and provider type.

| payer | year | code | provider type | lines | payer rate | Medicare | % of Medicare | gap at parity |
|---|---|---|---|---:|---:|---:|---:|---:|
| Anthem / BCBS | 2024 | 99214 | nonphysician | 1912 | $64 | $102 | 62% | $73,555 |
| Anthem / BCBS | 2025 | 99214 | nonphysician | 1809 | $64 | $100 | 64% | $65,251 |
| Anthem / BCBS | 2026 | 99214 | nonphysician | 1449 | $64 | $109 | 59% | $64,828 |
| Anthem / BCBS | 2025 | 99213 | nonphysician | 2488 | $51 | $71 | 72% | $49,287 |
| Anthem / BCBS | 2024 | 99213 | nonphysician | 2235 | $51 | $72 | 70% | $47,762 |
| Anthem / BCBS | 2026 | 99213 | nonphysician | 1574 | $51 | $76 | 67% | $39,492 |
| Anthem / BCBS | 2024 | 99214 | physician | 538 | $75 | $120 | 62% | $24,350 |
| Anthem / BCBS | 2024 | 99213 | physician | 943 | $60 | $85 | 70% | $23,707 |
| Anthem / BCBS | 2025 | 99213 | physician | 784 | $60 | $83 | 72% | $18,267 |
| Anthem / BCBS | 2024 | 17110 | nonphysician | 551 | $55 | $88 | 62% | $18,117 |
| UnitedHealthcare Medicare Advantage | 2024 | 77280 | physician | 246 | $167 | $239 | 70% | $17,623 |
| Anthem / BCBS | 2025 | 99214 | physician | 383 | $75 | $118 | 64% | $16,255 |
| Anthem / BCBS | 2025 | 17110 | nonphysician | 524 | $55 | $84 | 65% | $15,547 |
| Anthem / BCBS | 2026 | 99204 | nonphysician | 363 | $104 | $143 | 72% | $14,378 |
| Anthem / BCBS | 2024 | 99204 | nonphysician | 449 | $104 | $134 | 77% | $13,632 |
| UnitedHealthcare Medicare Advantage | 2024 | 77600 | physician | 90 | $338 | $483 | 70% | $13,036 |
| Humana Medicare Advantage | 2024 | 77280 | physician | 271 | $191 | $239 | 80% | $12,943 |
| Anthem / BCBS | 2025 | 99204 | nonphysician | 464 | $104 | $131 | 79% | $12,667 |
| Anthem / BCBS | 2026 | 17110 | nonphysician | 402 | $55 | $86 | 64% | $12,663 |
| Anthem / BCBS | 2026 | 99214 | physician | 204 | $75 | $128 | 59% | $10,739 |
| Anthem / BCBS | 2026 | 99213 | physician | 362 | $60 | $90 | 67% | $10,686 |
| Humana Medicare Advantage | 2025 | 77600 | physician | 384 | $445 | $468 | 95% | $8,989 |
| UnitedHealthcare Medicare Advantage | 2024 | 88305 | physician | 287 | $40 | $66 | 60% | $7,560 |
| Anthem / BCBS | 2024 | 11102 | nonphysician | 477 | $62 | $77 | 80% | $7,212 |
| Anthem / BCBS | 2024 | 77280 | physician | 134 | $186 | $239 | 78% | $7,137 |
| Anthem / BCBS | 2025 | 11102 | nonphysician | 622 | $62 | $74 | 84% | $7,134 |
| UnitedHealthcare Medicare Advantage | 2025 | 88305 | physician | 271 | $38 | $64 | 60% | $6,938 |
| Anthem / BCBS | 2026 | 99203 | nonphysician | 465 | $79 | $94 | 84% | $6,873 |
| Anthem / BCBS | 2024 | 99204 | physician | 192 | $122 | $157 | 77% | $6,856 |
| Humana Medicare Advantage | 2024 | 99213 | nonphysician | 863 | $65 | $72 | 89% | $6,611 |

## Medicare program adjustments

- 2023: net MIPS adjustment +$4,344 across 2994 lines
- 2023: sequestration withheld $7,721
- 2024: net MIPS adjustment +$307,984 across 35964 lines
- 2024: sequestration withheld $157,514
- 2025: net MIPS adjustment +$82,364 across 37106 lines
- 2025: sequestration withheld $207,387
- 2026: net MIPS adjustment +$15,014 across 21240 lines
- 2026: sequestration withheld $85,566

## Fees withheld and takebacks

- Fees: UMWA HEALTH & RETIREMENT FUNDS: $14,452 across 148 remittances
- Fees: MUTUAL OF OMAHA COMMERCIAL: $10,098 across 4000 remittances
- Fees: WAUSAU: $5,269 across 595 remittances
- Fees: AETNA - AMERICAN CONTINENTAL INS.CO.: $4,997 across 697 remittances
- Fees: GREAT AMERICAN LIFE: $3,120 across 656 remittances
- Fees: MEDICO INSURANCE COMPANY: $3,045 across 1787 remittances
- Fees: AMERICAN ENTERPRISE GROUP: $2,997 across 1364 remittances
- Fees: UNITEDHEALTH INTEGRATED SERVICE - FORMERLY WAUSAU: $2,990 across 387 remittances
- Fees: UMR: $2,705 across 355 remittances
- Fees: UNITED MEDICAL RESOURCES: $2,676 across 278 remittances
- Fees: ACTUARIAL MANAGEMENTS RESOURCES: $1,713 across 342 remittances
- Fees: WELLABE SERVICES COMPANY: $1,555 across 423 remittances
- Takebacks: KY MEDICARE 2026: $172,615 across 53 recoupments of $500 or more
- Takebacks: KY MEDICARE 2025: $127,390 across 10 recoupments of $500 or more
- Takebacks: MEDICARE OF INDIANA 2026: $63,374 across 17 recoupments of $500 or more
- Takebacks: KY MEDICARE 2024: $46,607 across 11 recoupments of $500 or more
- Takebacks: MEDICARE OF INDIANA 2025: $31,582 across 24 recoupments of $500 or more
- Takebacks: HUMANA 2025: $4,444 across 5 recoupments of $500 or more
- Takebacks: IN MEDICARE 2024: $2,416 across 4 recoupments of $500 or more
- Takebacks: UNITED HEALTHCARE OF ALL STATES 2024: $2,308 across 3 recoupments of $500 or more
- Takebacks: UNITED HEALTHCARE OF ALL STATES 2025: $2,285 across 2 recoupments of $500 or more
- Takebacks: HUMANA 2026: $2,165 across 3 recoupments of $500 or more

## Correctable billing errors (no appeal needed, just a corrected claim)

- DENIED_MISSING_INFO: 483 lines, $60,053
- CLAIM_DENIED_UNPAID: 147 lines, $23,355
- DENIED_MISSING_MODIFIER: 212 lines, $18,887
- DENIED_CODING: 66 lines, $6,787
- EM_BUNDLED_MOD57_MINOR: 38 lines, $3,788
- EM_BUNDLED_NO_MODIFIER: 40 lines, $2,923

## Appeal deadlines (from each payer's provider manual where known, by payer type otherwise)

- due within 30 days: 329 findings, $23,603
- due within 90 days: 537 findings, $48,488
- expired: 4,897 findings, $419,411
- later: 2,370 findings, $181,733

## Caveats

- Expected amounts come from the payer's own remittance history (mode of allowed amounts), not from executed contracts. Load the contracts to replace them.
- Provider type (physician vs PA/NP) is inferred from Medicare allowed amounts; a roster would make it exact.
- Appeal windows: Anthem commercial (two years) and Anthem Kentucky Medicaid (90 days) were read in the payer manuals; the others come from research notes or payer type. A contract can set a different period.
- Whole-claim denials count as unpaid only when no later remit paid the same claim number.
