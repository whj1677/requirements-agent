"""Compatibility entry for the current batch-table workflow.

Saved answers and proposals reach a PRD through one batch confirmation.
The former per-question controls were intentionally removed. The shared
rendered scenario verifies no writes on entry, exact confirmed clauses,
one batch confirmation, recheck and a successful PRD download.
"""
import argparse
import asyncio
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.check_prewrite_conflict_prd_browser import run

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--static-build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.case='saved';args.resume_data=None;args.replay_plan_from=None
    args.execute_real_model=False;args.replay_draft_from=None
    asyncio.run(run(args))
