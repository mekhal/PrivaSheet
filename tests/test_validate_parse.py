"""Source parsing and canonical review values (design sections 4 and 6)."""

import pytest

from privasheet.validate.parse import (
    is_canonical_date,
    is_canonical_decimal,
    parse_date,
    parse_decimal,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1,284.00", "1284.00"),
        ("(12.50)", "-12.50"),
        ("$ 5", "5"),
        ("  5 USD\t", "5"),
        ("EUR12.3400", "12.3400"),
        ("€5", "5"),
        ("5£", "5"),
        ("¥ 5", "5"),
        ("5 ฿", "5"),
        ("XYZ5", "5"),
        ("$(12.50)", "-12.50"),
        ("(12.50) USD", "-12.50"),
        ("(-12.50)", "-12.50"),
        ("-1,234.0001", "-1234.0001"),
        ("0005.00", "5.00"),
        ("0", "0"),
        ("-0.00", "-0.00"),
        ("999,999,999,999,999.9999", "999999999999999.9999"),
    ],
)
def test_parse_decimal_valid(text, expected):
    assert parse_decimal(text) == expected
    assert is_canonical_decimal(expected)


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        "12,34",
        "1234,567",
        "1,,234",
        "1,234,56",
        "1.234,56",
        "1.",
        ".5",
        "1.12345",
        "1000000000000000",
        "0000000000000000",
        "1,000,000,000,000,000",
        "+5",
        "--5",
        "1e3",
        "NaN",
        "Infinity",
        "(5",
        "5)",
        "((5))",
        "-(5)",
        "( 5)",
        "(5 )",
        "($5)",
        "$5 USD",
        "$$5",
        "usd5",
        "US5",
        "USDD5",
        "5 USD EUR",
        "1 234",
        "- 5",
        "5\n6",
        "5.0.0",
    ],
)
def test_parse_decimal_invalid(text):
    assert parse_decimal(text) is None


@pytest.mark.parametrize(
    "text,fmt,expected",
    [
        ("01/09/2026", "DD/MM/YYYY", "2026-09-01"),
        ("2026-09-01", "YYYY-MM-DD", "2026-09-01"),
        ("29.02.2024", "DD.MM.YYYY", "2024-02-29"),
        ("01092026", "DDMMYYYY", "2026-09-01"),
        (" 01\t sEp\n26 ", "DD MMM YY", "2026-09-01"),
        ("01 Sep 26", " DD  MMM\tYY ", "2026-09-01"),
        ("01/01/00", "DD/MM/YY", "2000-01-01"),
        ("31/12/99", "DD/MM/YY", "2099-12-31"),
        ("0001-01-01", "YYYY-MM-DD", "0001-01-01"),
        ("9999-12-31", "YYYY-MM-DD", "9999-12-31"),
        ("[01]+(09).2026", "[DD]+(MM).YYYY", "2026-09-01"),
    ],
)
def test_parse_date_valid(text, fmt, expected):
    assert parse_date(text, fmt) == expected
    assert is_canonical_date(expected)


@pytest.mark.parametrize(
    "number,month",
    list(
        enumerate(
            [
                "Jan",
                "Feb",
                "Mar",
                "Apr",
                "May",
                "Jun",
                "Jul",
                "Aug",
                "Sep",
                "Oct",
                "Nov",
                "Dec",
            ],
            start=1,
        )
    ),
)
def test_english_months(number, month):
    assert parse_date(f"01 {month} 2026", "DD MMM YYYY") == f"2026-{number:02d}-01"


@pytest.mark.parametrize(
    "text,fmt",
    [
        ("29/02/2023", "DD/MM/YYYY"),
        ("29/02/1900", "DD/MM/YYYY"),
        ("31/04/2026", "DD/MM/YYYY"),
        ("00/01/2026", "DD/MM/YYYY"),
        ("01/00/2026", "DD/MM/YYYY"),
        ("01/13/2026", "DD/MM/YYYY"),
        ("01/01/0000", "DD/MM/YYYY"),
        ("1/09/2026", "DD/MM/YYYY"),
        ("01/9/2026", "DD/MM/YYYY"),
        ("01/09/26", "DD/MM/YYYY"),
        ("01/09/2026", "DD/MM/YY"),
        ("01-09-2026", "DD/MM/YYYY"),
        ("01 September 2026", "DD MMM YYYY"),
        ("01 Xxx 2026", "DD MMM YYYY"),
        ("01Sep2026", "DD MMM YYYY"),
        ("01/09/2026 extra", "DD/MM/YYYY"),
        ("2026", "YYYY"),
        ("", ""),
        ("01/01/2026", "DD/DD/YYYY"),
    ],
)
def test_parse_date_invalid(text, fmt):
    assert parse_date(text, fmt) is None


@pytest.mark.parametrize(
    "text",
    [
        "0",
        "-0.00",
        "5",
        "-5.0000",
        "999999999999999.9999",
    ],
)
def test_canonical_decimal_valid(text):
    assert is_canonical_decimal(text)


@pytest.mark.parametrize(
    "text",
    [
        " 5",
        "5\n",
        "$5",
        "1,234",
        "(5)",
        "+5",
        "1e2",
        "NaN",
        ".5",
        "5.",
        "5.12345",
        "1000000000000000",
        "",
        None,
        5,
    ],
)
def test_canonical_decimal_invalid(text):
    assert not is_canonical_decimal(text)


@pytest.mark.parametrize(
    "text",
    [
        "2026-9-01",
        "2026-09-1",
        "26-09-01",
        "2026-02-29",
        "0000-01-01",
        "01/09/2026",
        " 2026-09-01",
        "2026-09-01\n",
        "20260901",
        "",
        None,
        5,
    ],
)
def test_canonical_date_invalid(text):
    assert not is_canonical_date(text)
