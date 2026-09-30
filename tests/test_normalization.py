from reshelf.metadata.normalization import (
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
    from reshelf.metadata.normalization import short_title

    assert (
        short_title("The Cultural Revolution: A People's History, 1962-1976")
        == "The Cultural Revolution"
    )
    assert short_title("一口气读完的人体秘密（套装共3册）") == "一口气读完的人体秘密"
    assert short_title("《周易梅花数》诠译") == "《周易梅花数》诠译"
    assert short_title("Dune") == "Dune"


def test_search_author():
    from reshelf.metadata.normalization import search_author

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


def test_parse_filename_takes_author_from_every_naming_scheme():
    from reshelf.metadata.normalization import parse_filename

    assert parse_filename("香港电影血与骨 (汤祯兆) (Z-Library)") == ("香港电影血与骨", "汤祯兆")
    assert parse_filename("中國回春秘訣 (將門文物編輯部) (Z-Library)-1") == ("中國回春秘訣", "將門文物編輯部")
    assert parse_filename("佛教小百科-文化[佟洵][2011]") == ("佛教小百科-文化", "佟洵")
    assert parse_filename(
        "张双兵 - 炮楼里的女人——山西日军性奴隶调查实录 (“苏人文学”系列) (2011, 江苏人民出版社) - libgen.li"
    ) == ("炮楼里的女人——山西日军性奴隶调查实录", "张双兵")
    assert parse_filename(
        "[汉译世界学术名著丛书]A0406 第一哲学沉思集 ([法]笛卡尔；庞景仁译) (Z-Library)"
    ) == ("第一哲学沉思集", "[法]笛卡尔；庞景仁译")
    assert parse_filename("57-终结贫穷之路中国和印度发展战略比较") == ("终结贫穷之路中国和印度发展战略比较", None)
    assert parse_filename("佐藤可士和的超整理术 -- 佐藤可士和 -- 2009") == ("佐藤可士和的超整理术", "佐藤可士和")
    assert parse_filename("Basic topology") == ("Basic topology", None)


def test_is_junk_title():
    from reshelf.metadata.normalization import is_junk_title

    for junk in ["SSReader Print.", "Crack by RAOGY.", "print", "a", "!00001.pdg", "<4D6963>", "13169674", None]:
        assert is_junk_title(junk), junk
    for real in ["苏美尔神话", "Deflation and Liberty", "Dune"]:
        assert not is_junk_title(real), real


def test_split_volume_and_drop_subtitle():
    from reshelf.metadata.normalization import drop_subtitle, split_volume

    assert split_volume("胡适文集 02") == ("胡适文集", "2")
    assert split_volume("胡适文集(11)") == ("胡适文集", "11")
    assert split_volume("胡适日记全编007") == ("胡适日记全编", "7")
    assert split_volume("资治通鉴卷二十一") == ("资治通鉴", "21")
    assert split_volume("明朝那些事儿(下)") == ("明朝那些事儿", "下")
    for title in ["天下", "1984", "天下第一", "三体"]:
        assert split_volume(title) == (title, None)
    assert drop_subtitle("战争改变历史 1500年以来的军事技术") == "战争改变历史"
    assert drop_subtitle("炮楼里的女人——山西日军性奴隶调查实录") == "炮楼里的女人"
    assert drop_subtitle("Basic topology") == "Basic topology"
