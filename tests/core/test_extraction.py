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


def test_ocr_misread_of_nispach_as_natpach() -> None:
    # Real corpus finding: a Migdal document (8049_512701095.pdf) whose real
    # appendix number, 970-971, was printed clearly enough for OCR to read
    # the digits correctly, but the OCR engine misread the label itself
    # ("נספח" -> "נטפח", ס confused for ט) - so the old regex (which only
    # recognized "נספח") found nothing and the extractor fell back to a
    # useless 9-digit filename hint instead of the real number.
    assert find_appendix_numbers("נטפח 970-971") == ["970", "971"]


def test_appendix_mention_with_mas_abbreviation() -> None:
    # Real corpus finding: a Migdal document (8148_533200291.pdf) whose
    # footer read "נספח מס'-801-806/91" - the old regex required the number
    # to follow "נספח" directly and didn't know about the "מס'" ("no.")
    # abbreviation some documents insert in between.
    assert find_appendix_numbers("נספח מס'-801-806/91") == ["801", "806"]
