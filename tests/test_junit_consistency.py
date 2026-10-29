"""JUnit reports must agree with their test cases before they can describe success."""

import pytest

from poolhouse.activity.gate import read_counts


@pytest.mark.parametrize("xml", [
    '<testsuite tests="1" failures="0"><testcase><failure/></testcase></testsuite>',
    '<testsuite tests="1" failures="1"><testcase/></testsuite>',
    '<testsuite tests="1"><testcase><failure/><error/></testcase></testsuite>',
    '<testsuite tests="2"><testcase/></testsuite>',
    '<testsuite tests="-1"/>',
    '<testsuites/>',
    '<testsuite',
])
def test_invalid_reports_are_rejected(tmp_path, xml):
    report = tmp_path / "report.xml"
    report.write_text(xml)
    with pytest.raises(ValueError):
        read_counts(report)


def test_multiple_suites_counts_every_case(tmp_path):
    report = tmp_path / "report.xml"
    report.write_text('<testsuites><testsuite tests="1"><testcase/></testsuite>'
                      '<testsuite tests="1" skipped="1"><testcase><skipped/></testcase></testsuite></testsuites>')
    assert read_counts(report) == {"passed": 1, "failed": 0, "errors": 0, "skipped": 1}
