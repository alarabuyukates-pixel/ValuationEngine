"""Build the downloadable project, excluding hosting identity and user workbooks."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
output = root / 'dist' / 'source.zip'
with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if not path.is_file() or path == output:
            continue
        if any(part in ('.git', '.openai', '__pycache__', '.venv', 'node_modules', '.sites-runtime') for part in relative.parts):
            continue
        if path.suffix.lower() in ('.xlsx', '.xls', '.pyc'):
            continue
        archive.write(path, 'bondflow/' + str(relative))
print('Created source download: dist/source.zip')
