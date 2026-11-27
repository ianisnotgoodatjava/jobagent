"""`python cli.py <command>`: the same command as `jobagent <command>`.

The command line lives in jobagent/console/main.py. This shim stays so the
scheduled run (run_auto.sh, and the launchd plist that calls it) and every
`python cli.py ...` in the README keep working from a checkout, including a
checkout whose venv never ran `pip install -e .`.
"""

from jobagent.console.main import main

if __name__ == "__main__":
    main(prog="python cli.py")
