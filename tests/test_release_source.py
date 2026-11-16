"""Installer deltas use the declared game reference, not the compilation inputs."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('release', Path(__file__).resolve().parents[1] / 'scripts/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseSourceTest(unittest.TestCase):
    def test_reference_and_integrity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            reference = root / 'base/installer-source'
            reference.mkdir(parents=True)
            toolchain = root / 'toolchain'
            archives = toolchain / 'base_archives'
            archives.mkdir(parents=True)
            (reference / 'new.dat').write_bytes(b'current game reference')
            (archives / 'new.dat').write_bytes(b'old compilation input')
            (archives / 'same.dat.part0').write_bytes(b'unchanged ')
            (archives / 'same.dat.part1').write_bytes(b'archive')
            joined = root / 'joined'
            joined.write_bytes(b'unchanged archive')
            (reference / 'manifest.json').write_text(json.dumps({'files': {
                'new.dat': {'override': True, 'sha256': release.digest(reference / 'new.dat')},
                'same.dat': {'override': False, 'sha256': release.digest(joined)},
            }}))
            old_root = release.ROOT
            release.ROOT = root
            try:
                self.assertEqual(release.original(toolchain, 'new.dat', root), reference / 'new.dat')
                self.assertEqual(release.original(toolchain, 'same.dat', root).read_bytes(), joined.read_bytes())
                for name in ['unknown.dat', '../new.dat', '..\\new.dat']:
                    with self.assertRaises(ValueError):
                        release.original(toolchain, name, root)
                (archives / 'same.dat.part0').write_bytes(b'changed')
                with self.assertRaises(ValueError):
                    release.original(toolchain, 'same.dat', root)
                (reference / 'new.dat').write_bytes(b'tampered')
                with self.assertRaises(ValueError):
                    release.original(toolchain, 'new.dat', root)
                (reference / 'new.dat').unlink()
                with self.assertRaises(ValueError):
                    release.original(toolchain, 'new.dat', root)
            finally:
                release.ROOT = old_root


if __name__ == '__main__':
    unittest.main()
