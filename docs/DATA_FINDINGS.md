# What the remittances say: where Owensboro Dermatology is losing money

Generated 2026-11-06 from 32,043 unique 835 remittances (190,604 claims, 414,157 service lines), payment dates 2024-01-01 to 2026-10-09, $29,687,478 paid. Detection version 1.1. Dollar figures are the allowed-amount shortfall against the payer's own usual rate (or Medicare where noted), not charges. All findings are open until a biller marks them otherwise.

## The one-paragraph answer

Across the whole archive the detectable, still-unrecovered leakage is about $2,472,352. Classic E/M downcoding, the thing the practice asked about, is $61,408 of it. The money is in bundling ($171,926), denials that were never worked ($633,105), contract rates below Medicare ($888,428 at Medicare parity), underpayment against the payer's own rate ($187,611), and fees plus takebacks ($529,875). A further $816,175 across 5,531 lines was initially denied or bundled and then paid on a later remittance, which is the rework the billers did do; those are marked resolved and excluded from the open figures.

## By year and category

| year | downcoding | bundling | denial | underpayment | fees | contract | total |
|---|---:|---:|---:|---:|---:|---:|---:|
Open findings only (resolved and dismissed excluded).

| 2023 | $816 | $3,721 | $89,222 | $1,994 | $0 | $5,528 | $101,281 |
| 2024 | $2,926 | $58,155 | $235,077 | $55,021 | $72,267 | $394,390 | $817,835 |
| 2025 | $4,095 | $36,766 | $205,577 | $81,261 | $197,789 | $281,640 | $807,127 |
| 2026 | $53,571 | $73,284 | $100,867 | $49,315 | $259,819 | $206,871 | $743,727 |

## By signal

| signal | category | what to do | open findings | open dollars | paid later (rework) |
|---|---|---|---:|---:|---:|
| CONTRACT_RATE_GAP | contract | renegotiate | 52 | $622,856 | $0 |
| TAKEBACK | fees | review | 141 | $462,040 | $0 |
| MA_BELOW_MEDICARE | contract | review | 134 | $265,572 | $0 |
| UNDERPAID_VS_OWN_RATE | underpayment | appeal | 3,037 | $185,443 | $0 |
| DENIED_NONCOVERED | denial | review | 1,036 | $107,371 | $95,168 |
| DENIED_AUTH | denial | appeal | 755 | $94,095 | $21,442 |
| DENIED_MEDICAL_NECESSITY | denial | appeal | 688 | $88,008 | $178,440 |
| EM_BUNDLED_MOD57_MAJOR | bundling | appeal | 737 | $71,931 | $43,523 |
| DENIED_DUPLICATE_UNPAID | denial | review | 376 | $70,747 | $0 |
| FEE_SKIM | fees | opt_out | 1,138 | $67,834 | $0 |
| DENIED_MISSING_INFO | denial | correct_and_resubmit | 489 | $61,457 | $109,954 |
| CODE_CHANGED | downcoding | review | 162 | $52,166 | $0 |
| PROCEDURE_BUNDLED | bundling | appeal | 230 | $49,854 | $154,058 |
| DENIED_FREQUENCY | denial | appeal | 325 | $42,132 | $46,465 |
| CLAIM_DENIED_UNPAID | denial | review | 331 | $41,706 | $0 |
| EM_BUNDLED_MOD25 | bundling | appeal | 585 | $37,733 | $39,277 |
| CLAIM_DENIED_UNPAID | denial | appeal | 832 | $35,011 | $0 |
| CLAIM_DENIED_UNPAID | denial | correct_and_resubmit | 145 | $22,755 | $0 |
| DENIED_MISSING_MODIFIER | denial | correct_and_resubmit | 207 | $20,347 | $14,321 |
| DENIED_TIMELY_FILING | denial | review | 143 | $20,187 | $1,041 |
| DENIED_PROVIDER | denial | review | 77 | $10,291 | $17,428 |
| DENIED_OTHER | denial | review | 50 | $9,440 | $20,131 |
| DENIED_CODING | denial | correct_and_resubmit | 64 | $6,681 | $15,634 |
| DOWNCODE_SVC06 | downcoding | appeal | 150 | $4,095 | $0 |
| DOWNCODE_LEVEL_DENIED | downcoding | appeal | 72 | $3,580 | $0 |
| EM_BUNDLED_MOD57_MINOR | bundling | correct_and_resubmit | 37 | $3,546 | $756 |
| EM_BUNDLED_MOD24 | bundling | appeal | 47 | $3,158 | $1,799 |
| EM_BUNDLED_NO_MODIFIER | bundling | correct_and_resubmit | 40 | $2,976 | $8,066 |
| DENIED_MISSING_PRIMARY | denial | review | 40 | $2,876 | $27,545 |
| ADDON_BUNDLED | bundling | appeal | 24 | $2,727 | $21,127 |
| MPPR_EXCESS | underpayment | appeal | 131 | $2,168 | $0 |
| DOWNCODE_RATE | downcoding | appeal | 47 | $1,542 | $0 |
| DOWNCODE_CHARGE | downcoding | appeal | 1 | $24 | $0 |

