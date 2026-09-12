"""Build a Blender extension ZIP with Python 3.11 or newer."""

import tomllib
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


root = Path(__file__).resolve().parent
source = root / "topopot_retopo_tool"
manifest_path = source / "blender_manifest.toml"


with manifest_path.open("rb") as handle:
    manifest = tomllib.load(handle)
version = manifest["version"]

destination = root / "zips" / f"{manifest['id']}-{version}.zip"
destination.parent.mkdir(parents=True, exist_ok=True)

with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    archive.write(manifest_path, "blender_manifest.toml")
    for path in sorted(source.glob("*.py")):
        archive.write(path, path.relative_to(source))

    for path in sorted((source / "assets").rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(source))

    archive.write(root / "README.md", "README.md")

with ZipFile(destination) as archive:
    assert archive.testzip() is None
    assert {"blender_manifest.toml", "__init__.py", "topology.py",
            "assets/retopo_icon.dat", "README.md"}.issubset(archive.namelist())

print(destination)
