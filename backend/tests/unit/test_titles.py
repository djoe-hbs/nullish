from app.api.titles import clean_title


def test_clean_title():
    assert clean_title('"Hudsmers Company Overview."') == "Hudsmers Company Overview"
    assert clean_title("Title: Book Prices\nextra line") == "Book Prices"
    assert clean_title("**AI Services**") == "AI Services"
    assert clean_title("") == ""
