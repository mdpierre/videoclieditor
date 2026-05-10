from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


NUMBER_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
}

MATCH_THRESHOLD = 0.78
MERGE_THRESHOLD = 0.84
MIN_ANCHOR_WORDS = 4
PHRASE_LOOKAROUND_WORDS = 6
PAUSE_GAP_THRESHOLD = 0.35
MAX_SILENCE_GAP = 0.2
TERMINAL_PUNCTUATION = (".", "!", "?", ",", ";", ":")
STRONG_END_PUNCTUATION = (".", "!", "?")
SOFT_END_PUNCTUATION = (",", ";", ":")
WEAK_BOUNDARY_END_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "but",
    "for",
    "have",
    "if",
    "in",
    "is",
    "it",
    "of",
    "or",
    "so",
    "than",
    "that",
    "the",
    "their",
    "then",
    "these",
    "they",
    "this",
    "to",
    "was",
    "we",
    "were",
    "you",
    "your",
}
WEAK_BOUNDARY_START_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "but",
    "for",
    "have",
    "if",
    "in",
    "is",
    "it",
    "of",
    "or",
    "so",
    "than",
    "that",
    "the",
    "their",
    "then",
    "these",
    "they",
    "this",
    "to",
    "was",
    "we",
    "were",
    "you",
    "your",
}
MIN_DUPLICATE_SHIFT_WORDS = 4
MAX_DUPLICATE_SHIFT_WORDS = 12
MAX_CLAUSE_BRIDGE_WORDS = 2


@dataclass(frozen=True)
class RewriteMatchResult:
    kept_word_ids: list[int]
    cut_word_ids: list[int]
    anchored_word_ids: list[int]
    source_word_count: int
    target_token_count: int
    kept_word_count: int
    matched_target_count: int
    unmatched_target_tokens: list[str]


def normalize_rewrite_token(text: str) -> str:
    lowered = text.casefold().replace("'", "")
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    tokens = [NUMBER_WORDS.get(token, token) for token in lowered.split()]
    return "".join(tokens)


def tokenize_rewrite_text(text: str) -> list[str]:
    return [token for token in (normalize_rewrite_token(part) for part in text.split()) if token]


def token_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    min_len = min(len(left), len(right))
    if min_len <= 2:
        return 0.0
    if min_len >= 4 and (left in right or right in left):
        return 0.92

    rows = len(left) + 1
    cols = len(right) + 1
    dp = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        dp[i][0] = i
    for j in range(cols):
        dp[0][j] = j

    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if left[i - 1] == right[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )

    return 1 - (dp[-1][-1] / max(len(left), len(right)))


def concat_tokens(tokens: list[str]) -> str:
    return "".join(token for token in tokens if token)


def _build_target_ngram_set(tokens: list[str], size: int) -> set[tuple[str, ...]]:
    if size <= 0 or len(tokens) < size:
        return set()
    return {tuple(tokens[index : index + size]) for index in range(len(tokens) - size + 1)}


def _find_exact_target_windows(source_tokens: list[str], target_tokens: list[str]) -> list[tuple[int, int]]:
    if not target_tokens or len(source_tokens) < len(target_tokens):
        return []
    window_size = len(target_tokens)
    return [
        (start_index, start_index + window_size - 1)
        for start_index in range(len(source_tokens) - window_size + 1)
        if source_tokens[start_index : start_index + window_size] == target_tokens
    ]


def _find_anchor_word_ids(
    words: list[dict[str, Any]],
    kept_word_ids: list[int],
    target_tokens: list[str],
    *,
    min_anchor_words: int = MIN_ANCHOR_WORDS,
) -> list[int]:
    if min_anchor_words <= 1:
        return kept_word_ids[:]

    kept_word_id_set = set(kept_word_ids)
    target_ngrams = _build_target_ngram_set(target_tokens, min_anchor_words)
    if not target_ngrams:
        return []

    anchored_word_ids: set[int] = set()
    current_run: list[dict[str, Any]] = []
    for word in words:
        if int(word["id"]) in kept_word_id_set:
            current_run.append(word)
            continue
        if current_run:
            run_tokens = [normalize_rewrite_token(str(item.get("word", ""))) for item in current_run]
            for index in range(len(run_tokens) - min_anchor_words + 1):
                ngram = tuple(run_tokens[index : index + min_anchor_words])
                if ngram in target_ngrams:
                    anchored_word_ids.update(int(item["id"]) for item in current_run[index : index + min_anchor_words])
            current_run = []

    if current_run:
        run_tokens = [normalize_rewrite_token(str(item.get("word", ""))) for item in current_run]
        for index in range(len(run_tokens) - min_anchor_words + 1):
            ngram = tuple(run_tokens[index : index + min_anchor_words])
            if ngram in target_ngrams:
                anchored_word_ids.update(int(item["id"]) for item in current_run[index : index + min_anchor_words])

    return [word_id for word_id in kept_word_ids if word_id in anchored_word_ids]
