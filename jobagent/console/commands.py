"""Every command this project answers to, as a table.

main() used to be 378 lines of argparse: sixty near-identical blocks of
add_parser, add_argument, set_defaults. Forty-five of the sixty handlers were
four lines of pure delegation, and forty-five imports sat inside function
bodies, originally to break import cycles and now only to keep startup cheap.
None of that was wrong; it was just unreadable at the size it had reached,
and nothing checked it. A rarely-run command whose handler had been renamed
failed when it was finally run, months later.

So the commands are data. Each entry names its group, its help line, its
handler as "module:function", and its arguments in argparse's own vocabulary
- A() passes straight through to add_argument, so nothing is abstracted away
and nothing is lost. The handler is imported when the command runs and not
before, which is the same laziness as before with one mechanism instead of
forty-five. tests/test_console.py resolves every handler in the table, so a
renamed function now fails in CI rather than in six months.

Grouping is what the four verbs in DESIGN.md turned out to be, plus a fifth:
helping fill an application in is its own concern and the document missed it.
Every command keeps the name it has always had - run_auto.sh and the launchd
plist call them, and renaming them would buy nothing.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class A:
    """One argument, forwarded to argparse verbatim."""

    flags: tuple
    kwargs: dict

    def __init__(self, *flags, **kwargs):
        object.__setattr__(self, "flags", flags)
        object.__setattr__(self, "kwargs", kwargs)


@dataclass(frozen=True)
class Command:
    name: str
    group: str
    help: str
    run: str                      # "module:function", imported on use
    args: tuple = ()

    def handler(self):
        module, _, function = self.run.partition(":")
        import importlib
        return getattr(importlib.import_module(module), function)


GROUPS = {
    "scan": "read Gmail and the web; produce applications and decisions",
    "reconcile": "make the sheet match what the scans found",
    "source": "find a reachable person at a company",
    "draft": "write, stage and manage outreach",
    "apply": "help fill an application in",
    "agent": "a model that reads the whole search, acts within a budget, and briefs you",
}

COMMANDS = [
    # ---------------------------------------------------------------- scan
    Command(
        'scan-new-applications', 'scan',
        "Find applications in Gmail that aren't in the sheet yet and add "
        'them',
        "jobagent.console.handlers:cmd_scan_new_applications",
        args=(
            A('--days', type=int, default=730),
            A('--dry-run', action="store_true"),
        ),
    ),
    Command(
        'scan-rejections', 'scan',
        'Scan Gmail for rejection emails and auto-update matching rows',
        "jobagent.console.handlers:cmd_scan_rejections",
        args=(
            A('--days', type=int, default=180,
              help='How many days back to search (default 180)'),
            A('--dry-run', action="store_true",
              help='Show what would change without writing to the sheet'),
            A('--since',
              help='Only consider applications applied on/after this date '
                   '(YYYY-MM-DD)'),
        ),
    ),
    Command(
        'scan-replies', 'scan',
        'Detect interview invites and real human replies; set '
        'Status=interviewing and Network=Yes',
        "jobagent.console.handlers:cmd_scan_replies",
        args=(
            A('--days', type=int, default=460,
              help='How many days back to search (default 460)'),
            A('--dry-run', action="store_true",
              help='Show what would change without writing to the sheet'),
            A('--no-calendar', action="store_true",
              help='Skip Google Calendar evidence'),
            A('--notify', action="store_true",
              help='Email yourself a reminder about new reach-outs'),
        ),
    ),
    Command(
        'scan-job-links', 'scan',
        'Scan Gmail confirmation emails to auto-fill missing Job Link '
        'column',
        "jobagent.console.handlers:cmd_scan_job_links",
        args=(
            A('--days', type=int, default=365,
              help='How many days back to search (default 365)'),
            A('--dry-run', action="store_true",
              help='Show what would change without writing to the sheet'),
            A('--since',
              help='Only consider applications applied on/after this date '
                   '(YYYY-MM-DD)'),
        ),
    ),
    Command(
        'auto', 'scan',
        'Full automated pass: add new applications, detect rejections, '
        'sort, color, and report rows ready for outreach',
        "jobagent.console.handlers:cmd_auto",
        args=(
            A('--days', type=int, default=30,
              help='Look back this many days (default 30)'),
            A('--dry-run', action="store_true"),
        ),
    ),
    Command(
        'watch', 'scan',
        'One pass of the every-few-hours cycle: new applications in, '
        'posting link + drafted outreach out',
        "jobagent.console.handlers:cmd_watch",
        args=(
            A('--days', type=int, default=14),
            A('--deep', action="store_true", default=None,
              help='Force the daily maintenance sweeps (dedupe, positions, '
                   'locations, network audit, job search)'),
            A('--no-deep', action="store_false", dest='deep',
              help='Skip the maintenance sweeps even on the late pass'),
            A('--dry-run', action="store_true"),
            A('--limit', type=int, default=3,
              help='Max applications to research this pass'),
            A('--min-fit', type=float, default=2.0),
            A('--no-draft', action="store_true",
              help='Skip the paid research/drafting step'),
            A('--no-common-ground', action="store_true",
              help='Skip looking for people you share something with at '
                   'new applications'),
        ),
    ),
    Command(
        'sync', 'scan',
        'Full sync: add new applications from Gmail, detect rejections, '
        'sort by date, color-code',
        "jobagent.console.handlers:cmd_sync",
        args=(
            A('--days', type=int, default=730,
              help='How many days back to search (default 730)'),
            A('--dry-run', action="store_true",
              help='Show what would change without writing'),
        ),
    ),
    Command(
        'find-jobs', 'scan',
        'Search online for live postings that fit your background and are '
        'not already on the sheet',
        "jobagent.console.handlers:cmd_find_jobs",
        args=(
            A('--area', help='Search one specific area instead of all six'),
            A('--new-companies-only', action="store_true",
              help='Skip companies you have already applied to at all'),
            A('--per-area', type=int, default=4),
        ),
    ),
    Command(
        'parser-accuracy', 'scan',
        'Measure the confirmation-email parser against a frozen pull of the '
        'mailbox, offline',
        "jobagent.console.handlers:cmd_parser_accuracy",
        args=(
            A('--days', type=int, default=730,
              help='How far back to read when refreshing (default 730)'),
            A('--refresh', action="store_true",
              help='Re-read the mailbox first (~15 minutes; one request per '
                   'message body)'),
            A('--show', type=int, default=12,
              help='How many examples to list per section (default 12)'),
        ),
    ),
    Command(
        'train-rejection-model', 'scan',
        'Retrain the learned rejection classifier from your mail',
        "jobagent.console.handlers:cmd_train_rejection_model",
        args=(
            A('--days', type=int, default=500),
        ),
    ),
    Command(
        'train-interview-model', 'scan',
        'Retrain the post-interview-rejection classifier (labelled from '
        'your calendar)',
        "jobagent.console.handlers:cmd_train_interview_model",
        args=(
            A('--days', type=int, default=500,
              help='How many days back to train on (default 500)'),
        ),
    ),
    Command(
        'interview-times', 'scan',
        'Find times agreed in email threads, optionally adding them to your '
        'calendar',
        "jobagent.console.handlers:cmd_interview_times",
        args=(
            A('--create', action="store_true",
              help='Actually create the calendar events'),
            A('--days', type=int, default=60),
            A('--limit', type=int, default=120),
        ),
    ),
    Command(
        'backfill-job-descriptions', 'scan',
        'Fill the empty Job Description column from job links and '
        'application emails',
        "jobagent.console.handlers:cmd_backfill_job_descriptions",
        args=(
            A('--days', type=int, default=460,
              help='How far back to read application emails'),
            A('--dry-run', action="store_true"),
            A('--limit', type=int, default=60,
              help='Max rows to fill in one pass (default 60)'),
        ),
    ),
    Command(
        'backfill-locations', 'scan',
        'Fill the empty location column from job titles and confirmation '
        'emails',
        "jobagent.console.handlers:cmd_backfill_locations",
        args=(
            A('--days', type=int, default=520),
            A('--dry-run', action="store_true"),
            A('--no-email', action="store_true",
              help='Only use the job title, skip reading email'),
        ),
    ),
    # ---------------------------------------------------------------- reconcile
    Command(
        'list', 'reconcile',
        'List logged applications',
        "jobagent.console.handlers:cmd_list",
        args=(
            A('--since',
              help='Only show applications applied on/after this date '
                   '(YYYY-MM-DD)'),
        ),
    ),
    Command(
        'log-application', 'reconcile',
        'Log a new job application to the sheet',
        "jobagent.console.handlers:cmd_log_application",
        args=(
            A('--company'),
            A('--date', help='Defaults to today (YYYY-MM-DD)'),
            A('--job-title'),
            A('--link'),
            A('--location'),
            A('--paste-file',
              help='File holding the posting text you copied from the page. '
                   "Use for Workday/LinkedIn, which won't serve the posting "
                   'to a script'),
            A('--requirements'),
            A('--url',
              help='Job posting URL — reads the live posting and fills '
                   'title, company, location and requirements'),
            A('--yes', action="store_true",
              help='Skip the confirmation prompt'),
        ),
    ),
    Command(
        'update-status', 'reconcile',
        'Update the Status column (applied/rejected/interview/offer)',
        "jobagent.console.handlers:cmd_update_status",
        args=(
            A('--row', type=int, required=True),
            A('--status', choices=['applied', 'interviewing', 'interview-rejected', 'rejected', 'offer'], required=True),
        ),
    ),
    Command(
        'age-stale', 'reconcile',
        "Move applications that went silent past N days from 'applied' to "
        "'no response'",
        "jobagent.console.handlers:cmd_age_stale",
        args=(
            A('--days', type=int, default=60,
              help='Silence threshold in days (default 60)'),
            A('--write', action="store_true",
              help='Apply the changes (default is a dry run)'),
        ),
    ),
    Command(
        'dedupe', 'reconcile',
        'Find rows that are the same application logged twice (fuzzy '
        'company/title/date match)',
        "jobagent.console.handlers:cmd_dedupe",
        args=(
            A('--dry-run', action="store_true"),
            A('--max-day-gap', type=int, default=21,
              help='Treat as the same application if applied within this '
                   'many days (default 21)'),
        ),
    ),
    Command(
        'clean-sheet', 'reconcile',
        'Normalize Status spelling, fill blank Network cells, trim stray '
        'whitespace',
        "jobagent.console.handlers:cmd_clean_sheet",
        args=(
            A('--dry-run', action="store_true"),
        ),
    ),
    Command(
        'clean-positions', 'reconcile',
        'Repair Position/Company cells the email parser mangled',
        "jobagent.console.handlers:cmd_clean_positions",
        args=(
            A('--dry-run', action="store_true"),
        ),
    ),
    Command(
        'fix-positions', 'reconcile',
        "Repair imported rows stuck on the placeholder role 'Analyst', and "
        'drop ones that duplicate your own rows',
        "jobagent.console.handlers:cmd_fix_positions",
        args=(
            A('--days', type=int, default=520),
            A('--dry-run', action="store_true"),
        ),
    ),
    Command(
        'color-rows', 'reconcile',
        'Color-code rows: red = rejected, green = interview/offer',
        "jobagent.console.handlers:cmd_color_rows",
        args=(
            A('--dry-run', action="store_true"),
        ),
    ),
    Command(
        'sort-by-date', 'reconcile',
        'Sort the sheet by applied date, oldest at top',
        "jobagent.console.handlers:cmd_sort_by_date",
    ),
    Command(
        'corrections', 'reconcile',
        "Show fields you've corrected (scanners never overwrite these); "
        'with --row, set and lock one',
        "jobagent.console.handlers:cmd_corrections",
        args=(
            A('--column', help='Column to lock, e.g. Status'),
            A('--reason', default='',
              help='Why — recorded alongside the lock'),
            A('--row', type=int, help='Row to correct and lock'),
            A('--value', help='Value to set'),
        ),
    ),
    Command(
        'open-loops', 'reconcile',
        'Threads where someone asked you something and it is still '
        'unanswered — the ball is in your court',
        "jobagent.console.handlers:cmd_open_loops",
        args=(
            A('--days', type=int, default=90),
        ),
    ),
    Command(
        'follow-ups', 'reconcile',
        'List outreach emails that never got a reply',
        "jobagent.console.handlers:cmd_follow_ups",
        args=(
            A('--days', type=int, default=180),
            A('--min-age', type=int, default=7,
              help='Only flag sends older than this many days'),
        ),
    ),
    # ---------------------------------------------------------------- source
    Command(
        'find-people', 'source',
        'Rank reachable contacts at a company: desk match, VP/Director '
        'band, address, minus anyone emailed this year',
        "jobagent.console.handlers:cmd_find_people",
        args=(
            A('--company', required=True),
            A('--domain', default='', help='Mail domain, to build addresses'),
            A('--role', default='',
              help='The role you applied to, for desk matching'),
            A('--state', default='NY'),
            A('--posting-file', default='',
              help='The posting text, pasted into a file: its team, office '
                   'and reporting line aim the search'),
            A('--url', default='', help='Posting URL to read instead'),
        ),
    ),
    Command(
        'people-db', 'source',
        'Everyone found at a firm across every search, ranked for a role: '
        'desk, band, how many sources name them, same office',
        "jobagent.console.handlers:cmd_people_db",
        args=(
            A('--company', default=''),
            A('--role', default='', help='Rank for this role, and link it'),
            A('--location', default='', help="The job's city"),
            A('--backfill', action="store_true",
              help='Load every person the existing caches already hold'),
        ),
    ),
    Command(
        'site-people', 'source',
        "Read a firm's own about/team pages: names, titles, published "
        'addresses, and the mail domain and convention they show',
        "jobagent.console.handlers:cmd_site_people",
        args=(
            A('--company', required=True),
            A('--website', default='',
              help='Homepage, if the name alone will not find it'),
            A('--domain', default='', help='Mail domain, if known'),
            A('--refresh', action="store_true",
              help='Read the site again instead of the 14-day cache'),
        ),
    ),
    Command(
        'people', 'source',
        'Real people currently registered at a broker-dealer, from FINRA '
        'BrokerCheck — faster and fresher than LinkedIn',
        "jobagent.console.handlers:cmd_people",
        args=(
            A('--company', required=True),
            A('--limit', type=int, default=200,
              help='How many registrations to pull'),
            A('--show', type=int, default=20),
            A('--state', default='NY',
              help='Branch state filter (default NY; empty for all)'),
        ),
    ),
    Command(
        'sourcing', 'source',
        'How to find a real person at this company — picks the right source '
        'per company',
        "jobagent.console.handlers:cmd_sourcing",
        args=(
            A('--company', required=True),
            A('--role', default=''),
            A('--state', default='NY'),
        ),
    ),
    Command(
        'edgar', 'source',
        "Investment professionals named in a firm's SEC fund filings — "
        'covers asset managers FINRA does not',
        "jobagent.console.handlers:cmd_edgar",
        args=(
            A('--company', required=True),
            A('--form-d', action="store_true",
              help='Form D related persons instead — officers and directors '
                   'of private funds'),
            A('--since', default='2024-01-01'),
        ),
    ),
    Command(
        'findability', 'source',
        'Rank open applications by how cheaply a real contact can be found '
        '— do the verifiable ones first',
        "jobagent.console.handlers:cmd_findability",
        args=(
            A('--coverage', action="store_true",
              help='Which open applications have no draft yet'),
            A('--days', type=int,
              help='Only applications from the last N days (default: all '
                   'open)'),
            A('--show', type=int, default=12, help='Rows to list per tier'),
        ),
    ),
    Command(
        'addresses', 'source',
        "Learn each company's email format from what actually got "
        'delivered; check an address before using it',
        "jobagent.console.handlers:cmd_addresses",
        args=(
            A('--cached', action="store_true",
              help='Use the saved cache instead of re-reading Gmail'),
            A('--check',
              help="Check one address against that domain's history"),
            A('--days', type=int, default=520),
        ),
    ),
    Command(
        'address-for', 'source',
        'Build a work address from a name, using the convention your '
        'delivered mail proves',
        "jobagent.console.handlers:cmd_address_for",
        args=(
            A('--domain', required=True),
            A('--name', required=True),
        ),
    ),
    Command(
        'hit-rate', 'draft',
        'Did the outreach land? Reply and bounce rate per email, with the '
        'ones too young to call held out of the denominator',
        "jobagent.console.handlers:cmd_hit_rate",
        args=(
            A('--days', type=int, default=520,
              help='How far back to read your sent mail'),
            A('--limit', type=int, default=600,
              help='Most sent messages to examine'),
            A('--refresh', action="store_true",
              help='Re-resolve every thread instead of reusing settled ones'),
            A('--pending', action="store_true",
              help='Just list the outreach still waiting, oldest first'),
        ),
    ),
    Command(
        'who-replies', 'source',
        'Reply and bounce rates from your own outreach history — who is '
        'actually worth emailing',
        "jobagent.console.handlers:cmd_who_replies",
        args=(
            A('--days', type=int, default=520),
            A('--limit', type=int, default=600),
        ),
    ),
    Command(
        'network-audit', 'source',
        'Re-check the Network column: only people you actually exchanged '
        'mail with stay Yes',
        "jobagent.console.handlers:cmd_network_audit",
        args=(
            A('--days', type=int, default=460),
            A('--dry-run', action="store_true"),
        ),
    ),
    Command(
        'targets', 'source',
        'Which open applications have nobody contacted yet — ranked so '
        'outreach goes where there is no coverage',
        "jobagent.console.handlers:cmd_targets",
        args=(
            A('--days', type=int, default=520),
        ),
    ),
    Command(
        'prune-repeat-contacts', 'source',
        'Delete queued first-touch drafts to anyone you already emailed '
        'inside the window',
        "jobagent.console.handlers:cmd_recent_contacts",
        args=(
            A('--days', type=int, default=365),
            A('--dry-run', action="store_true"),
        ),
    ),
    Command(
        'common-ground', 'source',
        'People at the firm who share something with you — a school, '
        'language, sport, hobby or background — nearest the role first',
        "jobagent.console.handlers:cmd_common_ground",
        args=(
            A('--row', type=int, help='The application, by sheet row'),
            A('--company', default='', help='Or name the company'),
            A('--role', default='', help='The role applied to'),
            A('--trait', action="append", default=[],
              metavar='KIND:TERM',
              help='Also look for this, repeatable: e.g. sport:rowing, '
                   'language:Portuguese, school:"Example University", '
                   'hobby:chess, group:Toastmasters. Several spellings: '
                   'language:Portuguese|Brazilian Portuguese'),
            A('--only', action="store_true",
              help='Look only for the --trait values, not your profile '
                   'and resume'),
            A('--links-only', action="store_true",
              help='Only print the free LinkedIn/web searches; no model '
                   'request'),
            A('--redo', action="store_true",
              help='Research again even if this application was checked'),
            A('--days', type=int, default=14,
              help='With no --row/--company: check every unchecked '
                   'application from the last N days'),
            A('--limit', type=int, default=5,
              help='Max applications to research in that sweep'),
            A('--dry-run', action="store_true",
              help='Sweep: show what would be researched, pay for nothing'),
        ),
    ),
    Command(
        'find-contact-link', 'source',
        'Print a LinkedIn people-search URL to browse manually',
        "jobagent.console.handlers:cmd_find_contact_link",
        args=(
            A('--company', required=True),
            A('--keywords', default='recruiter OR talent OR hiring'),
        ),
    ),
    Command(
        'save-contact', 'source',
        'Save a contact you found manually to a row',
        "jobagent.console.handlers:cmd_save_contact",
        args=(
            A('--linkedin-url'),
            A('--name', required=True),
            A('--row', type=int, required=True),
            A('--title'),
        ),
    ),
    # ---------------------------------------------------------------- draft
    Command(
        'auto-outreach', 'draft',
        'Find up to 3 verified people on the team and draft to them, for '
        'each application with no contact yet',
        "jobagent.console.handlers:cmd_auto_outreach",
        args=(
            A('--contacts', type=int, default=3,
              help='Contacts per firm (your rule is up to 3 per team)'),
            A('--dry-run', action="store_true"),
            A('--limit', type=int, default=10,
              help='Applications to work through'),
        ),
    ),
    Command(
        'auto-draft', 'draft',
        'Research a real contact and write a finished outreach email per '
        "application, via Meta's Muse API (costs money)",
        "jobagent.console.handlers:cmd_auto_draft",
        args=(
            A('--days', type=int,
              help='Only applications from the last N days (default: all '
                   'open)'),
            A('--exclude',
              help="Regex on company to leave out entirely (e.g. 'situs')"),
            A('--limit', type=int, default=5),
            A('--min-fit', type=float, default=2.0),
            A('--prefer',
              help='Regex; matching company/role/skills go first (e.g. '
                   "'real estate|CRE|mortgage')"),
            A('--redo', action="store_true",
              help='Re-research applications already done in a previous run'),
            A('--write-drafts', action="store_true",
              help='Also create the Gmail drafts (still unsent)'),
        ),
    ),
    Command(
        'auto-draft-outreach', 'draft',
        'Auto-generate an outreach email from a row, matching your past '
        'networking tone, and create it as a Gmail draft',
        "jobagent.console.handlers:cmd_auto_draft_outreach",
        args=(
            A('--attach', action="append", default=[],
              help='Path to attach (e.g. resume.pdf), repeatable'),
            A('--dry-run', action="store_true",
              help='Print the generated email without creating a Gmail '
                   'draft'),
            A('--follow-up', action="store_true",
              help='Generate a follow-up instead of the initial outreach'),
            A('--name',
              help="Contact's first/full name, for the greeting (sheet has "
                   'no Contact Name column to read it from)'),
            A('--row', type=int, required=True),
            A('--to', required=True),
        ),
    ),
    Command(
        'batch-outreach', 'draft',
        'Prepare an outreach draft for each uncontacted application, ranked '
        'by fit; you fill in the hook',
        "jobagent.console.handlers:cmd_batch_outreach",
        args=(
            A('--limit', type=int, default=25),
            A('--min-fit', type=float, default=1.0),
        ),
    ),
    Command(
        'stage-drafts', 'draft',
        'Name the contact on the sheet and create Gmail drafts from '
        'research already saved',
        "jobagent.console.handlers:cmd_stage_drafts",
        args=(
            A('--company', help='Regex; only stage companies matching this'),
            A('--drafts', action="store_true",
              help='Also create the Gmail drafts (still unsent)'),
            A('--write', action="store_true",
              help='Apply the changes; without it, only report what would '
                   'happen'),
        ),
    ),
    Command(
        'cleanup-drafts', 'draft',
        'Delete outreach drafts for applications that were rejected',
        "jobagent.console.handlers:cmd_cleanup_drafts",
        args=(
            A('--dry-run', action="store_true"),
        ),
    ),
    Command(
        'schedule-followups', 'draft',
        'Find silent outreach past the learned wait and draft ONE touch #2 '
        'each',
        "jobagent.console.handlers:cmd_schedule_followups",
        args=(
            A('--draft', action="store_true",
              help='Create the Gmail drafts. Without this, only report what '
                   'is due'),
            A('--max', type=int, default=12,
              help='Most drafts to create in one run'),
            A('--max-age', type=int, default=30,
              help='Ignore first sends older than this; that is '
                   "reoutreach's territory"),
            A('--min-age', type=int,
              help='Days of silence before a follow-up is due (default: his '
                   'measured median)'),
        ),
    ),
    Command(
        'reach-out-again', 'draft',
        'Warm leads that went cold: someone replied, then it went quiet '
        'with no rejection',
        "jobagent.console.handlers:cmd_reoutreach",
        args=(
            A('--days', type=int, default=520,
              help='How far back to look (default 520)'),
            A('--min-silent', type=int, default=21,
              help='Only show threads quiet this many days (default 21)'),
        ),
    ),
    Command(
        'draft-outreach-email', 'draft',
        'Create a Gmail draft to the contact on a row',
        "jobagent.console.handlers:cmd_draft_outreach_email",
        args=(
            A('--attach', action="append", default=[],
              help='Path to attach (e.g. resume.pdf), repeatable'),
            A('--body'),
            A('--body-file'),
            A('--row', type=int, required=True),
            A('--subject', required=True),
            A('--to', required=True),
        ),
    ),
    Command(
        'draft-application-email', 'draft',
        'Create a Gmail draft to apply via email',
        "jobagent.console.handlers:cmd_draft_application_email",
        args=(
            A('--attach', action="append", default=[],
              help='Path to attach, repeatable'),
            A('--body'),
            A('--body-file'),
            A('--row', type=int, required=True),
            A('--subject', required=True),
            A('--to', required=True),
        ),
    ),
    Command(
        'style', 'draft',
        'How you actually write, measured from your sent corpus — what the '
        'draft generator is told to match',
        "jobagent.console.handlers:cmd_style",
    ),
    Command(
        'outreach-examples', 'draft',
        'Show your most similar past outreach emails for a row, to base a '
        'new draft on',
        "jobagent.console.handlers:cmd_outreach_examples",
        args=(
            A('--area',
              help="Team/subject area to weight the search (e.g. 'RMBS')"),
            A('-k', type=int, default=3, help='How many examples to show'),
            A('--row', type=int, required=True),
        ),
    ),
    Command(
        'build-outreach-corpus', 'draft',
        'Index your past sent outreach emails for retrieval',
        "jobagent.console.handlers:cmd_build_outreach_corpus",
        args=(
            A('--limit', type=int, default=900),
        ),
    ),
    Command(
        'export-past-outreach', 'draft',
        'Save your past sent networking emails to a file, for tone/format '
        'reference',
        "jobagent.console.handlers:cmd_export_past_outreach",
        args=(
            A('--limit', type=int, default=50),
            A('--out', default='past_outreach.txt'),
        ),
    ),
    Command(
        'cover-letter', 'draft',
        'Render a cover letter body to .docx and PDF using your resume_bank '
        'letterhead',
        "jobagent.console.handlers:cmd_cover_letter",
        args=(
            A('--body-file', required=True,
              help='Plain-text letter body; blank lines separate paragraphs'),
            A('--company', required=True),
            A('--date', help='Letter date (defaults to today)'),
            A('--recipient', action="append",
              help='Recipient address block line, repeatable'),
        ),
    ),
    # ---------------------------------------------------------------- apply
    Command(
        'prep', 'apply',
        'Build an application prep package (bullets, ATS answers, parsed '
        'requirements) from a job URL',
        "jobagent.console.handlers:cmd_prep",
        args=(
            A('--company', default='', help='Override company name'),
            A('--cover-letter', action="store_true",
              help='Also draft a cover letter'),
            A('--no-resume', action="store_true",
              help='Skip the tailored PDF resume'),
            A('--no-sheet', action="store_true",
              help="Don't write anything to the sheet"),
            A('--role', default='', help='Override role title'),
            A('--row', type=int,
              help='Sheet row this application is on (also writes Job '
                   'Description/Requirements)'),
            A('--text-file', default='',
              help='Posting text you pasted into a file (use when the ATS '
                   'blocks fetching)'),
            A('--url', default='', help='Job posting URL'),
        ),
    ),
    Command(
        'apply-profile', 'apply',
        'Export your autofill field map and the standard application '
        'answers',
        "jobagent.console.handlers:cmd_apply_profile",
        args=(
            A('--company', default='',
              help='Company, to title the answer sheet'),
            A('--role', default='', help='Role, to title the answer sheet'),
        ),
    ),
    Command(
        'autofill-extension', 'apply',
        'Build a Chrome extension that fills Greenhouse/Lever forms '
        'automatically on open — never submits',
        "jobagent.console.handlers:cmd_autofill_extension",
    ),
    Command(
        'autofill-link', 'apply',
        'Build a bookmarklet that fills a Greenhouse/Lever application form '
        'from your profile — it never submits',
        "jobagent.console.handlers:cmd_autofill_link",
    ),
    Command(
        'setup', 'apply',
        'Check what this clone still needs, and optionally create the '
        'tracking sheet',
        "jobagent.console.handlers:cmd_setup",
        args=(
            A('--create-sheet', action="store_true",
              help='Create a new Google Sheet in your Drive and write its '
                   'id to .env'),
            A('--title', default='Job Applications',
              help='Title for the new sheet'),
        ),
    ),
    Command(
        'enable-calendar', 'apply',
        'Grant read-only Google Calendar access (one-time browser consent)',
        "jobagent.console.handlers:cmd_enable_calendar",
    ),
    Command(
        'tailor', 'apply',
        'Copy a job posting, get your resume with the skills line tailored '
        'to it -- only skills your own bullets prove, gaps listed, one page',
        "jobagent.console.handlers:cmd_tailor",
        args=(
            A('--watch', action="store_true",
              help='Keep watching the clipboard and tailor every posting you copy'),
            A('--text-file', default='',
              help='Read the posting from a file instead of the clipboard'),
            A('--company', default='', help='Company, for the file name'),
            A('--role', default='', help='Role title, for the file name'),
            A('--open', action="store_true",
              help='Open the PDF when it is written'),
        ),
    ),
    Command(
        'state', 'reconcile',
        'Every state file and database: what kind it is, whether it reads, '
        'how big; --backup snapshots what cannot be rebuilt',
        "jobagent.console.handlers:cmd_state",
        args=(
            A('--backup', action="store_true",
              help='Copy corrections, ledgers and people.db to data/backups/<today>/'),
        ),
    ),
    # --------------------------------------------------------------- agent
    Command(
        'agent', 'agent',
        'The daily agent pass: read the whole search, research the best '
        'gaps, draft due follow-ups (unsent), and write a brief',
        "jobagent.console.handlers:cmd_agent",
        args=(
            A('--dry-run', action="store_true",
              help='Drafting tools only report what they would do'),
            A('--force', action="store_true",
              help='Run even if today already has a brief'),
            A('--max-drafts', type=int, default=3,
              help='Most applications to research this pass'),
            A('--max-followups', type=int, default=5,
              help='Most follow-ups to draft this pass'),
            A('--no-notify', action="store_true",
              help='No macOS notification when the brief is written'),
            A('--email', action="store_true",
              help='Also email the brief to your own address'),
        ),
    ),
    Command(
        'ask', 'agent',
        'Ask the agent about your search in plain language, e.g. '
        '"who is waiting on a reply from me?"',
        "jobagent.console.handlers:cmd_ask",
        args=(
            A('question', nargs='*',
              help='The question; quotes optional'),
            A('--allow-drafts', action="store_true",
              help='Let it create unsent drafts if the question asks for them'),
        ),
    ),
    Command(
        'agent-serve', 'agent',
        "Run the tracker's tools as an MCP server on stdio, for Claude "
        'Desktop, Claude Code or any MCP client',
        "jobagent.console.handlers:cmd_agent_serve",
    ),
    Command(
        'llm-status', 'agent',
        'Which models are configured, which one runs the agent, and how to '
        'connect the tracker to Claude Desktop or Claude Code',
        "jobagent.console.handlers:cmd_llm_status",
    ),
    Command(
        'install', 'agent',
        'Write the launchd job that runs the pipeline at 06:00, 18:00 and '
        '23:00, and print how to connect Claude to the tracker',
        "jobagent.console.handlers:cmd_install",
        args=(
            A('--load', action="store_true",
              help='Also copy it to ~/Library/LaunchAgents and load it, '
                   'replacing a job already loaded under the same name'),
            A('--force', action="store_true",
              help='Overwrite a different plist already at that path'),
        ),
    ),
]

BY_NAME = {c.name: c for c in COMMANDS}

# Arguments that name a file the user points at: a body they wrote, a
# posting they pasted, a resume to attach, where to save an export. Every
# command runs in the home directory (core/home.py), so main.py makes these
# absolute from where the command was typed first.
USER_FILES = ("paste_file", "posting_file", "attach", "body_file", "out",
              "text_file")

