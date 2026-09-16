from drawing2step.revb_ocr import document_ai_tokens, fixed_tiles, map_tokens


def test_overlapping_tiles_cover_full_page():
    tiles = fixed_tiles(4200, 3100)
    assert tiles[0] == (0, 0, 2048, 2048)
    assert tiles[1][0] < tiles[0][2]
    assert max(t[2] for t in tiles) == 4200
    assert max(t[3] for t in tiles) == 3100


def test_ocr_coordinates_map_back_to_full_page_and_keep_source_token():
    doc = {
        "text": "Ø80",
        "pages": [
            {
                "tokens": [
                    {
                        "layout": {
                            "textAnchor": {"textSegments": [{"endIndex": 3}]},
                            "boundingPoly": {
                                "normalizedVertices": [{"x": 0.1, "y": 0.2}, {"x": 0.5, "y": 0.6}]
                            },
                        }
                    }
                ]
            }
        ],
    }
    tokens = map_tokens(doc, (100, 200, 600, 700), 1000, 1000, "tile1")
    assert tokens[0]["box"] == [300, 150, 500, 350]
    assert tokens[0]["raw_text"] == "Ø80"
    assert tokens[0]["id"] == "tile1-1"


def test_unpinned_or_redirectable_processor_is_unavailable(tmp_path):
    for processor in (
        "",
        "https://example.org/processor",
        "projects/p/locations/us/processors/p/processorVersions/latest",
    ):
        r = document_ai_tokens(tmp_path / "not-opened.png", tmp_path, processor)
        assert r["metadata"]["status"] == "UNAVAILABLE"
        assert r["tokens"] == []
