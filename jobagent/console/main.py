"""The `jobagent` command: builds the parser from commands.py and dispatches.

Typical flow:
  1. jobagent log-application --job-title ... --company ... --requirements ... --location ... --link ...
  2. jobagent find-contact-link --company "Acme Corp"      # opens manually in your browser
  3. jobagent save-contact --row 2 --name "Jane Doe" --title "Engineering Manager" --linkedin-url "..."
  4. jobagent draft-outreach-email --row 2 --to jane@acme.com --subject "..." --body-file outreach.txt
  5. jobagent draft-application-email --row 2 --to jobs@acme.com --subject "..." --body-file cover_letter.txt --attach resume.pdf

Or just: jobagent watch, the scheduled pipeline that run_auto.sh runs.

From a checkout, `python cli.py ...` is the same command. cli.py is a shim
over main() here, kept so run_auto.sh, the launchd plist and the README go on
working unchanged.

This used to be cli.py, and before that it was 951 lines, 378 of them
argparse, because every command repeated the same three calls. The commands
are a table now and the handlers are in handlers.py. Nothing here knows what
any command does.

Every command runs in the user's home directory (core/home.py), the way the
scheduled run always began with `cd` into the project. Files named on the
command line are the exception: those are found from wherever the command
was typed.
"""

import argparse
import os

from jobagent.console.commands import BY_NAME, COMMANDS, GROUPS, USER_FILES


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Job application tracker CLI",
        epilog=_epilog(prog or "jobagent"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # The sixty-two names made a usage line nobody could read. The epilog
    # lists them by group instead, and the metavar keeps argparse from
    # printing the whole set a second time.
    sub = parser.add_subparsers(dest="command", required=True,
                                metavar="<command>")
    for command in COMMANDS:
        p = sub.add_parser(command.name, help=command.help)
        for arg in command.args:
            p.add_argument(*arg.flags, **arg.kwargs)
        p.set_defaults(_command=command)
    return parser


def _epilog(prog: str) -> str:
    out = ["commands, by what they do:"]
    for group, what in GROUPS.items():
        out.append(f"\n  {group} — {what}")
        names = [c.name for c in COMMANDS if c.group == group]
        line = "   "
        for name in names:
            if len(line) + len(name) + 2 > 76:
                out.append(line)
                line = "   "
            line += f" {name}"
        out.append(line)
    out.append(f"\n  {prog} <command> --help  for one command's options")
    return "\n".join(out)


def from_here(args: argparse.Namespace, cwd: str) -> None:
    """Makes the files the user named absolute, from the directory they
    typed the command in, before the command moves to the home directory.

    Only values they typed: a default such as past_outreach.txt is one of the
    project's own files and stays relative, which now means in the home
    directory.
    """
    command = BY_NAME[args.command]
    defaults = {_dest(a): a.kwargs.get("default") for a in command.args}
    for dest in USER_FILES:
        value = getattr(args, dest, None)
        if not value or value == defaults.get(dest):
            continue
        if isinstance(value, list):
            setattr(args, dest, [os.path.join(cwd, os.path.expanduser(v))
                                 for v in value])
        else:
            setattr(args, dest, os.path.join(cwd, os.path.expanduser(value)))


def _dest(arg) -> str:
    """The attribute argparse stores an argument under, by its own rule."""
    if "dest" in arg.kwargs:
        return arg.kwargs["dest"]
    longs = [f for f in arg.flags if f.startswith("--")]
    return (longs or list(arg.flags))[0].lstrip("-").replace("-", "_")


def main(argv: list[str] | None = None, prog: str | None = None) -> None:
    args = build_parser(prog).parse_args(argv)
    from_here(args, os.getcwd())
    # Before the handler is imported: the home's .env may set JOBAGENT_*
    # overrides that core/storage.py reads at import.
    from jobagent.core import home
    home.enter()
    args._command.handler()(args)
