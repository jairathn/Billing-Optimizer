# Tools of the decisions unit (the owner's decisions 2, 3 and 4)

Everything here opens a private copy of the real store: patient data. Run them only in a private folder (mode 700), with
`python3 -I -B`; they print counts, dollars, codes, payer groups, finding ids and day offsets, never names, claim ids,
check numbers or dates of one claim. Never copy their outputs into the repository.

The run itself is `plans/tools/world/cycle_run.py` (point `F5_SRC` at an exported tree, `P2_DIR` at the folder holding
the world copy and `ema_ledger.sqlite`, `P2_DB` at the copy's file name; its argument is the run's day).

- `fullsnap.py <world> <snapshot>`: the findings table (every column) and results_items of a world after a run.
- `diff_ba.py <base snapshot> <after snapshot> <moved.json>`: item by item; prints the moves by class (d2: a rule 8
  patient-group denial; d3: an item closed by a payment in another role; other) and the recovered dollars that changed;
  writes the moved items (ids, states, sizes) to the JSON file.
- `trace.py <tree> <world> <base snapshot> <after snapshot> <finding id>`: one moved item before and after, its evidence,
  EMA's line totals, every 835 row of its service, and the state and next step the tree gives it today.
- `hand.py <world> <finding id>`: the item's raw rows read straight from the tables (no remitwatch code): the 835
  lines of its service with their adjustments, EMA's lines, and an independent reading of decisions 2 and 3 to set beside
  what the run decided. `pick.py <moved.json> [ids]`: picks the items to hand-trace (every decision-3 item, a spread of
  decision-2 transitions) into `<moved.json>.pick`.
- `one_item.py <world> <finding id>`: the item's bill, every 835 line of its codes with its adjustments.
- `d2_ema.py <tree> <world> <moved.json>`, `d2_list.py <tree> <world> <condition>`: decision 2 items by state with EMA's
  line totals (patient paid, adjusted, written off, open); the second lists the ids matching a condition on them.
- `r8_base.py`, `r8_states.py`, `r8_facts.py`: rule 8 items in a snapshot or a world (aggregates).
- `d3_base.py`, `d3_detail.py`, `d3_owed.py`, `d3_owed_b.py`, `d3_r3_all.py`: decision 3 populations (rule 3 closures
  in another role, closures settled as secondary) and what each payer owed in that role by the formula.
- `t.sh <python> <pytest args>`: the tests with every feed variable unset.

Decision 4 (codes a payer almost never pays; run on a world after a cycle of the branch's tree):
- `d4_measure.py <tree> <world>`: the run's evidence per pair (meta rarely_paid:why), the owner's table's payer-policy
  patterns (count, dollars, decision, researched verdict), the items that carry a "Why" block by payer group, code,
  verdict and period (dollars, work now), and the work-now items whose next step a never-payable rule leads, by signal.
- `d4_hand.py <world>`: the evidence of the biggest pairs counted again from the raw 835 rows with plain SQL (no
  remitwatch code): lines answered and paid, recoded, and the reasons on the unpaid lines.
- `d4_trace.py <tree> <world>`: every work-now item a never-payable rule leads, checked against its own rows (the billed
  code is the rule's, nothing paid on the line, the service date inside the rule's period, the payer group the rule's;
  for a denial only EMA saw, one EMA line of that code with no insurance payment). Prints the checks and the failures.
- `d4_show.py <tree> <world>`: what the drawer's "Why" block and the next step say for one item of a few kinds.

The fix round of 2026-11-26 (the two reviews of decisions 2, 3 and 4):
- `fix_measure.py <tree> <world> <today>`: the open patient-group denials by live state and rule (count, dollars), the
  shares the next payer's patient share capped, the next payers that answered with a contractual part only, the
  work-now appeal blocks, the open second-role remainders and the open G6001 items by payer group and state.
- `check_moved.py <world> <moved.json>`: every item `diff_ba.py` lists as moved, checked against its own rows with plain
  SQL (no remitwatch code): a can't-check has no EMA line of the service and no Production report covering its date; a
  resized payer-side item's size is its evidence's payer rate; a capped share is at most the next payer's patient share.
