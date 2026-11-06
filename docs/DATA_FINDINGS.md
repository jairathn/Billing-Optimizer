# What the remittances say: where Owensboro Dermatology is losing money

Generated 2026-11-06 from 31,547 unique 835 remittances (188,089 claims, 408,876 service lines), payment dates 2024-01-01 to 2026-09-10, $29,371,752 paid. Detection version 1.1. Dollar figures are the allowed-amount shortfall against the payer's own usual rate (or Medicare where noted), not charges. All findings are open until a biller marks them otherwise.

## The one-paragraph answer

Across the whole archive the detectable leakage is about $3,254,619. Classic E/M downcoding, the thing the practice asked about, is $79,851 of it. The money is in bundling ($430,200), denials that were never worked ($1,157,321), contract rates below Medicare ($877,022 at Medicare parity), underpayment against the payer's own rate ($182,702), and fees plus takebacks ($527,522).

## By year and category

| year | downcoding | bundling | denial | underpayment | fees | contract | total |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2023 | $982 | $9,386 | $117,403 | $1,994 | $0 | $5,528 | $135,293 |
| 2024 | $9,804 | $142,213 | $397,965 | $55,021 | $72,267 | $394,390 | $1,071,659 |
| 2025 | $11,081 | $122,780 | $413,846 | $81,243 | $197,789 | $281,640 | $1,108,380 |
| 2026 | $57,984 | $155,822 | $225,745 | $44,424 | $257,466 | $195,465 | $936,906 |

## By signal

| signal | category | what to do | findings | dollars |
|---|---|---|---:|---:|
| CONTRACT_RATE_GAP | contract | renegotiate | 51 | $615,666 |
| TAKEBACK | fees | review | 139 | $460,343 |
| DENIED_MEDICAL_NECESSITY | denial | appeal | 1,420 | $261,586 |
| MA_BELOW_MEDICARE | contract | review | 132 | $261,356 |
| PROCEDURE_BUNDLED | bundling | appeal | 772 | $197,995 |
| DENIED_NONCOVERED | denial | review | 1,716 | $194,922 |
| UNDERPAID_VS_OWN_RATE | underpayment | appeal | 2,989 | $180,541 |
| DENIED_MISSING_INFO | denial | correct_and_resubmit | 1,253 | $159,291 |
| DENIED_AUTH | denial | appeal | 923 | $115,231 |
| EM_BUNDLED_MOD57_MAJOR | bundling | appeal | 1,185 | $112,610 |
| DENIED_FREQUENCY | denial | appeal | 1,023 | $85,976 |
| EM_BUNDLED_MOD25 | bundling | appeal | 1,112 | $76,364 |
| DENIED_DUPLICATE_UNPAID | denial | review | 410 | $75,924 |
| CODE_CHANGED | downcoding | review | 276 | $70,369 |
| FEE_SKIM | fees | opt_out | 1,122 | $67,179 |
| CLAIM_DENIED_UNPAID | denial | review | 347 | $43,077 |
| DENIED_MISSING_MODIFIER | denial | correct_and_resubmit | 375 | $34,510 |
| CLAIM_DENIED_UNPAID | denial | appeal | 829 | $34,152 |
| DENIED_MISSING_PRIMARY | denial | review | 170 | $29,951 |
| DENIED_OTHER | denial | review | 98 | $29,018 |
| DENIED_PROVIDER | denial | review | 173 | $26,882 |
| CLAIM_DENIED_UNPAID | denial | correct_and_resubmit | 154 | $25,318 |
| ADDON_BUNDLED | bundling | appeal | 130 | $23,309 |
| DENIED_CODING | denial | correct_and_resubmit | 208 | $21,353 |
| DENIED_TIMELY_FILING | denial | review | 158 | $20,129 |
| EM_BUNDLED_NO_MODIFIER | bundling | correct_and_resubmit | 135 | $10,825 |
| EM_BUNDLED_MOD24 | bundling | appeal | 72 | $4,886 |
| DOWNCODE_SVC06 | downcoding | appeal | 152 | $4,401 |
| EM_BUNDLED_MOD57_MINOR | bundling | correct_and_resubmit | 43 | $4,212 |
| DOWNCODE_LEVEL_DENIED | downcoding | appeal | 71 | $3,548 |
| MPPR_EXCESS | underpayment | appeal | 130 | $2,161 |
| DOWNCODE_RATE | downcoding | appeal | 46 | $1,509 |
| DOWNCODE_CHARGE | downcoding | appeal | 1 | $24 |

