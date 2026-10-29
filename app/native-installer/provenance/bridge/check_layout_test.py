"""The generated-header inventory must match every layout-table field."""
import tempfile
import unittest
from pathlib import Path

from check_layout import NAMES, check, listed


class LayoutInventoryTests(unittest.TestCase):
    def test_missing_added_reordered_and_duplicate_fields_fail(self):
        table_source = (Path(__file__).parent / "layout.h").read_text(encoding="utf-8")
        types = []
        for macro, typename in NAMES.items():
            fields = listed(table_source, macro)
            types.append("typedef struct { " + " ".join("unsigned long " + field + ";" for field in fields) + " } " + typename + ";\n")
        header_source = "".join(types)
        with tempfile.TemporaryDirectory() as root:
            header = Path(root) / "generated.h"
            table = Path(root) / "layout.h"
            header.write_text(header_source, encoding="utf-8")
            table.write_text(table_source, encoding="utf-8")
            check(header, table)
            for altered in (
                table_source.replace("X(archive_fd)", "", 1),
                table_source.replace("X(stage_fd) X(archive_fd)", "X(archive_fd) X(stage_fd)", 1),
                table_source.replace("X(stage_fd)", "X(stage_fd) X(stage_fd)", 1),
            ):
                table.write_text(altered, encoding="utf-8")
                with self.assertRaises(ValueError):
                    check(header, table)
            table.write_text(table_source, encoding="utf-8")
            header.write_text(header_source.replace("unsigned long archive_fd;", "unsigned long archive_fd; unsigned long added;"), encoding="utf-8")
            with self.assertRaises(ValueError):
                check(header, table)


if __name__ == "__main__":
    unittest.main()
