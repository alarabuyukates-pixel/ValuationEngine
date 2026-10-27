"""Download pinned, SHA-256 verified browser dependencies. No pip packages needed."""
from pathlib import Path
import hashlib
import io
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'dist' / 'vendor'


def download(url, expected):
    with urllib.request.urlopen(url, timeout=90) as response:
        content = response.read()
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError(f'Checksum mismatch for {url}')
    return content


def main():
    VENDOR.mkdir(parents=True, exist_ok=True)
    xlsx = download(
        'https://cdn.sheetjs.com/xlsx-0.20.3/package/dist/xlsx.full.min.js',
        'cc015130aa8521e7f088f88898eba949ccdcbfb38df0bd129b44b7273c3a6f41')
    (VENDOR / 'xlsx.full.min.js').write_bytes(xlsx)
    content = download(
        'https://registry.npmjs.org/pyodide/-/pyodide-0.27.7.tgz',
        '89d5d952675eb08e11286e52c7238f1248ce71726e896db9703e73bef23dc343')
    target = VENDOR / 'pyodide'
    target.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(content), mode='r:gz') as archive:
        for member in archive.getmembers():
            # Distribution contains only root-level regular files under package/.
            if member.isfile() and member.name.startswith('package/'):
                relative = Path(member.name).relative_to('package')
                if len(relative.parts) != 1:
                    raise ValueError('Unexpected distribution path')
                (target / relative).write_bytes(archive.extractfile(member).read())
    print('Pinned SheetJS and Pyodide browser dependencies are ready.')


if __name__ == '__main__':
    main()
