"""The project's shape, checked rather than believed.

A layered design decays the moment one import points the wrong way, and that
import never looks wrong on its own — it is one line that makes something
work. So the layering is asserted here instead of described in a document
nobody re-reads.

Before this existed the graph was genuinely knotted: sources imported
sheetops for two string functions and sheetops imported sources to find job
links, so no ordering of the layers could be free of upward imports.
"""

import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import architecture

WHERE = {m: layer for layer, mods in architecture.LAYOUT.items() for m in mods}
RANK = {layer: i for i, layer in enumerate(architecture.LAYERS)}


def _local_imports(path):
    """Every project module a file imports, however the import is spelled.

    Three forms reach the same module and all three must be seen. The one
    that matters most is `from outreach import draft_writer`, because that is
    what the move produced everywhere -- reading only the module part of it
    yields "outreach", a layer name that is in no layer, so every violation
    slipped through and this test passed while the rule was being broken.

    Since the layers moved under one package every import carries its name
    first -- `from jobagent.outreach import draft_writer` -- and read as before
    the first part is "jobagent", which is in no layer either. So the package
    is taken off before anything else is read, and
    TestTheCheckItself plants each spelling to prove a violation still fails.
    """
    for node in ast.walk(ast.parse(open(path).read())):
        if isinstance(node, ast.Import):
            for alias in node.names:          # import jobagent.core.company_id
                parts = _unpackaged(alias.name.split("."))
                if parts:
                    yield parts[-1]
        elif isinstance(node, ast.ImportFrom) and node.module:
            parts = _unpackaged(node.module.split("."))
            if not parts:                     # from jobagent import outreach
                for alias in node.names:
                    yield alias.name
            elif parts[0] in RANK:
                if len(parts) > 1:
                    yield parts[-1]           # from jobagent.core.company_id import key
                else:
                    for alias in node.names:  # from jobagent.core import company_id
                        yield alias.name
            else:
                yield parts[0]


def _unpackaged(parts):
    """The dotted name without the package in front, if it has it."""
    return parts[1:] if parts and parts[0] == architecture.PACKAGE else parts


def _layer_of(dep):
    """The layer an imported name belongs to: a module's layer, or the layer
    itself when a whole layer was imported. None for anything else."""
    return WHERE.get(dep) or (dep if dep in RANK else None)


def _upward(layer, path):
    """What a file in `layer` imports from layers above its own."""
    return [f"{_layer_of(dep)}/{dep}" for dep in _local_imports(path)
            if _layer_of(dep) and RANK[_layer_of(dep)] > RANK[layer]]


def _modules():
    for layer in architecture.LAYERS:
        folder = os.path.join(ROOT, architecture.folder(layer))
        for name in sorted(os.listdir(folder)):
            if name.endswith(".py") and name != "__init__.py":
                yield layer, name[:-3], os.path.join(folder, name)


def _entry_points():
    for name in architecture.ENTRY_POINTS:
        yield name, os.path.join(ROOT, name)


class TestEntryPoints:
    """The root scripts, which the layer walk above cannot see."""

    def test_entry_points_exist(self):
        for name, path in _entry_points():
            assert os.path.exists(path), f"{name} is declared and missing"

    def test_nothing_imports_an_entry_point(self):
        """An entry point is a leaf. Importing cli.py from a layer would make
        the command line part of the system rather than a way into it."""
        names = {n[:-3] for n, _ in _entry_points()}
        bad = [f"{layer}/{module} imports {dep}"
               for layer, module, path in _modules()
               for dep in _local_imports(path) if dep in names]
        assert not bad, bad

    def test_an_entry_point_holds_no_logic(self):
        """cli.py was 951 lines, 378 of them argparse and 400 of them handler
        bodies. Both moved to console/, and this keeps them out."""
        for name, path in _entry_points():
            source = open(path).read()
            lines = len(source.split("\n"))
            assert lines < 120, f"{name} is {lines} lines; logic belongs in a layer"


class TestLayering:
    def test_no_module_imports_from_a_higher_layer(self):
        bad = [f"{layer}/{module} imports {dep}"
               for layer, module, path in _modules()
               for dep in _upward(layer, path)]
        assert not bad, "imports pointing upward:\n  " + "\n  ".join(bad)

    def test_core_touches_nothing_outside_core(self):
        # core is the one layer with no excuse: pure logic, no Gmail, no
        # sheet, no network. If it needs something from above, the something
        # is in the wrong place.
        bad = []
        for layer, module, path in _modules():
            if layer != "core":
                continue
            for dep in _local_imports(path):
                if _layer_of(dep) not in (None, "core"):
                    bad.append(f"core/{module} imports {_layer_of(dep)}/{dep}")
        assert not bad, "core reaching outside itself:\n  " + "\n  ".join(bad)

    def test_every_module_on_disk_has_a_declared_layer(self):
        # A module nobody placed is a module nobody is checking.
        undeclared = [f"{layer}/{m}" for layer, m, _ in _modules()
                      if m not in WHERE]
        assert not undeclared, f"not in architecture.LAYOUT: {undeclared}"

    def test_every_declared_module_exists(self):
        on_disk = {m for _, m, _ in _modules()}
        missing = sorted(set(WHERE) - on_disk)
        assert not missing, f"declared but not on disk: {missing}"


class TestTheCheckItself:
    """The check above, shown to fail on a violation.

    It has passed while broken twice: once when the layers became packages
    and `from outreach import draft_writer` read as a module named
    "outreach", and once more would have followed when they moved under
    `jobagent.`. So every spelling of an upward import is planted here and
    must be caught.
    """

    SPELLINGS = [
        "from jobagent.outreach import draft_writer",
        "from jobagent.outreach.draft_writer import OUT_DIR",
        "import jobagent.outreach.draft_writer",
        "from jobagent import outreach",
        "from ..outreach import draft_writer",
        "def later():\n    from jobagent.outreach import draft_writer",
    ]

    def test_every_spelling_of_an_upward_import_is_caught(self, tmp_path):
        missed = []
        for i, line in enumerate(self.SPELLINGS):
            path = tmp_path / f"planted_{i}.py"
            path.write_text(line + "\n")
            if not _upward("core", str(path)):
                missed.append(line)
        assert not missed, f"upward imports the check cannot see: {missed}"

    def test_a_downward_import_is_not_flagged(self, tmp_path):
        path = tmp_path / "fine.py"
        path.write_text("from jobagent.core import company_id\n"
                        "from jobagent.store import sheets\n")
        assert _upward("outreach", str(path)) == []
