from video_cli_toolkit.rewrite_matcher import (
    build_ranges_from_kept_word_ids,
    match_rewrite_words,
    normalize_rewrite_token,
    tokenize_rewrite_text,
)


def test_normalize_rewrite_token_handles_punctuation_and_number_words() -> None:
    assert normalize_rewrite_token("Sixteen,") == "16"
    assert normalize_rewrite_token("David's") == "davids"


def test_tokenize_rewrite_text_drops_empty_tokens() -> None:
    assert tokenize_rewrite_text(" Hello,   world! ") == ["hello", "world"]


def test_match_rewrite_words_keeps_exact_matches() -> None:
    words = [
        {"id": 0, "word": "hello", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "world", "start": 0.2, "end": 0.5},
    ]

    result = match_rewrite_words(words, "hello world")

    assert result.kept_word_ids == [0, 1]
    assert result.cut_word_ids == []
    assert result.kept_word_count == 2
    assert result.matched_target_count == 2
    assert result.anchored_word_ids == []
    assert result.unmatched_target_tokens == []


def test_match_rewrite_words_handles_punctuation_and_case() -> None:
    words = [
        {"id": 0, "word": "Hello,", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "WORLD!", "start": 0.2, "end": 0.5},
    ]

    result = match_rewrite_words(words, "hello world")

    assert result.kept_word_ids == [0, 1]
    assert result.cut_word_ids == []


def test_match_rewrite_words_handles_number_word_normalization() -> None:
    words = [
        {"id": 0, "word": "chapter", "start": 0.0, "end": 0.3},
        {"id": 1, "word": "16", "start": 0.3, "end": 0.5},
        {"id": 2, "word": "david", "start": 0.5, "end": 0.8},
    ]

    result = match_rewrite_words(words, "chapter sixteen david")

    assert result.kept_word_ids == [0, 1, 2]
    assert result.cut_word_ids == []


def test_match_rewrite_words_supports_target_two_to_source_one_matches() -> None:
    words = [
        {"id": 0, "word": "prohuman", "start": 0.0, "end": 0.3},
        {"id": 1, "word": "value", "start": 0.3, "end": 0.6},
    ]

    result = match_rewrite_words(words, "pro human value")

    assert result.kept_word_ids == [0, 1]
    assert result.cut_word_ids == []
    assert result.matched_target_count == 3


def test_match_rewrite_words_supports_target_one_to_source_two_matches() -> None:
    words = [
        {"id": 0, "word": "note", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "book", "start": 0.2, "end": 0.5},
        {"id": 2, "word": "draft", "start": 0.5, "end": 0.8},
    ]

    result = match_rewrite_words(words, "notebook draft")

    assert result.kept_word_ids == [0, 1, 2]
    assert result.cut_word_ids == []
    assert result.matched_target_count == 2


def test_match_rewrite_words_cuts_deleted_source_words() -> None:
    words = [
        {"id": 0, "word": "keep", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "this", "start": 0.2, "end": 0.4},
        {"id": 2, "word": "remove", "start": 0.4, "end": 0.7},
        {"id": 3, "word": "that", "start": 0.7, "end": 1.0},
    ]

    result = match_rewrite_words(words, "keep that")

    assert result.kept_word_ids == [0, 3]
    assert result.cut_word_ids == [1, 2]


def test_match_rewrite_words_reports_unmatched_target_tokens() -> None:
    words = [
        {"id": 0, "word": "hello", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "world", "start": 0.2, "end": 0.5},
    ]

    result = match_rewrite_words(words, "hello brave world")

    assert result.kept_word_ids == [0, 1]
    assert result.unmatched_target_tokens == ["brave"]


