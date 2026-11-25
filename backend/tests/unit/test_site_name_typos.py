from app.db.models import Site
from app.orchestrator.answer import edit_distance, fix_site_name_typos

SITES = [Site(name="Hudsmers"), Site(name="Books to Scrape"), Site(name="GTA 6")]


def test_edit_distance_counts_swaps_as_one():
    assert edit_distance("hudmser", "hudsmers") == 2  # swap + missing letter
    assert edit_distance("hudsmers", "hudsmers") == 0
    assert edit_distance("abc", "acb") == 1


def test_fixes_misspelled_site_names_only():
    assert fix_site_name_typos("Hudmser Business Solutions", SITES) == \
        "Hudsmers Business Solutions"
    assert fix_site_name_typos("Wat about hudsmrs?", SITES) == "Wat about Hudsmers?"
    assert fix_site_name_typos("Do the books scrape fast?", SITES) == "Do the books scrape fast?"
    # Ordinary words are never "corrected" (not within 1-2 edits of a site name word).
    q = "What does the company offer for students and business owners?"
    assert fix_site_name_typos(q, SITES) == q