## By payer

| payer | kind | findings | dollars | of which appealable now |
|---|---|---:|---:|---:|
| Anthem / BCBS | commercial | 2,554 | $822,301 | $17,928 |
| Medicare KY (CGS J15) | medicare | 1,297 | $483,806 | $11,120 |
| Humana Medicare Advantage | medicare_advantage | 1,807 | $397,783 | $4,083 |
| UnitedHealthcare Medicare Advantage | medicare_advantage | 1,219 | $221,854 | $22,432 |
| Medicare IN (WPS J8) | medicare | 868 | $187,559 | $8,939 |
| Anthem / BCBS Medicare Advantage | medicare_advantage | 682 | $94,536 | $836 |
| UnitedHealthcare | commercial | 439 | $46,297 | $11,439 |
| Medicare Supplement carriers | supplement | 559 | $35,390 | $67 |
| UMWA Health & Retirement Funds | commercial | 650 | $31,056 | $3,572 |
| UHC Integrated Services (Wausau) | commercial | 382 | $26,056 | $3,061 |
| UMR | commercial | 324 | $22,143 | $4,202 |
| Aetna | commercial | 298 | $21,100 | $3,623 |
| Self-funded plan TPAs | commercial | 417 | $18,084 | $566 |
| Humana Medicaid | medicaid | 109 | $11,878 | $0 |
| Medicaid KY | medicaid | 39 | $8,770 | $0 |
| Humana | commercial | 40 | $8,172 | $0 |
| Aetna Senior Supplemental (American Continental) | supplement | 35 | $6,414 | $0 |
| Illumifin | other | 39 | $4,544 | $0 |
| Medicaid IN | medicaid | 57 | $2,954 | $2,235 |
| Southeastern Indiana Health Organization (Siho) | other | 46 | $2,629 | $0 |

## Contract rates versus Medicare

Usual allowed amount per code (most common allowed amount on cleanly paid primary lines) as a percent of the same code on this practice's own Medicare remits, same year and provider type.

| payer | year | code | provider type | lines | payer rate | Medicare | % of Medicare | gap at parity |
|---|---|---|---|---:|---:|---:|---:|---:|
| Anthem / BCBS | 2024 | 99214 | nonphysician | 1912 | $64 | $102 | 62% | $73,555 |
| Anthem / BCBS | 2025 | 99214 | nonphysician | 1809 | $64 | $100 | 64% | $65,251 |
| Anthem / BCBS | 2026 | 99214 | nonphysician | 1361 | $64 | $109 | 59% | $60,891 |
| Anthem / BCBS | 2025 | 99213 | nonphysician | 2488 | $51 | $71 | 72% | $49,287 |
| Anthem / BCBS | 2024 | 99213 | nonphysician | 2235 | $51 | $72 | 70% | $47,762 |
| Anthem / BCBS | 2026 | 99213 | nonphysician | 1484 | $51 | $76 | 67% | $37,234 |
| Anthem / BCBS | 2024 | 99214 | physician | 538 | $75 | $120 | 62% | $24,350 |
| Anthem / BCBS | 2024 | 99213 | physician | 943 | $60 | $85 | 70% | $23,707 |
| Anthem / BCBS | 2025 | 99213 | physician | 784 | $60 | $83 | 72% | $18,267 |
| Anthem / BCBS | 2024 | 17110 | nonphysician | 551 | $55 | $88 | 62% | $18,117 |
| UnitedHealthcare Medicare Advantage | 2024 | 77280 | physician | 246 | $167 | $239 | 70% | $17,623 |
| Anthem / BCBS | 2025 | 99214 | physician | 384 | $75 | $118 | 64% | $16,297 |
| Anthem / BCBS | 2025 | 17110 | nonphysician | 524 | $55 | $84 | 65% | $15,547 |
| Anthem / BCBS | 2024 | 99204 | nonphysician | 449 | $104 | $134 | 77% | $13,632 |
| Anthem / BCBS | 2026 | 99204 | nonphysician | 348 | $104 | $142 | 73% | $13,534 |
| UnitedHealthcare Medicare Advantage | 2024 | 77600 | physician | 90 | $338 | $483 | 70% | $13,036 |
| Humana Medicare Advantage | 2024 | 77280 | physician | 271 | $191 | $239 | 80% | $12,943 |
| Anthem / BCBS | 2025 | 99204 | nonphysician | 464 | $104 | $131 | 79% | $12,667 |
| Anthem / BCBS | 2026 | 17110 | nonphysician | 374 | $55 | $86 | 64% | $11,781 |
| Anthem / BCBS | 2026 | 99213 | physician | 349 | $60 | $90 | 67% | $10,302 |
| Anthem / BCBS | 2026 | 99214 | physician | 194 | $75 | $128 | 59% | $10,212 |
| Humana Medicare Advantage | 2025 | 77600 | physician | 384 | $445 | $468 | 95% | $8,989 |
| UnitedHealthcare Medicare Advantage | 2024 | 88305 | physician | 287 | $40 | $66 | 60% | $7,560 |
| Anthem / BCBS | 2024 | 11102 | nonphysician | 477 | $62 | $77 | 80% | $7,212 |
| Anthem / BCBS | 2024 | 77280 | physician | 134 | $186 | $239 | 78% | $7,137 |
| Anthem / BCBS | 2025 | 11102 | nonphysician | 622 | $62 | $74 | 84% | $7,134 |
| UnitedHealthcare Medicare Advantage | 2025 | 88305 | physician | 271 | $38 | $64 | 60% | $6,938 |
| Anthem / BCBS | 2024 | 99204 | physician | 192 | $122 | $157 | 77% | $6,856 |
| Humana Medicare Advantage | 2024 | 99213 | nonphysician | 863 | $65 | $72 | 89% | $6,611 |
| Humana Medicare Advantage | 2025 | 77280 | physician | 572 | $219 | $231 | 95% | $6,607 |

