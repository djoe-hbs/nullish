import pytest

from app.orchestrator.answer import is_smalltalk


@pytest.mark.parametrize("text", [
    "Hi", "hi!", "Hiii", "hello there", "Hey", "good morning", "Good evening!",
    "thanks", "Thank you so much", "thx", "ok", "cool, thanks", "bye", "see you later",
    "how are you?", "Who are you?", "what can you do?", "hi, how are you", "ok thanks bye",
    "Namaste",
])
def test_smalltalk(text):
    assert is_smalltalk(text)


@pytest.mark.parametrize("text", [
    "hi, what is the return policy?", "Hello, where are Hudsmers offices?",
    "Hudsmers", "Wat about hudsmers", "thanks, and what does the book cost?",
    "What services do they offer?", "price of A Light in the Attic", "",
    "who are hudsmers", "what is this book about",
])
def test_not_smalltalk(text):
    assert not is_smalltalk(text)