def test_match_rewrite_words_prefers_contiguous_phrase_window_over_scattered_hits() -> None:
    words = [
        {"id": 0, "word": "human", "start": 0.0, "end": 0.1},
        {"id": 1, "word": "lead", "start": 0.1, "end": 0.2},
        {"id": 2, "word": "in", "start": 0.2, "end": 0.3},
        {"id": 3, "word": "beings", "start": 0.3, "end": 0.4},
        {"id": 4, "word": "not", "start": 0.4, "end": 0.5},
        {"id": 5, "word": "human", "start": 0.5, "end": 0.6},
        {"id": 6, "word": "doings,", "start": 0.6, "end": 0.7},
        {"id": 7, "word": "human", "start": 0.7, "end": 0.8},
        {"id": 8, "word": "beings", "start": 0.8, "end": 0.9},
        {"id": 9, "word": "not", "start": 0.9, "end": 1.0},
        {"id": 10, "word": "human", "start": 1.0, "end": 1.1},
        {"id": 11, "word": "doings.", "start": 1.1, "end": 1.2},
    ]

    result = match_rewrite_words(words, "human beings not human doings")

    assert result.kept_word_ids == [7, 8, 9, 10, 11]
    assert result.anchored_word_ids == [7, 8, 9, 10, 11]


def test_match_rewrite_words_prefers_later_duplicate_take() -> None:
    words = [
        {"id": 0, "word": "I", "start": 0.0, "end": 0.1},
        {"id": 1, "word": "really", "start": 0.1, "end": 0.2},
        {"id": 2, "word": "mean", "start": 0.2, "end": 0.3},
        {"id": 3, "word": "it,", "start": 0.3, "end": 0.4},
        {"id": 4, "word": "I", "start": 0.4, "end": 0.5},
        {"id": 5, "word": "really", "start": 0.5, "end": 0.6},
        {"id": 6, "word": "mean", "start": 0.6, "end": 0.7},
        {"id": 7, "word": "it.", "start": 0.7, "end": 0.8},
    ]

    result = match_rewrite_words(words, "I really mean it")

    assert result.kept_word_ids == [4, 5, 6, 7]
    assert result.anchored_word_ids == [4, 5, 6, 7]


def test_build_ranges_from_kept_word_ids_splits_on_cut_words() -> None:
    words = [
        {"id": 0, "word": "hello", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "world", "start": 0.2, "end": 0.5},
        {"id": 2, "word": "cut", "start": 0.5, "end": 0.7},
        {"id": 3, "word": "again", "start": 0.8, "end": 1.1},
    ]

    ranges = build_ranges_from_kept_word_ids(words, [0, 1, 3])

    assert ranges == [{"start": 0.0, "end": 0.5}, {"start": 0.8, "end": 1.1}]


def test_match_rewrite_words_marks_four_word_runs_as_anchors() -> None:
    words = [
        {"id": 0, "word": "but", "start": 0.0, "end": 0.1},
        {"id": 1, "word": "I", "start": 0.1, "end": 0.2},
        {"id": 2, "word": "don't", "start": 0.2, "end": 0.3},
        {"id": 3, "word": "want", "start": 0.3, "end": 0.4},
        {"id": 4, "word": "to", "start": 0.4, "end": 0.5},
        {"id": 5, "word": "be", "start": 0.5, "end": 0.6},
        {"id": 6, "word": "defined", "start": 0.6, "end": 0.8},
    ]

    result = match_rewrite_words(words, "I don't want to be defined")

    assert result.kept_word_ids == [1, 2, 3, 4, 5, 6]
    assert result.anchored_word_ids == [1, 2, 3, 4, 5, 6]


