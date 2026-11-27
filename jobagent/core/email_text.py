"""Undo the line wrapping Gmail puts into plain-text mail.

Gmail's plain-text copy of a sent email is hard-wrapped at 76 characters,
and that copy is what the corpus stores. Two things then go wrong. Exemplars
shown to the generator carry the wrapping, so it writes drafts with a
newline every 75 characters. And a draft whose body has those newlines shows
them literally: Gmail turns each one into a <br>, so every paragraph arrives
as a ragged column of half-lines, worst on a phone.

`unwrap` joins the lines of a paragraph that was wrapped and leaves alone a
block whose line breaks were meant. "Wrapped" is tested the way wrapping
works, not by guessing from length alone: a line was broken by the wrapper
only if it is within the width and the next word would not have fitted on
it. Measured over the corpus, wrapped lines score 74 or more on that test;
deliberate breaks — "Best regards,\\nJane Doe", a "Job Title:/Job ID:" pair
in a follow-up — score far under it, or run past the width, which a wrapped
line never does.
"""

import re

WIDTH = 76      # Gmail's plain-text wrap column; no wrapped line is longer.
_SLACK = 2      # a wrapped line plus the word that did not fit: >= WIDTH - 2

_KEEP_LINE_RE = re.compile(r"^\s*(>|[-•*]\s|\d+[.)]\s|On .+wrote:\s*$)")


def _broken_by_wrapper(line: str, next_line: str) -> bool:
    line = line.rstrip()
    words = next_line.split()
    if not words or len(line) > WIDTH:
        return False
    return len(line) + 1 + len(words[0]) >= WIDTH - _SLACK


def _keep(line: str) -> bool:
    return bool(_KEEP_LINE_RE.match(line))


def unwrap(body: str) -> str:
    """Join lines the wrapper broke; keep blank lines and deliberate breaks.

    Decided per line, not per paragraph: a wrapped paragraph often runs
    straight into "Best regards," with no blank line between, and that
    block is half wrapped prose and half a sign-off that must stay put.
    """
    lines = (body or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    prev = ""   # the previous line as written — the joined one is too long
    for line in lines:
        if (prev.strip() and line.strip() and not _keep(prev)
                and not _keep(line) and _broken_by_wrapper(prev, line)):
            out[-1] = out[-1].rstrip() + " " + line.strip()
        else:
            out.append(line)
        prev = line
    return "\n".join(out)