## Medicare program adjustments

- 2023: net MIPS adjustment +$4,344 across 2994 lines
- 2023: sequestration withheld $7,721
- 2024: net MIPS adjustment +$307,984 across 35964 lines
- 2024: sequestration withheld $157,514
- 2025: net MIPS adjustment +$82,364 across 37106 lines
- 2025: sequestration withheld $207,387
- 2026: net MIPS adjustment +$14,341 across 20235 lines
- 2026: sequestration withheld $81,674

## Fees withheld and takebacks

- Fees: UMWA HEALTH & RETIREMENT FUNDS: $14,354 across 147 remittances
- Fees: MUTUAL OF OMAHA COMMERCIAL: $10,029 across 3966 remittances
- Fees: WAUSAU: $5,269 across 595 remittances
- Fees: AETNA - AMERICAN CONTINENTAL INS.CO.: $4,892 across 678 remittances
- Fees: GREAT AMERICAN LIFE: $3,097 across 650 remittances
- Fees: AMERICAN ENTERPRISE GROUP: $2,997 across 1364 remittances
- Fees: UNITEDHEALTH INTEGRATED SERVICE - FORMERLY WAUSAU: $2,920 across 379 remittances
- Fees: MEDICO INSURANCE COMPANY: $2,870 across 1681 remittances
- Fees: UNITED MEDICAL RESOURCES: $2,676 across 278 remittances
- Fees: UMR: $2,432 across 330 remittances
- Fees: ACTUARIAL MANAGEMENTS RESOURCES: $1,645 across 334 remittances
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

- DENIED_MISSING_INFO: 489 lines, $61,457
- CLAIM_DENIED_UNPAID: 145 lines, $22,755
- DENIED_MISSING_MODIFIER: 207 lines, $20,347
- DENIED_CODING: 64 lines, $6,681
- EM_BUNDLED_MOD57_MINOR: 37 lines, $3,546
- EM_BUNDLED_NO_MODIFIER: 40 lines, $2,976

## Appeal deadlines (estimated from the remittance date and payer type)

- due within 30 days: 317 findings, $24,317
- due within 90 days: 447 findings, $36,131
- expired: 7,443 findings, $643,385
- later: 436 findings, $35,430

## Caveats

- Expected amounts come from the payer's own remittance history (mode of allowed amounts), not from executed contracts. Load the contracts to replace them.
- Provider type (physician vs PA/NP) is inferred from Medicare allowed amounts; a roster would make it exact.
- Appeal windows are defaults by payer type; confirm against each contract and provider manual.
- Whole-claim denials count as unpaid only when no later remit paid the same claim number.
