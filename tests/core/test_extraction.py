from __future__ import annotations

from core.extraction.appendix_number import find_appendix_numbers


def test_single_appendix_number() -> None:
    assert find_appendix_numbers("בתחתית העמוד מופיע נספח 101 בלבד") == ["101"]


def test_multiple_appendix_numbers_comma_separated() -> None:
    assert find_appendix_numbers("נספחים 101, 102") == ["101", "102"]


def test_multiple_appendix_numbers_with_vav() -> None:
    assert find_appendix_numbers("נספחים 101 ו-102") == ["101", "102"]


def test_duplicate_mentions_across_pages_deduplicated() -> None:
    text = "עמוד ראשון\nנספח 101\n\nעמוד שני\nנספח 101"
    assert find_appendix_numbers(text) == ["101"]


def test_no_appendix_number_present() -> None:
    assert find_appendix_numbers("טקסט רגיל בלי שום אזכור") == []


def test_appendix_word_without_trailing_number() -> None:
    assert find_appendix_numbers("ראה נספח בהמשך המסמך") == []


def test_real_ocr_sample_from_migdal() -> None:
    # Reproduces the tail of the real OCR output validated against a live
    # Migdal document (7736_101.pdf).
    text = (
        "יהיה בעל הפוליסה זכאי להחזר של תוספת הפרמיה ששולמה ממועד\n"
        "משלוח ההודעה האמורה, ובכפוף להיקף הסילוק שיבקש.\n\n"
        "נספח 101\n"
    )
    assert find_appendix_numbers(text) == ["101"]
