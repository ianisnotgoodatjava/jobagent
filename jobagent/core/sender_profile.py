"""Who the outreach is from — kept in a file that git does not track.

The drafting modules used to hard-code one person's name, school, degree,
employer and address directly into their templates and prompts. That works
fine privately and makes the repository unpublishable: the code cannot be
shared or reused without also publishing a CV.

So the identity lives in `profile.json`, which is gitignored, and
`profile.example.json` ships with placeholders. Anything missing falls back to
a neutral placeholder rather than to someone's real details, so a checkout
with no profile produces obviously-unsent template text instead of silently
writing mail as the previous owner.
"""

import json
import os
import re

PROFILE_FILE = os.environ.get("OUTREACH_PROFILE", "profile.json")
EXAMPLE_FILE = "profile.example.json"

# Deliberately placeholder-shaped. If one of these turns up in a draft it is
# obvious at a glance that the profile was never filled in.
DEFAULTS = {
    "name": "YOUR NAME",
    "email": "you@example.com",
    "phone": "",
    "school": "YOUR UNIVERSITY",
    "school_short": "YOUR UNIVERSITY",
    "degree": "YOUR DEGREE",
    "graduated": "recently",
    "employer": "YOUR EMPLOYER",
    "job_title": "YOUR JOB TITLE",
    # The sender's university mail domain, if they have one. Used to keep
    # university mail out of the confirmation searches and to stop it being
    # read as a company. Empty means no such domain, and nothing is excluded.
    "school_email_domain": "",
    "sign_off": "Best regards",
    "subject_prefix": "Recent {school_short} Graduate Inquiring about",
    "resume_path": "",
}

_cache = None


def load(path: str | None = None) -> dict:
    """The sender's details, with placeholders for anything unset."""
    global _cache
    if _cache is not None and path is None:
        return _cache
    merged = dict(DEFAULTS)
    for candidate in ([path] if path else [PROFILE_FILE, EXAMPLE_FILE]):
        if candidate and os.path.exists(candidate):
            try:
                with open(candidate) as f:
                    merged.update({k: v for k, v in json.load(f).items()
                                   if v not in (None, "")})
                break
            except (OSError, ValueError):
                continue
    if path is None:
        _cache = merged
    return merged


def get(field: str, default: str = "") -> str:
    return str(load().get(field, default) or default)


def first_name() -> str:
    """The sender's first name, as prompts and patterns refer to them."""
    parts = get("name").split()
    return parts[0] if parts else ""


def file_stem() -> str:
    """The name as it goes into a filename: "Jane Doe" -> "Jane_Doe"."""
    return "_".join(get("name").split()) or "Resume"


def resume_path() -> str:
    """The resume to attach to outreach.

    profile.json's resume_path when set. Without one, the file the drafting
    modules used to hard-code for everyone, ~/<First_Last>_Resume.pdf --
    which is now derived from the sender's own name instead of the first
    owner's, so a second user's drafts no longer try to attach a file that
    only exists on someone else's laptop.
    """
    configured = get("resume_path")
    if configured:
        return os.path.expanduser(configured)
    return os.path.expanduser(f"~/{file_stem()}_Resume.pdf")


def is_configured() -> bool:
    """False when the profile is still placeholders.

    Worth checking before anything is sent or drafted in bulk: the failure is
    silent otherwise, and produces mail addressed from "YOUR NAME".
    """
    return load().get("name") not in (None, "", DEFAULTS["name"])


def subject_prefix() -> str:
    p = load()
    return str(p["subject_prefix"]).format(
        school_short=p["school_short"], school=p["school"])


def subject_template_re() -> str:
    """Matches the sender's own subject template, either verb form.

    style_check used to carry this as a literal with the first owner's school
    in it, which both named them in a shareable file and flagged every subject
    a second user sent as off-template.
    """
    school = re.escape(load()["school_short"])
    return (r"(?i)^(re:\s*)?recent " + school +
            r" graduate (inquiring about|interested in)\b")


def subject_template_label(verb: str = "Inquiring about") -> str:
    """The template as it is shown to a human: "... about <area> at <company>"."""
    p = load()
    return (f"Recent {p['school_short']} Graduate {verb} "
            f"<area> at <company>")


def school_email_domain() -> str:
    """The sender's university mail domain, lowercased, or ""."""
    return get("school_email_domain").strip().lower()


def generic_domains() -> set:
    """Domains that are the sender's own rather than an employer's.

    Just the school domain today. It used to be written into two literal sets
    as one person's university, which named them and was wrong for everyone
    else.
    """
    d = school_email_domain()
    return {d} if d else set()


def intro_opening() -> str:
    """Just the self-introduction, for templates that add their own reason."""
    p = load()
    return (f"I hope you're doing well. My name is {p['name']}. I "
            f"{p['graduated']} graduated from {p['school_short']} with a "
            f"degree in {p['degree']} and currently work at {p['employer']}.")


def intro_sentence(area: str, company: str, focus: str = "") -> str:
    """The opening paragraph, built from the profile rather than hard-coded.

    The shape is measured from the most recent sends rather than invented: a
    one-clause introduction, then the specific thing of the reader's the
    sender came across, then the area they want to hear about. `focus` is that
    specific thing -- "your background and leadership of X's structured
    products practice" -- and falls back to the generic "your work at X" that
    about a fifth of the corpus emails use.

    "recently" becomes "recent" so the clause reads as the noun phrase the
    sender actually writes: "a recent <school> graduate in <degree>".
    """
    p = load()
    graduated = str(p["graduated"])
    adj = graduated[:-2] if graduated.endswith("ly") else graduated
    came_across = focus.strip() or f"your work at {company}"
    return (
        f"I hope you're doing well. My name is {p['name']}, a {adj} "
        f"{p['school_short']} graduate in {p['degree']}, currently working at "
        f"{p['employer']}. I came across {came_across} and would love to learn "
        f"more about your work and experience in {area}."
    )


def sign_off() -> str:
    return f"{load()['sign_off']},\n{load()['name']}"


def contact_line() -> str:
    p = load()
    return " | ".join(x for x in (p.get("email"), p.get("phone")) if x)