## By payer

| payer | kind | findings | dollars | of which appealable now |
|---|---|---:|---:|---:|
| Anthem / BCBS | commercial | 2,831 | $838,247 | $20,457 |
| Medicare KY (CGS J15) | medicare | 3,178 | $714,045 | $21,131 |
| Humana Medicare Advantage | medicare_advantage | 2,811 | $542,653 | $0 |
| Medicare IN (WPS J8) | medicare | 1,508 | $286,686 | $6,638 |
| UnitedHealthcare Medicare Advantage | medicare_advantage | 1,529 | $244,677 | $25,880 |
| UMWA Health & Retirement Funds | commercial | 1,206 | $186,125 | $22,247 |
| Anthem / BCBS Medicare Advantage | medicare_advantage | 948 | $133,548 | $0 |
| UnitedHealthcare | commercial | 551 | $74,705 | $33,165 |
| Medicare Supplement carriers | supplement | 553 | $35,082 | $38 |
| UHC Integrated Services (Wausau) | commercial | 457 | $34,724 | $4,447 |
| Aetna | commercial | 347 | $29,864 | $3,709 |
| UMR | commercial | 426 | $28,812 | $4,537 |
| Self-funded plan TPAs | commercial | 478 | $21,595 | $562 |
| Humana Medicaid | medicaid | 119 | $13,025 | $0 |
| Humana | commercial | 79 | $11,010 | $0 |
| Southeastern Indiana Health Organization (Siho) | other | 90 | $10,392 | $0 |
| Medicaid KY | medicaid | 41 | $8,734 | $0 |
| Aetna Senior Supplemental (American Continental) | supplement | 34 | $6,366 | $0 |
| Railroad Medicare | medicare | 61 | $5,565 | $236 |
| Anthem / BCBS Medicaid | medicaid | 64 | $5,526 | $0 |

## Contract rates versus Medicare

Usual allowed amount per code (most common allowed amount on cleanly paid primary lines) as a percent of the same code on this practice's own Medicare remits, same year and provider type.