def match_rewrite_words(words: list[dict[str, Any]], target_text: str) -> RewriteMatchResult:
    target_tokens = tokenize_rewrite_text(target_text)
    source_tokens = [normalize_rewrite_token(str(word.get("word", ""))) for word in words]
    source_word_ids = [int(word["id"]) for word in words]

    m = len(target_tokens)
    n = len(source_tokens)
    scores = [[0.0] * (n + 1) for _ in range(m + 1)]
    back: list[list[tuple[str, int, int] | None]] = [[None] * (n + 1) for _ in range(m + 1)]

    for i in range(m + 1):
        for j in range(n + 1):
            if i == 0 and j == 0:
                continue

            best = -1.0
            choice: tuple[str, int, int] | None = None

            if i > 0 and scores[i - 1][j] >= best:
                best = scores[i - 1][j]
                choice = ("skip-target", 1, 0)

            if j > 0 and scores[i][j - 1] > best:
                best = scores[i][j - 1]
                choice = ("skip-orig", 0, 1)

            if i > 0 and j > 0:
                score = token_similarity(target_tokens[i - 1], source_tokens[j - 1])
                if score >= MATCH_THRESHOLD and scores[i - 1][j - 1] + score > best:
                    best = scores[i - 1][j - 1] + score
                    choice = ("match-1-1", 1, 1)

            if i > 1 and j > 0:
                score = token_similarity(concat_tokens(target_tokens[i - 2 : i]), source_tokens[j - 1])
                if score >= MERGE_THRESHOLD and scores[i - 2][j - 1] + score > best:
                    best = scores[i - 2][j - 1] + score
                    choice = ("match-2-1", 2, 1)

            if i > 0 and j > 1:
                score = token_similarity(target_tokens[i - 1], concat_tokens(source_tokens[j - 2 : j]))
                if score >= MERGE_THRESHOLD and scores[i - 1][j - 2] + score > best:
                    best = scores[i - 1][j - 2] + score
                    choice = ("match-1-2", 1, 2)

            scores[i][j] = max(best, 0.0)
            back[i][j] = choice

    kept_word_ids: set[int] = set()
    matched_target_indexes: set[int] = set()
    unmatched_target_tokens: list[str] = []
    i = m
    j = n
    while i > 0 or j > 0:
        choice = back[i][j]
        if choice is None:
            break

        name, di, dj = choice
        if name == "match-1-1":
            kept_word_ids.add(source_word_ids[j - 1])
            matched_target_indexes.add(i - 1)
        elif name == "match-2-1":
            kept_word_ids.add(source_word_ids[j - 1])
            matched_target_indexes.update((i - 2, i - 1))
        elif name == "match-1-2":
            kept_word_ids.update((source_word_ids[j - 2], source_word_ids[j - 1]))
            matched_target_indexes.add(i - 1)
        elif name == "skip-target":
            unmatched_target_tokens.append(target_tokens[i - 1])

        i -= di
        j -= dj

    ordered_kept_word_ids = [word_id for word_id in source_word_ids if word_id in kept_word_ids]
    ordered_kept_word_ids = _prefer_exact_target_window(words, source_tokens, target_tokens, ordered_kept_word_ids)
    ordered_kept_word_ids = _prefer_later_duplicate_runs(words, ordered_kept_word_ids)
    ordered_cut_word_ids = [word_id for word_id in source_word_ids if word_id not in set(ordered_kept_word_ids)]
    anchored_word_ids = _find_anchor_word_ids(words, ordered_kept_word_ids, target_tokens)
    unmatched_target_tokens.reverse()
    return RewriteMatchResult(
        kept_word_ids=ordered_kept_word_ids,
        cut_word_ids=ordered_cut_word_ids,
        anchored_word_ids=anchored_word_ids,
        source_word_count=len(source_word_ids),
        target_token_count=len(target_tokens),
        kept_word_count=len(ordered_kept_word_ids),
        matched_target_count=len(matched_target_indexes),
        unmatched_target_tokens=unmatched_target_tokens,
    )


