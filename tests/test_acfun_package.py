import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from package_acfun_publisher import FILES, package


class PackageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.source = self.root / "source"
        self.output = self.root / "skill.zip"
        for name in FILES:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("source", encoding="utf-8")

    def test_only_explicit_sources_enter_archive(self):
        for name in ("data/acfun-session.json", "data/drafts/private.md", ".env",
                     "scripts/__pycache__/acfun_client.pyc", "scripts/private.json"):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("FAKE_PRIVATE_CONTENT", encoding="utf-8")
        package(self.source, self.output)
        with ZipFile(self.output) as archive:
            self.assertEqual(set(archive.namelist()), {f"acfun-publisher/{name}" for name in FILES})
            self.assertEqual(archive.testzip(), None)
            self.assertTrue(all(archive.read(name) == b"source" for name in archive.namelist()))
        original = self.output.read_bytes()
        with self.assertRaises(FileExistsError):
            package(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), original)

    def test_missing_source_fails_before_creating_archive(self):
        (self.source / "SKILL.md").unlink()
        with self.assertRaisesRegex(ValueError, "缺失"):
            package(self.source, self.output)
        self.assertFalse(self.output.exists())

    @unittest.skipIf(sys.platform == "win32", "Windows symlink privileges depend on host settings")
    def test_symlink_cannot_smuggle_credentials_into_source_manifest(self):
        secret = self.root / "session.json"
        secret.write_text("FAKE_PRIVATE_CONTENT", encoding="utf-8")
        path = self.source / "scripts/acfun_client.py"
        path.unlink()
        path.symlink_to(secret)
        with self.assertRaisesRegex(ValueError, "符号链接"):
            package(self.source, self.output)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