| payer | year | code | provider type | lines | payer rate | Medicare | % of Medicare | gap at parity |
|---|---|---|---|---:|---:|---:|---:|---:|
| Anthem / BCBS | 2024 | 99214 | nonphysician | 1912 | $64 | $102 | 62% | $73,555 |
| Anthem / BCBS | 2025 | 99214 | nonphysician | 1809 | $64 | $100 | 64% | $65,251 |
| Anthem / BCBS | 2026 | 99214 | nonphysician | 1317 | $64 | $109 | 59% | $58,923 |
| Anthem / BCBS | 2025 | 99213 | nonphysician | 2488 | $51 | $71 | 72% | $49,287 |
| Anthem / BCBS | 2024 | 99213 | nonphysician | 2235 | $51 | $72 | 70% | $47,762 |
| Anthem / BCBS | 2026 | 99213 | nonphysician | 1424 | $51 | $76 | 67% | $35,728 |
| Anthem / BCBS | 2024 | 99214 | physician | 538 | $75 | $120 | 62% | $24,350 |
| Anthem / BCBS | 2024 | 99213 | physician | 943 | $60 | $85 | 70% | $23,707 |
| Anthem / BCBS | 2025 | 99213 | physician | 784 | $60 | $83 | 72% | $18,267 |
| Anthem / BCBS | 2024 | 17110 | nonphysician | 551 | $55 | $88 | 62% | $18,117 |
| UnitedHealthcare Medicare Advantage | 2024 | 77280 | physician | 246 | $167 | $239 | 70% | $17,623 |
| Anthem / BCBS | 2025 | 99214 | physician | 384 | $75 | $118 | 64% | $16,297 |
| Anthem / BCBS | 2025 | 17110 | nonphysician | 524 | $55 | $84 | 65% | $15,547 |
| Anthem / BCBS | 2024 | 99204 | nonphysician | 449 | $104 | $134 | 77% | $13,632 |
| Anthem / BCBS | 2026 | 99204 | nonphysician | 336 | $104 | $142 | 73% | $13,067 |
| UnitedHealthcare Medicare Advantage | 2024 | 77600 | physician | 90 | $338 | $483 | 70% | $13,036 |
| Humana Medicare Advantage | 2024 | 77280 | physician | 271 | $191 | $239 | 80% | $12,943 |
| Anthem / BCBS | 2025 | 99204 | nonphysician | 464 | $104 | $131 | 79% | $12,667 |
| Anthem / BCBS | 2026 | 17110 | nonphysician | 361 | $55 | $86 | 64% | $11,372 |
| Anthem / BCBS | 2026 | 99214 | physician | 190 | $75 | $128 | 59% | $10,002 |
| Anthem / BCBS | 2026 | 99213 | physician | 336 | $60 | $90 | 67% | $9,919 |
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
- 2025: sequestration withheld $207,405
- 2026: net MIPS adjustment +$13,568 across 18955 lines
- 2026: sequestration withheld $76,734

## Fees withheld and takebacks

- Fees: UMWA HEALTH & RETIREMENT FUNDS: $14,166 across 145 remittances
- Fees: MUTUAL OF OMAHA COMMERCIAL: $9,979 across 3952 remittances
- Fees: WAUSAU: $5,269 across 595 remittances
- Fees: AETNA - AMERICAN CONTINENTAL INS.CO.: $4,845 across 672 remittances
- Fees: GREAT AMERICAN LIFE: $3,084 across 643 remittances
- Fees: AMERICAN ENTERPRISE GROUP: $2,997 across 1364 remittances
- Fees: UNITEDHEALTH INTEGRATED SERVICE - FORMERLY WAUSAU: $2,887 across 370 remittances
- Fees: MEDICO INSURANCE COMPANY: $2,765 across 1585 remittances
- Fees: UNITED MEDICAL RESOURCES: $2,676 across 278 remittances
- Fees: UMR: $2,363 across 302 remittances
- Fees: ACTUARIAL MANAGEMENTS RESOURCES: $1,636 across 330 remittances
- Fees: WELLABE SERVICES COMPANY: $1,555 across 423 remittances
- Takebacks: KY MEDICARE 2026: $171,610 across 52 recoupments of $500 or more
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

- DENIED_MISSING_INFO: 1,253 lines, $159,291
- DENIED_MISSING_MODIFIER: 375 lines, $34,510
- CLAIM_DENIED_UNPAID: 154 lines, $25,318
- DENIED_CODING: 208 lines, $21,353
- EM_BUNDLED_NO_MODIFIER: 135 lines, $10,825
- EM_BUNDLED_MOD57_MINOR: 43 lines, $4,212

## Appeal deadlines (estimated from the remittance date and payer type)

- due within 30 days: 411 findings, $49,299
- due within 90 days: 427 findings, $39,933
- expired: 11,684 findings, $1,213,665
- later: 501 findings, $56,905

## Caveats

- Expected amounts come from the payer's own remittance history (mode of allowed amounts), not from executed contracts. Load the contracts to replace them.
- Provider type (physician vs PA/NP) is inferred from Medicare allowed amounts; a roster would make it exact.
- Appeal windows are defaults by payer type; confirm against each contract and provider manual.
- Whole-claim denials count as unpaid only when no later remit paid the same claim number.
