"""`python -m jobagent <command>`: the same command as `jobagent <command>`."""

from jobagent.console.main import main

if __name__ == "__main__":
    main(prog="python -m jobagent")
