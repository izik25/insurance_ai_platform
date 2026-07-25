from __future__ import annotations

from core.pdf_processing.reading_order import reconstruct_rtl_line_order


def test_reconstructs_confirmed_real_world_case() -> None:
    # Exact word bboxes read off a real Hachshara document (page 1, the
    # "מספר נספח 531" header). get_text()'s plain-text stream emits this as
    # "531 נספח מספר" - digits before "נספח" - even though these three
    # words share one y-band and read right-to-left visually.
    words = [
        (312.0, 112.0, 340.0, 127.0, "נספח", 0, 0, 0),
        (280.0, 112.0, 309.0, 127.0, "מספר", 0, 0, 1),
        (255.0, 112.0, 277.0, 127.0, "531", 0, 0, 2),
    ]
    assert reconstruct_rtl_line_order(words) == "נספח מספר 531"


def test_empty_input_returns_empty_string() -> None:
    assert reconstruct_rtl_line_order([]) == ""


def test_groups_multiple_lines_by_y_proximity() -> None:
    words = [
        # line 1 (y around 90)
        (100.0, 90.0, 130.0, 105.0, "world", 0, 0, 0),
        (140.0, 91.0, 170.0, 106.0, "hello", 0, 0, 1),
        # line 2 (y around 200, clearly separate)
        (100.0, 200.0, 130.0, 215.0, "two", 0, 1, 0),
        (140.0, 201.0, 170.0, 216.0, "line", 0, 1, 1),
    ]
    result = reconstruct_rtl_line_order(words)
    lines = result.split("\n")
    assert lines[0] == "hello world"
    assert lines[1] == "line two"


def test_y_tolerance_controls_line_clustering() -> None:
    words = [
        (100.0, 90.0, 130.0, 105.0, "a", 0, 0, 0),
        (100.0, 95.0, 130.0, 110.0, "b", 0, 0, 1),
    ]
    assert reconstruct_rtl_line_order(words, y_tolerance=10.0) == "a b"
    # With zero tolerance, a 5pt y-difference splits them into two lines.
    assert reconstruct_rtl_line_order(words, y_tolerance=0.0) == "a\nb"
