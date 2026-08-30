from book_organizer.metadata.normalization import (
    clean_text,
    normalize_author,
    normalize_language,
    normalize_title,
    title_from_filename,
)


def test_clean_text_strips_control_chars():
    assert clean_text("The Cultural Revolution\x00") == "The Cultural Revolution"
    assert clean_text("a\x00b\x1fc\td") == "a b c d"
    assert clean_text("  \x00 ") is None
    assert clean_text(None) is None


def test_title_from_filename_annas_archive():
    stem = "佐藤可士和的超整理术 -- 佐藤可士和 -- 2009 -- 江苏美术出版社 -- 7534427908 -- 311815d"
    assert title_from_filename(stem) == "佐藤可士和的超整理术"


def test_title_from_filename_zlibrary():
    stem = "衍射、傅里叶光学及成像 (埃尔索伊) (Z-Library)"
    assert title_from_filename(stem) == "衍射、傅里叶光学及成像"


def test_title_from_filename_plain():
    assert title_from_filename("Basic topology") == "Basic topology"


def test_short_title():
    from book_organizer.metadata.normalization import short_title

    assert (
        short_title("The Cultural Revolution: A People's History, 1962-1976")
        == "The Cultural Revolution"
    )
    assert short_title("一口气读完的人体秘密（套装共3册）") == "一口气读完的人体秘密"
    assert short_title("《周易梅花数》诠译") == "《周易梅花数》诠译"
    assert short_title("Dune") == "Dune"


def test_search_author():
    from book_organizer.metadata.normalization import search_author

    assert search_author("（美）MATTHEW MCKAY，JEFFREY C.WOOD著") == "MATTHEW MCKAY"
    assert search_author("加文·弗朗西斯; 悉达多•穆克吉") == "加文·弗朗西斯"
    assert search_author("Frank Dikötter") == "Frank Dikötter"
    assert search_author("（美）") is None


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