def _word_has_terminal_punctuation(word: dict[str, Any]) -> bool:
    text = str(word.get("word", "")).rstrip()
    return text.endswith(TERMINAL_PUNCTUATION)


def _gap_after(words: list[dict[str, Any]], index: int) -> float:
    if index < 0 or index >= len(words) - 1:
        return float("inf")
    current_end = float(words[index]["end"])
    next_start = float(words[index + 1]["start"])
    return max(0.0, next_start - current_end)


def _gap_matches_audio_silence(
    words: list[dict[str, Any]],
    left_index: int,
    right_index: int,
    *,
    max_silence_gap: float,
    audio_silences: list[dict[str, float]] | None,
) -> bool:
    gap_start = float(words[left_index]["end"])
    gap_end = float(words[right_index]["start"])
    gap_duration = max(0.0, gap_end - gap_start)
    if gap_duration <= max_silence_gap:
        return False
    if audio_silences is None:
        return True
    if not audio_silences:
        return False

    minimum_overlap = max(0.12, min(gap_duration, max_silence_gap) * 0.6)
    for silence in audio_silences:
        silence_start = float(silence["start"])
        silence_end = float(silence["end"])
        overlap = max(0.0, min(gap_end, silence_end) - max(gap_start, silence_start))
        if overlap >= minimum_overlap:
            return True
    return False


def _boundary_word_token(word: dict[str, Any]) -> str:
    return normalize_rewrite_token(str(word.get("word", "")))


def _is_weak_cut(words: list[dict[str, Any]], left_index: int, right_index: int) -> bool:
    return _boundary_score(words, left_index, right_index) <= 0


def _boundary_score(words: list[dict[str, Any]], left_index: int, right_index: int) -> int:
    left_word = str(words[left_index].get("word", "")).rstrip()
    right_word = str(words[right_index].get("word", "")).rstrip()
    left_token = _boundary_word_token(words[left_index])
    right_token = _boundary_word_token(words[right_index])

    score = 0
    if left_word.endswith(STRONG_END_PUNCTUATION):
        score += 4
    elif left_word.endswith(SOFT_END_PUNCTUATION):
        score += 2

    if left_token in WEAK_BOUNDARY_END_WORDS:
        score -= 4
    if right_token in WEAK_BOUNDARY_START_WORDS:
        score -= 4

    if left_token and right_token and left_token not in WEAK_BOUNDARY_END_WORDS and right_token not in WEAK_BOUNDARY_START_WORDS:
        score += 1

    gap_duration = max(0.0, float(words[right_index]["start"]) - float(words[left_index]["end"]))
    if gap_duration >= 0.5:
        score += 1

    return score