def test_build_ranges_from_kept_word_ids_drops_short_unanchored_islands() -> None:
    words = [
        {"id": 0, "word": "there's", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "an", "start": 0.2, "end": 0.3},
        {"id": 2, "word": "in-between", "start": 0.3, "end": 0.5},
        {"id": 3, "word": "point", "start": 0.5, "end": 0.7},
        {"id": 4, "word": "that", "start": 0.7, "end": 0.8},
        {"id": 5, "word": "we", "start": 0.8, "end": 0.9},
        {"id": 6, "word": "can", "start": 0.9, "end": 1.0},
        {"id": 7, "word": "strike", "start": 1.0, "end": 1.1},
        {"id": 8, "word": "or", "start": 1.1, "end": 1.2},
        {"id": 9, "word": "it's", "start": 1.2, "end": 1.3},
        {"id": 10, "word": "we", "start": 1.3, "end": 1.4},
        {"id": 11, "word": "do", "start": 1.4, "end": 1.5},
        {"id": 12, "word": "work", "start": 1.5, "end": 1.7},
        {"id": 13, "word": "to", "start": 1.7, "end": 1.8},
        {"id": 14, "word": "feed", "start": 1.8, "end": 2.0},
    ]

    ranges = build_ranges_from_kept_word_ids(words, [5, 11, 12, 13, 14], anchored_word_ids=[11, 12, 13, 14])

    assert ranges == [{"start": 1.4, "end": 2.0}]


def test_build_ranges_from_kept_word_ids_snaps_to_nearby_phrase_boundary() -> None:
    words = [
        {"id": 0, "word": "you,", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "but", "start": 0.2, "end": 0.3},
        {"id": 2, "word": "I", "start": 0.3, "end": 0.35},
        {"id": 3, "word": "don't", "start": 0.35, "end": 0.5},
        {"id": 4, "word": "want", "start": 0.5, "end": 0.65},
        {"id": 5, "word": "to", "start": 0.65, "end": 0.75},
        {"id": 6, "word": "be", "start": 0.75, "end": 0.8},
        {"id": 7, "word": "defined", "start": 0.8, "end": 1.0},
        {"id": 8, "word": "by", "start": 1.0, "end": 1.1},
        {"id": 9, "word": "how", "start": 1.1, "end": 1.2},
        {"id": 10, "word": "productive", "start": 1.2, "end": 1.5},
        {"id": 11, "word": "I", "start": 1.5, "end": 1.55},
        {"id": 12, "word": "am.", "start": 1.55, "end": 1.8},
    ]

    ranges = build_ranges_from_kept_word_ids(
        words,
        [7, 8, 9, 10, 11, 12],
        anchored_word_ids=[7, 8, 9, 10, 11, 12],
    )

    assert ranges == [{"start": 0.2, "end": 1.8}]


def test_build_ranges_from_kept_word_ids_splits_kept_run_on_long_silence() -> None:
    words = [
        {"id": 0, "word": "you", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "matter", "start": 0.2, "end": 0.5},
        {"id": 2, "word": "more", "start": 0.5, "end": 0.7},
        {"id": 3, "word": "today", "start": 0.7, "end": 0.9},
        {"id": 4, "word": "dreams", "start": 1.3, "end": 1.5},
        {"id": 5, "word": "still", "start": 1.5, "end": 1.7},
        {"id": 6, "word": "belong", "start": 1.7, "end": 1.9},
        {"id": 7, "word": "here", "start": 1.9, "end": 2.1},
    ]

    ranges = build_ranges_from_kept_word_ids(
        words,
        [0, 1, 2, 3, 4, 5, 6, 7],
        anchored_word_ids=[0, 1, 2, 3, 4, 5, 6, 7],
        max_silence_gap=0.2,
        audio_silences=[{"start": 0.95, "end": 1.25, "duration": 0.3}],
    )

    assert ranges == [{"start": 0.0, "end": 0.9}, {"start": 1.3, "end": 2.1}]


