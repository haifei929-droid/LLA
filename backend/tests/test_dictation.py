from app.core.dictation import DictationErrorType, evaluate_dictation


def test_defined_writing_equivalents_are_exact() -> None:
    result = evaluate_dictation("I'm sure you would've gone.", "I am sure you would have gone")
    assert result.is_exact_match
    assert result.errors == ()


def test_dictation_classifies_errors_and_active_blank() -> None:
    result = evaluate_dictation("The running dogs don't stop.", "The runing dog ____ stop")
    error_types = {error.error_type for error in result.errors}
    assert DictationErrorType.SPELLING in error_types
    assert DictationErrorType.WORD_FORM in error_types
    assert DictationErrorType.ACTIVE_BLANK in error_types


def test_missing_word_is_not_spelling_error() -> None:
    result = evaluate_dictation("The quick brown fox", "The quick fox")
    assert any(error.error_type == DictationErrorType.MISS for error in result.errors)


def test_dictation_classifies_irregular_word_form() -> None:
    result = evaluate_dictation("She went home.", "She go home.")

    assert result.is_exact_match is False
    assert result.errors[0].error_type == DictationErrorType.WORD_FORM
