"""Create a clean legacy add-on ZIP installable through Install from Disk."""

import ast
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


root = Path(__file__).resolve().parent
init_path = root / "retopo_tool" / "__init__.py"


def get_addon_version(init_file: Path) -> str:
    tree = ast.parse(init_file.read_text(encoding="utf-8"))

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "bl_info":
                    bl_info = ast.literal_eval(node.value)
                    return ".".join(map(str, bl_info["version"]))

    raise RuntimeError("Could not find bl_info['version'] in __init__.py")


version = get_addon_version(init_path)

destination = root / "zips" / f"retopo_tool-{version}.zip"
destination.parent.mkdir(parents=True, exist_ok=True)

with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for path in sorted((root / "retopo_tool").glob("*.py")):
        archive.write(path, path.relative_to(root))

    for path in sorted((root / "retopo_tool" / "assets").glob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(root))

    archive.write(root / "README.md", "retopo_tool/README.md")

with ZipFile(destination) as archive:
    assert archive.testzip() is None
    assert "retopo_tool/__init__.py" in archive.namelist()
    assert "retopo_tool/assets/retopo_icon.dat" in archive.namelist()

print(destination)