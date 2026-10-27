"""Protect dependency downloads against regressions without requiring network access."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / 'scripts' / 'prepare_vendor.py'
spec = importlib.util.spec_from_file_location('prepare_vendor', path)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class VendorDownloadTests(unittest.TestCase):
    def test_download_accepts_only_expected_digest(self):
        data = b'pinned dependency bytes'
        with patch.object(prepare.urllib.request, 'urlopen', return_value=io.BytesIO(data)) as request:
            result = prepare.download('https://registry.npmjs.org/example', hashlib.sha256(data).hexdigest())
        self.assertEqual(result, data)
        self.assertIsInstance(request.call_args.args[0], prepare.urllib.request.Request)

    def test_checksum_mismatch_is_rejected(self):
        with patch.object(prepare.urllib.request, 'urlopen', return_value=io.BytesIO(b'changed bytes')):
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                prepare.download('https://registry.npmjs.org/example', '0' * 64)

    def test_sheetjs_extracts_and_verifies_expected_file(self):
        data = b'expected spreadsheet library'
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            member = tarfile.TarInfo('package/dist/xlsx.full.min.js')
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        with patch.object(prepare, 'fetch', return_value=stream.getvalue()):
            with patch.object(prepare, 'SHEETJS_SHA256', hashlib.sha256(data).hexdigest()):
                self.assertEqual(prepare.sheetjs_bytes(), data)

    def test_sheetjs_changed_file_is_rejected(self):
        data = b'unexpected spreadsheet library'
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            member = tarfile.TarInfo('package/dist/xlsx.full.min.js')
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
        with patch.object(prepare, 'fetch', return_value=stream.getvalue()):
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                prepare.sheetjs_bytes()


if __name__ == '__main__':
    unittest.main()
