import pytest

from app.preprocess.material import MaterialPreprocessError, MaterialPreprocessor, TimestampedSentence


def _sentences() -> list[TimestampedSentence]:
    return [TimestampedSentence(f"Sentence {index}.", (index - 1) * 10, index * 10) for index in range(1, 10)]


def test_material_is_split_into_three_semantic_parts() -> None:
    material = MaterialPreprocessor().process(
        material_id="m1",
        title="Preset",
        audio_path="data/materials/m1.wav",
        transcript=" ".join(sentence.text for sentence in _sentences()),
        timestamped_sentences=_sentences(),
    )
    assert [sentence.part_no for sentence in material.sentences] == [1, 1, 1, 2, 2, 2, 3, 3, 3]
    assert len(material.part_boundaries) == 3
    assert material.speech_rate_wpm > 0


def test_material_requires_timestamped_sentences() -> None:
    with pytest.raises(MaterialPreprocessError):
        MaterialPreprocessor().process(
            material_id="m1",
            title="Preset",
            audio_path="m1.wav",
            transcript="Only one sentence.",
            timestamped_sentences=[TimestampedSentence("Only one sentence.", 0, 1)],
        )


def test_natural_part_boundaries_are_preferred_for_splitting() -> None:
    # 12 sentences; exact-third time targets fall near sentence 5 / 9, but
    # natural semantic boundaries are declared at 4 and 8 (within tolerance).
    sentences = [TimestampedSentence(f"Sentence {index}.", (index - 1) * 10, index * 10) for index in range(1, 13)]
    material = MaterialPreprocessor().process(
        material_id="m1",
        title="Preset",
        audio_path="m1.wav",
        transcript=" ".join(sentence.text for sentence in sentences),
        timestamped_sentences=sentences,
        natural_part_boundaries=[4, 8],
    )
    assert [sentence.part_no for sentence in material.sentences] == [1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3]