def test_build_ranges_from_kept_word_ids_keeps_short_silence_tail_with_phrase() -> None:
    words = [
        {"id": 0, "word": "world", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "where", "start": 0.2, "end": 0.4},
        {"id": 2, "word": "we're", "start": 0.4, "end": 0.6},
        {"id": 3, "word": "forced", "start": 0.6, "end": 0.8},
        {"id": 4, "word": "to", "start": 0.8, "end": 1.0},
        {"id": 5, "word": "have", "start": 1.0, "end": 1.2},
        {"id": 6, "word": "output.", "start": 1.6, "end": 1.8},
    ]

    ranges = build_ranges_from_kept_word_ids(
        words,
        [0, 1, 2, 3, 4, 5, 6],
        anchored_word_ids=[0, 1, 2, 3, 4, 5, 6],
        max_silence_gap=0.2,
        audio_silences=[{"start": 1.25, "end": 1.55, "duration": 0.3}],
    )

    assert ranges == [{"start": 0.0, "end": 1.8}]


def test_build_ranges_from_kept_word_ids_ignores_word_gap_without_detected_silence() -> None:
    words = [
        {"id": 0, "word": "you", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "matter", "start": 0.2, "end": 0.5},
        {"id": 2, "word": "more", "start": 0.5, "end": 0.7},
        {"id": 3, "word": "today", "start": 0.7, "end": 0.9},
        {"id": 4, "word": "dreams", "start": 1.3, "end": 1.5},
        {"id": 5, "word": "still", "start": 1.5, "end": 1.7},
        {"id": 6, "word": "belong", "start": 1.7, "end": 1.9},
        {"id": 7, "word": "here", "start": 1.9, "end": 2.1},
    ]

    ranges = build_ranges_from_kept_word_ids(
        words,
        [0, 1, 2, 3, 4, 5, 6, 7],
        anchored_word_ids=[0, 1, 2, 3, 4, 5, 6, 7],
        max_silence_gap=0.2,
        audio_silences=[],
    )

    assert ranges == [{"start": 0.0, "end": 2.1}]


def test_build_ranges_from_kept_word_ids_avoids_weak_be_productive_boundary() -> None:
    words = [
        {"id": 0, "word": "when", "start": 0.0, "end": 0.2},
        {"id": 1, "word": "they", "start": 0.2, "end": 0.4},
        {"id": 2, "word": "told", "start": 0.4, "end": 0.6},
        {"id": 3, "word": "you", "start": 0.6, "end": 0.8},
        {"id": 4, "word": "that", "start": 0.8, "end": 1.0},
        {"id": 5, "word": "you", "start": 1.0, "end": 1.2},
        {"id": 6, "word": "have", "start": 1.2, "end": 1.4},
        {"id": 7, "word": "to", "start": 1.4, "end": 1.55},
        {"id": 8, "word": "constantly", "start": 1.55, "end": 1.9},
        {"id": 9, "word": "be,", "start": 2.2, "end": 2.4},
        {"id": 10, "word": "productive", "start": 2.75, "end": 3.1},
        {"id": 11, "word": "to", "start": 3.1, "end": 3.25},
        {"id": 12, "word": "have", "start": 3.25, "end": 3.45},
        {"id": 13, "word": "value.", "start": 3.45, "end": 3.8},
    ]

    ranges = build_ranges_from_kept_word_ids(
        words,
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13],
        anchored_word_ids=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13],
        max_silence_gap=0.35,
        audio_silences=[{"start": 2.45, "end": 2.7, "duration": 0.25}],
    )

    assert ranges == [{"start": 0.0, "end": 3.8}]


def test_build_ranges_from_kept_word_ids_merges_across_short_weak_clause_gap() -> None:
    words = [
        {"id": 0, "word": "I", "start": 0.0, "end": 0.1},
        {"id": 1, "word": "know", "start": 0.1, "end": 0.2},
        {"id": 2, "word": "and", "start": 0.2, "end": 0.3},
        {"id": 3, "word": "trust", "start": 0.3, "end": 0.4},
        {"id": 4, "word": "myself", "start": 0.4, "end": 0.6},
    ]

    ranges = build_ranges_from_kept_word_ids(
        words,
        [0, 1, 3, 4],
        anchored_word_ids=[0, 1, 3, 4],
    )

    assert ranges == [{"start": 0.0, "end": 0.6}]