def _build_kept_runs(words: list[dict[str, Any]], kept_word_ids: set[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    current_start: int | None = None
    for index, word in enumerate(words):
        if int(word["id"]) in kept_word_ids:
            if current_start is None:
                current_start = index
            continue
        if current_start is not None:
            runs.append((current_start, index - 1))
            current_start = None
    if current_start is not None:
        runs.append((current_start, len(words) - 1))
    return runs


def _run_tokens(words: list[dict[str, Any]], start_index: int, end_index: int) -> list[str]:
    return [_boundary_word_token(word) for word in words[start_index : end_index + 1]]


def _duplicate_take_score(words: list[dict[str, Any]], start_index: int, end_index: int) -> tuple[int, int, int]:
    before_score = _boundary_score(words, start_index - 1, start_index) if start_index > 0 else 0
    after_score = _boundary_score(words, end_index, end_index + 1) if end_index < len(words) - 1 else 0
    return (before_score + after_score, before_score, start_index)


def _prefer_exact_target_window(
    words: list[dict[str, Any]],
    source_tokens: list[str],
    target_tokens: list[str],
    kept_word_ids: list[int],
) -> list[int]:
    exact_windows = _find_exact_target_windows(source_tokens, target_tokens)
    if not exact_windows:
        return kept_word_ids
    best_start, best_end = max(
        exact_windows,
        key=lambda window: (_duplicate_take_score(words, window[0], window[1])[0], window[0]),
    )
    return [int(word["id"]) for word in words[best_start : best_end + 1]]


def _prefer_later_duplicate_runs(words: list[dict[str, Any]], kept_word_ids: list[int]) -> list[int]:
    keep_set = set(kept_word_ids)
    changed = True
    while changed:
        changed = False
        runs = _build_kept_runs(words, keep_set)
        for run_start, run_end in runs:
            run_length = run_end - run_start + 1
            if run_length < MIN_DUPLICATE_SHIFT_WORDS:
                continue
            run_tokens = _run_tokens(words, run_start, run_end)
            search_limit = min(len(words) - run_length + 1, run_end + MAX_DUPLICATE_SHIFT_WORDS + 2)
            best_candidate: tuple[int, int] | None = None
            best_score = _duplicate_take_score(words, run_start, run_end)
            for candidate_start in range(run_end + 1, search_limit):
                candidate_end = candidate_start + run_length - 1
                if any(int(word["id"]) in keep_set for word in words[candidate_start : candidate_end + 1]):
                    continue
                if _run_tokens(words, candidate_start, candidate_end) != run_tokens:
                    continue
                candidate_score = _duplicate_take_score(words, candidate_start, candidate_end)
                if candidate_score[:2] < best_score[:2]:
                    continue
                if candidate_score[:2] == best_score[:2] and candidate_start <= best_score[2]:
                    continue
                best_candidate = (candidate_start, candidate_end)
                best_score = candidate_score
            if best_candidate is None:
                continue

            keep_set.difference_update(int(word["id"]) for word in words[run_start : run_end + 1])
            keep_set.update(int(word["id"]) for word in words[best_candidate[0] : best_candidate[1] + 1])
            changed = True
            break

    return [int(word["id"]) for word in words if int(word["id"]) in keep_set]


def _should_bridge_cut_gap(
    words: list[dict[str, Any]],
    left_index: int,
    right_index: int,
    *,
    pause_gap: float,
) -> bool:
    gap_word_count = right_index - left_index - 1
    if gap_word_count <= 0 or gap_word_count > MAX_CLAUSE_BRIDGE_WORDS:
        return False
    if not _is_weak_cut(words, left_index, right_index):
        return False
    for index in range(left_index, right_index):
        if _gap_after(words, index) >= pause_gap:
            return False
    for index in range(left_index + 1, right_index):
        if _word_has_terminal_punctuation(words[index]):
            return False
    return True


def _should_split_at_gap(
    words: list[dict[str, Any]],
    left_index: int,
    right_index: int,
    *,
    max_silence_gap: float,
    audio_silences: list[dict[str, float]] | None,
) -> bool:
    if not _gap_matches_audio_silence(
        words,
        left_index,
        right_index,
        max_silence_gap=max_silence_gap,
        audio_silences=audio_silences,
    ):
        return False
    return _boundary_score(words, left_index, right_index) > 0


def _snap_start_index(words: list[dict[str, Any]], start_index: int, *, lookaround_words: int, pause_gap: float) -> int:
    if start_index <= 0 or _gap_after(words, start_index - 1) >= pause_gap:
        return start_index

    lower_bound = max(0, start_index - lookaround_words - 1)
    for index in range(start_index - 1, lower_bound - 1, -1):
        if _word_has_terminal_punctuation(words[index]) or _gap_after(words, index) >= pause_gap:
            return index + 1
    return start_index


def _snap_end_index(words: list[dict[str, Any]], end_index: int, *, lookaround_words: int, pause_gap: float) -> int:
    if end_index >= len(words) - 1 or _gap_after(words, end_index) >= pause_gap or _word_has_terminal_punctuation(words[end_index]):
        return end_index

    upper_bound = min(len(words) - 1, end_index + lookaround_words)
    for index in range(end_index + 1, upper_bound + 1):
        if _word_has_terminal_punctuation(words[index]) or _gap_after(words, index) >= pause_gap:
            return index
    return end_index


def build_ranges_from_kept_word_ids(
    words: list[dict[str, Any]],
    kept_word_ids: list[int],
    *,
    anchored_word_ids: list[int] | None = None,
    min_duration: float = 0.1,
    min_anchor_words: int = MIN_ANCHOR_WORDS,
    phrase_lookaround_words: int = PHRASE_LOOKAROUND_WORDS,
    pause_gap: float = PAUSE_GAP_THRESHOLD,
    max_silence_gap: float | None = None,
    audio_silences: list[dict[str, float]] | None = None,
) -> list[dict[str, float]]:
    kept_word_id_set = set(kept_word_ids)
    anchored_word_id_set = set(anchored_word_ids or [])
    apply_anchor_pruning = bool(anchored_word_id_set)
    raw_runs: list[tuple[int, int]] = []
    current_start: int | None = None
    previous_kept_index: int | None = None

    for index, word in enumerate(words):
        if int(word["id"]) in kept_word_id_set:
            if (
                current_start is not None
                and previous_kept_index is not None
                and max_silence_gap is not None
                and _should_split_at_gap(
                    words,
                    previous_kept_index,
                    index,
                    max_silence_gap=max_silence_gap,
                    audio_silences=audio_silences,
                )
            ):
                raw_runs.append((current_start, previous_kept_index))
                current_start = index
            if current_start is None:
                current_start = index
            previous_kept_index = index
            continue
        if current_start is not None:
            raw_runs.append((current_start, index - 1))
            current_start = None
        previous_kept_index = None

    if current_start is not None:
        raw_runs.append((current_start, previous_kept_index if previous_kept_index is not None else len(words) - 1))

    if apply_anchor_pruning and len(raw_runs) > 1:
        merged_short_runs: list[tuple[int, int]] = []
        for start_index, end_index in raw_runs:
            if (
                merged_short_runs
                and start_index == merged_short_runs[-1][1] + 1
                and (
                    (merged_short_runs[-1][1] - merged_short_runs[-1][0] + 1) < min_anchor_words
                    or (end_index - start_index + 1) < min_anchor_words
                )
            ):
                previous_start, _previous_end = merged_short_runs[-1]
                merged_short_runs[-1] = (previous_start, end_index)
            else:
                merged_short_runs.append((start_index, end_index))
        raw_runs = merged_short_runs

    adjusted_runs: list[tuple[int, int]] = []
    for start_index, end_index in raw_runs:
        word_count = end_index - start_index + 1
        run_word_ids = {int(item["id"]) for item in words[start_index : end_index + 1]}
        run_is_anchored = bool(run_word_ids & anchored_word_id_set)
        if apply_anchor_pruning and word_count < min_anchor_words and not run_is_anchored:
            continue

        if apply_anchor_pruning:
            snapped_start = _snap_start_index(words, start_index, lookaround_words=phrase_lookaround_words, pause_gap=pause_gap)
            snapped_end = _snap_end_index(words, end_index, lookaround_words=phrase_lookaround_words, pause_gap=pause_gap)
        else:
            snapped_start = start_index
            snapped_end = end_index
        adjusted_runs.append((snapped_start, snapped_end))

    merged_runs: list[tuple[int, int]] = []
    for start_index, end_index in adjusted_runs:
        bridge_cut_gap = (
            bool(merged_runs)
            and apply_anchor_pruning
            and _should_bridge_cut_gap(words, merged_runs[-1][1], start_index, pause_gap=pause_gap)
        )
        split_on_silence = (
            bool(merged_runs)
            and max_silence_gap is not None
            and _should_split_at_gap(
                words,
                merged_runs[-1][1],
                start_index,
                max_silence_gap=max_silence_gap,
                audio_silences=audio_silences,
            )
        )
        if not merged_runs or (start_index > merged_runs[-1][1] + 1 and not bridge_cut_gap) or split_on_silence:
            merged_runs.append((start_index, end_index))
            continue
        previous_start, previous_end = merged_runs[-1]
        merged_runs[-1] = (previous_start, max(previous_end, end_index))

    ranges: list[dict[str, float]] = []
    for start_index, end_index in merged_runs:
        start = float(words[start_index]["start"])
        end = float(words[end_index]["end"])
        if end - start >= min_duration:
            ranges.append({"start": start, "end": end})

    if not ranges:
        return []

    normalized_ranges: list[dict[str, float]] = []
    for current in sorted(ranges, key=lambda item: (float(item["start"]), float(item["end"]))):
        if not normalized_ranges or float(current["start"]) > float(normalized_ranges[-1]["end"]):
            normalized_ranges.append({"start": float(current["start"]), "end": float(current["end"])})
            continue
        normalized_ranges[-1]["end"] = max(float(normalized_ranges[-1]["end"]), float(current["end"]))

    return normalized_ranges
