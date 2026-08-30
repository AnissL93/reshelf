from book_organizer.metadata.normalization import (
    normalize_author,
    normalize_language,
    normalize_title,
)


def test_title_spec_example():
    assert normalize_title("The Three-Body Problem: A Novel") == "three body problem"


def test_title_strips_edition_and_format_markers():
    assert normalize_title("Dune (EPUB) 2nd Edition") == "dune"


def test_title_keeps_cjk():
    assert normalize_title("三体") == "三体"


def test_title_handles_none():
    assert normalize_title(None) == ""


def test_author_strips_diacritics_and_case():
    assert normalize_author("Gabriel García Márquez") == "gabriel garcia marquez"


def test_author_keeps_cjk():
    assert normalize_author("刘慈欣") == "刘慈欣"


def test_language_codes():
    assert normalize_language("eng") == "en"
    assert normalize_language("zho") == "zh"
    assert normalize_language("chi") == "zh"
    assert normalize_language("zh-CN") == "zh"
    assert normalize_language("EN") == "en"
    assert normalize_language(None) is None
