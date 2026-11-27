#!/bin/bash
# The one scheduled run: 06:00, 18:00 and 23:00.
#
# There used to be two launchd jobs calling two entry points, and they were
# substantially the same program. `auto` and `watch` both scanned Gmail for
# applications, both scanned for rejections and replies, both backfilled job
# links, both drafted outreach -- five heavy passes over the same mailbox,
# duplicated. What differed was only the edges: watch enriched postings and
# ran the sheet-quality sweeps, auto cleaned up dead drafts and reported the
# coverage gap.
#
# Those edges are merged into `watch`, so this calls it once. The schedules
# were also backwards before: the pipeline that keeps the sheet current ran
# once at 03:15 while the lighter pass ran three times, so a rejection
# arriving at 09:00 sat undetected for eighteen hours -- which is how
# outreach kept being drafted to desks that had already said no.
#
# With nothing set, it runs this checkout's venv in this folder, as it always
# has. `jobagent install` writes JOBAGENT_PYTHON (a venv elsewhere) and
# JOBAGENT_HOME (data kept elsewhere) into the launchd job when you use them.
HERE="$(cd "$(dirname "$0")" && pwd)" || exit 1
PYTHON="${JOBAGENT_PYTHON:-$HERE/venv/bin/python}"
cd "${JOBAGENT_HOME:-$HERE}" || exit 1
mkdir -p logs
LOG="logs/auto-$(date +%Y-%m-%d).log"
{
  echo "===== run at $(date) ====="
  "$PYTHON" -u "$HERE/cli.py" watch --days 14 --limit 8
  echo
  # Then the agent: it reads what watch left, researches the best of the
  # applications still without a draft, drafts any follow-ups that are due
  # (unsent, like everything else), and writes the day's brief to
  # logs/briefs/. Once a day -- the first pass that runs writes it and the
  # later ones skip, so a Mac asleep at 06:00 still gets one at 18:00.
  "$PYTHON" -u "$HERE/cli.py" agent
  echo
} >> "$LOG" 2>&1
ls -1t logs/auto-*.log 2>/dev/null | tail -n +31 | xargs -I {} rm -f {}
