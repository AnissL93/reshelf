from book_organizer.metadata.isbn import (
    find_isbns,
    is_valid_isbn10,
    is_valid_isbn13,
    isbn10_to_isbn13,
    normalize_isbn,
)


def test_valid_isbn13():
    assert is_valid_isbn13("9780123456472")
    assert is_valid_isbn13("9780765382030")
    assert not is_valid_isbn13("9780123456473")  # bad check digit
    assert not is_valid_isbn13("978012345647")  # too short


def test_valid_isbn10():
    assert is_valid_isbn10("0306406152")
    assert is_valid_isbn10("080442957X")
    assert not is_valid_isbn10("0306406153")


def test_isbn10_to_isbn13():
    assert isbn10_to_isbn13("0306406152") == "9780306406157"


def test_normalize_spec_example():
    assert normalize_isbn("ISBN 978-0-123456-47-2") == "9780123456472"


def test_normalize_isbn10_converts():
    assert normalize_isbn("ISBN-10: 0-306-40615-2") == "9780306406157"


def test_normalize_invalid_returns_none():
    assert normalize_isbn("9780123456473") is None
    assert normalize_isbn("not an isbn") is None


def test_find_isbns_in_text():
    text = "see ISBN 978-0-765-38203-0 and again 9780765382030, junk 12345"
    assert find_isbns(text) == ["9780765382030"]
    assert find_isbns(None) == []
