set -e
M=/tmp/claude-0/-home-user-Billing-Optimizer/5f451de0-d1a3-5988-9a9a-08fbd67159f6/scratchpad/det/a
python3 -I -B /tmp/claude-0/-home-user-Billing-Optimizer/5f451de0-d1a3-5988-9a9a-08fbd67159f6/scratchpad/det/snap.py $M/world.sqlite /tmp/claude-0/-home-user-Billing-Optimizer/5f451de0-d1a3-5988-9a9a-08fbd67159f6/scratchpad/det/snap1.sqlite
for n in 2 3; do
  cd $M && F5_SRC=/tmp/claude-0/-home-user-Billing-Optimizer/5f451de0-d1a3-5988-9a9a-08fbd67159f6/scratchpad/wt/suite P2_DIR=$M P2_DB=world.sqlite python3 -I -B /tmp/claude-0/-home-user-Billing-Optimizer/5f451de0-d1a3-5988-9a9a-08fbd67159f6/scratchpad/det/run.py 2026-11-24 > $M/run${n}_stdout.txt 2> $M/run${n}_stderr.txt
  python3 -I -B /tmp/claude-0/-home-user-Billing-Optimizer/5f451de0-d1a3-5988-9a9a-08fbd67159f6/scratchpad/det/snap.py $M/world.sqlite /tmp/claude-0/-home-user-Billing-Optimizer/5f451de0-d1a3-5988-9a9a-08fbd67159f6/scratchpad/det/snap$n.sqlite
  echo "run $n done"
done
