"""Create a clean legacy add-on ZIP installable through Install from Disk."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parent
destination = root / "retopo_pool-1.2.0.zip"
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for path in sorted((root / "retopo_pool").glob("*.py")):
        archive.write(path, path.relative_to(root))
    archive.write(root / "README.md", "retopo_pool/README.md")
with ZipFile(destination) as archive:
    assert archive.testzip() is None
    assert "retopo_pool/__init__.py" in archive.namelist()
print(destination)
