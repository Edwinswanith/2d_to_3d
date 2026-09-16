import pytest

from drawing2step.local_ocr import parse_tokens


def test_word_coordinates_preserved_and_no_approval():
    tsv = "level\tleft\ttop\twidth\theight\tconf\ttext\n"
    tsv += "1\t0\t0\t100\t200\t-1\t\n"
    tsv += "5\t10\t20\t30\t40\t89.5\t6.4\n"
    tokens = parse_tokens(tsv, 100, 200)
    assert len(tokens) == 1
    assert tokens[0]["box"] == [100, 100, 300, 400]
    assert tokens[0]["raw_text"] == "6.4"
    assert not tokens[0]["accepted"]


def test_invalid_image_dimensions_rejected():
    with pytest.raises(ValueError):
        parse_tokens("", 0, 200)


def test_literal_inch_quote_does_not_swallow_next_token():
    tsv = "level\tleft\ttop\twidth\theight\tconf\ttext\n"
    tsv += '5\t10\t20\t30\t40\t89.5\t"\n'
    tsv += "5\t40\t20\t30\t40\t89.5\t25.4\n"
    tokens = parse_tokens(tsv, 100, 200)
    assert [t["raw_text"] for t in tokens] == ['"', "25.4"]
