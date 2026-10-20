"""Release library aliases are materialized without archive path escapes."""

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from poolhouse import tar_libraries
from poolhouse.net.sniff import audit_archive
from poolhouse.safenames import Unsafe


class LibraryArchiveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def archive(self, rows):
        path = self.root / "release.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name, kind, value in rows:
                item = tarfile.TarInfo(name)
                item.type = kind
                item.mode = 0o755
                if kind == tarfile.REGTYPE:
                    item.size = len(value)
                    archive.addfile(item, io.BytesIO(value))
                else:
                    item.linkname = value
                    archive.addfile(item)
        return path

    def test_soname_chain_is_materialized_and_generic_audit_stays_strict(self):
        archive = self.archive([
            ("bin/libggml.so", tarfile.SYMTYPE, "libggml.so.0"),
            ("bin/libggml.so.0", tarfile.SYMTYPE, "libggml.so.0.1"),
            ("bin/libggml.so.0.1", tarfile.REGTYPE, b"library"),
        ])
        self.assertTrue(audit_archive(archive))
        self.assertFalse(audit_archive(archive, library_links=True))
        into = self.root / "unpacked"
        tar_libraries.unpack(archive, into)
        for name in ("libggml.so", "libggml.so.0", "libggml.so.0.1"):
            found = into / "bin" / name
            self.assertFalse(found.is_symlink())
            self.assertEqual(found.read_bytes(), b"library")

    def test_macos_dylib_chain_is_materialized_only_in_explicit_library_flow(self):
        archive = self.archive([
            ("bin/libcore.dylib", tarfile.SYMTYPE, "libcore.0.dylib"),
            ("bin/libcore.0.dylib", tarfile.SYMTYPE, "libcore.0.1.dylib"),
            ("bin/libcore.0.1.dylib", tarfile.REGTYPE, b"library"),
        ])
        self.assertTrue(audit_archive(archive))
        self.assertFalse(audit_archive(archive, library_links=True))
        into = self.root / "unpacked"
        tar_libraries.unpack(archive, into)
        for name in ("libcore.dylib", "libcore.0.dylib", "libcore.0.1.dylib"):
            found = into / "bin" / name
            self.assertFalse(found.is_symlink())
            self.assertEqual(found.read_bytes(), b"library")

    def test_macos_aliases_cannot_escape_or_point_at_nonlibraries(self):
        hostile = [
            [("bin/lib.dylib", tarfile.SYMTYPE, "/outside/lib.dylib")],
            [("bin/lib.dylib", tarfile.SYMTYPE, "../../outside/lib.dylib")],
            [("bin/lib.dylib", tarfile.SYMTYPE, "missing.dylib")],
            [("bin/lib.dylib", tarfile.SYMTYPE, "other.dylib"),
             ("bin/other.dylib", tarfile.SYMTYPE, "lib.dylib")],
            [("bin/lib.dylib", tarfile.SYMTYPE, "tool"),
             ("bin/tool", tarfile.REGTYPE, b"executable")],
            [("bin/lib.dylib.sh", tarfile.SYMTYPE, "lib.dylib"),
             ("bin/lib.dylib", tarfile.REGTYPE, b"library")],
        ]
        for rows in hostile:
            with self.subTest(rows=rows):
                source = self.archive(rows)
                self.assertTrue(audit_archive(source, library_links=True))
                with self.assertRaises(Unsafe):
                    tar_libraries.unpack(source, self.root / "hostile")
                self.assertFalse((self.root / "hostile").exists())

    def test_hostile_links_and_devices_are_refused_before_writing(self):
        hostile = [
            [("bin/lib.so", tarfile.SYMTYPE, "/outside/lib.so")],
            [("bin/lib.so", tarfile.SYMTYPE, "../../outside/lib.so")],
            [("bin/lib.so", tarfile.SYMTYPE, "missing.so")],
            [("bin/lib.so", tarfile.SYMTYPE, "other.so"),
             ("bin/other.so", tarfile.SYMTYPE, "lib.so")],
            [("bin/lib.so", tarfile.CHRTYPE, "")],
            [("bin/lib.so", tarfile.LNKTYPE, "bin/lib.so.1")],
            [("../../outside", tarfile.REGTYPE, b"escape")],
            [("bin/link", tarfile.SYMTYPE, "lib.so.1")],
            [("bin/lib.so", tarfile.SYMTYPE, "directory.so"),
             ("bin/directory.so", tarfile.DIRTYPE, "")],
            [("bin/lib.so", tarfile.REGTYPE, b"first"),
             ("bin/lib.so", tarfile.REGTYPE, b"second")],
            [("bin/lib.so", tarfile.SYMTYPE, "lib.so.1"),
             ("bin/lib.so/child", tarfile.REGTYPE, b"nested"),
             ("bin/lib.so.1", tarfile.REGTYPE, b"library")],
        ]
        for rows in hostile:
            with self.subTest(rows=rows):
                archive = self.archive(rows)
                into = self.root / "hostile"
                self.assertTrue(audit_archive(archive, library_links=True))
                with self.assertRaises(Unsafe):
                    tar_libraries.unpack(archive, into)
                self.assertFalse(into.exists())

    def test_materialized_alias_bytes_are_charged_to_the_size_limit(self):
        archive = self.archive([
            ("lib.so", tarfile.SYMTYPE, "lib.so.1"),
            ("lib.so.1", tarfile.REGTYPE, b"library"),
        ])
        with patch.object(tar_libraries, "MOST_UNPACKED", 10), self.assertRaises(Unsafe):
            tar_libraries.unpack(archive, self.root / "limited")

    def test_entry_count_is_bounded_before_unpacking(self):
        archive = self.archive([(f"file{index}", tarfile.REGTYPE, b"data") for index in range(3)])
        with patch.object(tar_libraries, "MOST_ENTRIES", 2), self.assertRaisesRegex(Unsafe, "too many entries"):
            tar_libraries.unpack(archive, self.root / "limited")


if __name__ == "__main__":
    unittest.main()
