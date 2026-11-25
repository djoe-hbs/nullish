from app.orchestrator.answer import drop_unsupported


def test_drop_unsupported_sentences():
    a = "Costs £12 [1]. It is cheap [9]. Ships fast [2][7].\n- Red [3]\n- Free gift [8]\nThanks."
    assert drop_unsupported(a, 3) == "Costs £12 [1]. Ships fast [2][7].\n- Red [3]\nThanks."
    assert drop_unsupported("Only junk [9].", 3) == ""
    assert drop_unsupported("No citations here.", 3) == "No citations here."
