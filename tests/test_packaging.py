import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "privasheet"

# Import names that differ from the distribution name that provides them.
DISTRIBUTION_OF = {"PIL": "pillow", "multipart": "python-multipart"}


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared_dependencies() -> dict[str, str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    declared = {}
    for requirement in project["dependencies"]:
        name = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement).group(0)
        declared[_normalize(name)] = requirement
    return declared


def _imported_modules(path: Path) -> set[str]:
    """Top-level names imported anywhere in the file, including imports done through importlib."""
    modules = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            called = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            argument = node.args[0]
            if (
                called in {"import_module", "__import__"}
                and isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
            ):
                modules.add(argument.value.split(".")[0])
    return modules


def _third_party_imports() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        for module in _imported_modules(path):
            if module in sys.stdlib_module_names or module in {
                "privasheet",
                "__future__",
            }:
                continue
            found.setdefault(module, set()).add(str(path.relative_to(ROOT)))
    return found


def test_every_third_party_import_is_a_declared_dependency():
    declared = _declared_dependencies()
    missing = {
        module: sorted(files)
        for module, files in _third_party_imports().items()
        if _normalize(DISTRIBUTION_OF.get(module, module)) not in declared
    }
    assert not missing, (
        f"imported by src/privasheet but not in [project] dependencies: {missing}"
    )


def test_the_scan_sees_the_ocr_engine_imports():
    # engine.py loads these through importlib inside a function; a scan that only reads
    # `import` statements would miss them.
    imported = _third_party_imports()
    assert {"rapidocr", "numpy", "PIL"} <= imported.keys()


def test_ocr_dependencies_carry_a_lower_bound():
    declared = _declared_dependencies()
    assert declared["rapidocr"].replace(" ", "") == "rapidocr>=3.9.2"
    assert declared["onnxruntime"].replace(" ", "") == "onnxruntime>=1.30.0"
    assert re.search(r">=\s*\d", declared["numpy"])
