from app.orchestrator.router import parse_route


def test_parse_route():
    assert parse_route('{"intent": "chat", "query": ""}') == ("chat", "")
    assert parse_route('Sure! {"intent":"search","query":"price of Olio"}') == (
        "search", "price of Olio")
    assert parse_route('{"intent": "sites_overview"}') == ("sites_overview", "")
    assert parse_route('{"intent": "nonsense"}') is None
    assert parse_route("not json at all") is None
