from __future__ import annotations

import random
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable

import pandas as pd


TITLE_COL = "Name"
AUTHOR_COL = "Author"
SUMMARY_COL = "Summary (AI)"
RATING_COL = "Rating (Normalized)"
YEAR_READ_COL = "Year Read"
DATE_READ_COL = "Date Read"
YEAR_PUBLISHED_COL = "Year Published"
GENRE_COL = "Genre (Simple)"
DETAILED_GENRE_COL = "Genre (Detailed)"
TYPE_COL = "Type"
SERIES_COL = "Series"
COUNTRY_COL = "Country of Origin"
QUICK_NOTE_COL = "Quick Summary/Notes (written at the time)"
LATER_NOTE_COL = "Notes (written much later)"
OWNERSHIP_COL = "Own a copy?"
AWARDS_COL = "Awards"

FACT_SHEET = "Quiz Facts"
# A book is eligible only when the Quiz Facts sheet contains a complete,
# independent set of title clues plus plot, character, and theme material.
FACT_COLUMNS = {
    "opening_hard": ["Opening Clue Hard", "Opening Clue"],
    "opening_medium": ["Opening Clue Medium", "Opening Clue"],
    "opening_easy": ["Opening Clue Easy", "Opening Clue"],
    "hint_1": ["Hint 1"],
    "hint_2": ["Hint 2"],
    "plot_question": ["Plot Question"],
    "plot_answer": ["Plot Answer", "Central Conflict"],
    "plot_choice_answer": ["Plot Choice Answer"],
    "plot_distractor_1": ["Plot Distractor 1"],
    "plot_distractor_2": ["Plot Distractor 2"],
    "plot_distractor_3": ["Plot Distractor 3"],
    "plot_refresher": ["Plot Refresher (No Ending)"],
    "ending_question": ["Ending Question"],
    "ending_hint": ["Ending Hint"],
    "ending_answer": ["Ending Answer"],
    "ending_choice_answer": ["Ending Choice Answer"],
    "ending_distractor_1": ["Ending Distractor 1"],
    "ending_distractor_2": ["Ending Distractor 2"],
    "ending_distractor_3": ["Ending Distractor 3"],
    "theme_question": ["Theme Question"],
    "theme_answer": ["Theme Answer", "Major Themes"],
    "tone_question": ["Tone/Style Question"],
    "tone_answer": ["Tone/Style Answer"],
    "tone_distractor_1": ["Tone/Style Distractor 1"],
    "tone_distractor_2": ["Tone/Style Distractor 2"],
    "tone_distractor_3": ["Tone/Style Distractor 3"],
    "character_question": ["Character Question"],
    "character_answer": ["Character Answer", "Major Characters"],
    "fun_fact_1": ["Fun Fact 1"],
    "fun_fact_2": ["Fun Fact 2"],
    "source_1": ["Source 1"],
    "source_2": ["Source 2"],
    "source_3": ["Source 3"],
}

REQUIRED_ENRICHMENT_FIELDS = (
    "opening_hard",
    "opening_medium",
    "opening_easy",
    "hint_1",
    "hint_2",
    "plot_question",
    "plot_answer",
    "plot_choice_answer",
    "plot_distractor_1",
    "plot_distractor_2",
    "plot_distractor_3",
    "plot_refresher",
    "ending_question",
    "ending_hint",
    "ending_answer",
    "ending_choice_answer",
    "ending_distractor_1",
    "ending_distractor_2",
    "ending_distractor_3",
    "theme_question",
    "theme_answer",
    "tone_question",
    "tone_answer",
    "tone_distractor_1",
    "tone_distractor_2",
    "tone_distractor_3",
)


@dataclass
class FollowUpQuestion:
    question_id: str
    skill: str
    subtype: str
    prompt: str
    answer: str
    acceptable_answers: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    grading: str = "auto"  # auto | choice | self | self_help
    explanation: str = ""
    refresher: str = ""
    hint: str = ""
    spoiler: bool = False
    max_points: float = 1.0


@dataclass
class QuizRound:
    round_id: str
    book_key: str
    title: str
    author: str
    target: str  # title | author
    opening_prompt: str
    opening_answer: str
    opening_aliases: list[str]
    hints: list[str]
    options: list[str]
    followups: list[FollowUpQuestion]
    book: dict
    reading_events: list[dict]
    facts: dict


# -------------------------------
# Cleaning and matching
# -------------------------------

def clean_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat"}:
        return ""
    return re.sub(r"\s+", " ", text)


def normalize_answer(value: object) -> str:
    text = clean_text(value)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    text = re.sub(r"^(the|a|an)\s+", "", text)
    return re.sub(r"\s+", " ", text)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [clean_text(c) for c in out.columns]
    for col in out.columns:
        if out[col].dtype == object:
            out[col] = out[col].map(clean_text)
    return out


def to_float(value: object) -> float | None:
    text = clean_text(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: object) -> int | None:
    number = to_float(value)
    return int(number) if number is not None else None


def answer_matches(user_answer: object, accepted_answers: Iterable[object]) -> bool:
    user = normalize_answer(user_answer)
    if not user:
        return False
    for accepted_value in accepted_answers:
        accepted = normalize_answer(accepted_value)
        if not accepted:
            continue
        if user == accepted:
            return True
        # Permit small spelling errors on reasonably long answers.
        if min(len(user), len(accepted)) >= 7 and SequenceMatcher(None, user, accepted).ratio() >= 0.88:
            return True
    return False


def title_aliases(title: str) -> list[str]:
    aliases = [title]
    if ":" in title:
        aliases.append(title.split(":", 1)[0].strip())
    if "(" in title:
        aliases.append(title.split("(", 1)[0].strip())
    return unique_nonempty(aliases)


def author_aliases(author: str) -> list[str]:
    aliases = [author]
    parts = [part.strip() for part in re.split(r"\s+(?:&|and)\s+", author, flags=re.IGNORECASE) if part.strip()]
    if len(parts) > 1:
        surnames = []
        for part in parts:
            words = [word for word in part.split() if word]
            if words:
                surname = words[-1]
                if len(words) >= 3 and words[-2].lower() in {"de", "del", "la", "le", "van", "von", "da", "du"}:
                    surname = f"{words[-2]} {words[-1]}"
                surnames.append(surname)
        aliases.extend([" and ".join(parts), " & ".join(parts)])
        if len(surnames) == len(parts):
            aliases.extend([" and ".join(surnames), " & ".join(surnames)])
        return unique_nonempty(aliases)

    words = [w for w in re.split(r"\s+", author.strip()) if w]
    if len(words) >= 2:
        surname = words[-1]
        # Preserve compound surnames such as Le Guin and van Vogt.
        if len(words) >= 3 and words[-2].lower() in {"de", "del", "la", "le", "van", "von", "da", "du"}:
            surname = f"{words[-2]} {words[-1]}"
        aliases.append(surname)
    return unique_nonempty(aliases)




def author_redaction_terms(author: str) -> list[str]:
    terms = author_aliases(author)
    parts = [part.strip() for part in re.split(r"\s+(?:&|and)\s+", author, flags=re.IGNORECASE) if part.strip()]
    if len(parts) > 1:
        surnames = []
        for part in parts:
            terms.append(part)
            words = [word for word in part.split() if word]
            if words:
                surnames.append(words[-1])
                terms.append(words[-1])
        if len(surnames) > 1:
            terms.extend([" and ".join(surnames), " & ".join(surnames)])
    return unique_nonempty(terms)

def unique_nonempty(values: Iterable[object]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = clean_text(value)
        key = normalize_answer(text)
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def book_key(title: str, author: str) -> str:
    return f"{normalize_answer(title)}::{normalize_answer(author)}"


# -------------------------------
# Workbook preparation
# -------------------------------

def is_dnf_row(row: pd.Series | dict) -> bool:
    # DNF is recorded inconsistently across the workbook (for example in the
    # original-rating column, Date Read, or free-text notes), so inspect the
    # whole row rather than relying on one designated field.
    values = row.values if isinstance(row, pd.Series) else row.values()
    text = " | ".join(clean_text(value) for value in values)
    return bool(re.search(r"\bdnf\b|did not finish|didn.t finish", text, flags=re.IGNORECASE))


def prepare_book_rows(df: pd.DataFrame) -> pd.DataFrame:
    df = normalize_columns(df)
    if TITLE_COL not in df.columns or AUTHOR_COL not in df.columns:
        raise ValueError("The Book List sheet must contain Name and Author columns.")
    filtered = df[df[TITLE_COL].map(bool) & df[AUTHOR_COL].map(bool)].copy()
    filtered = filtered[~filtered.apply(is_dnf_row, axis=1)]
    if TYPE_COL in filtered.columns:
        filtered = filtered[filtered[TYPE_COL].str.lower().ne("short story")]
    return filtered.reset_index(drop=True)


def prepare_facts(df: pd.DataFrame | None) -> dict[str, dict]:
    if df is None or df.empty:
        return {}
    facts_df = normalize_columns(df)
    if TITLE_COL not in facts_df.columns or AUTHOR_COL not in facts_df.columns:
        return {}
    output: dict[str, dict] = {}
    for row in facts_df.to_dict("records"):
        title = clean_text(row.get(TITLE_COL))
        author = clean_text(row.get(AUTHOR_COL))
        if title and author:
            output[book_key(title, author)] = row
    return output


def group_books(df: pd.DataFrame) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for row in df.to_dict("records"):
        key = book_key(clean_text(row[TITLE_COL]), clean_text(row[AUTHOR_COL]))
        groups.setdefault(key, []).append(row)

    books: list[dict] = []
    for key, events in groups.items():
        # Prefer the most recently listed event as the representative record, while
        # filling any missing metadata from earlier rows.
        representative = dict(events[-1])
        for col in df.columns:
            if not clean_text(representative.get(col)):
                for event in reversed(events[:-1]):
                    if clean_text(event.get(col)):
                        representative[col] = event.get(col)
                        break
        books.append({"book_key": key, "book": representative, "events": events})
    return books


def filter_rows(
    df: pd.DataFrame,
    *,
    genres: list[str] | None = None,
    authors: list[str] | None = None,
    years_read: list[str] | None = None,
    min_rating: float | None = None,
) -> pd.DataFrame:
    filtered = df.copy()
    if genres and GENRE_COL in filtered.columns:
        filtered = filtered[filtered[GENRE_COL].isin(genres)]
    if authors:
        filtered = filtered[filtered[AUTHOR_COL].isin(authors)]
    if years_read and YEAR_READ_COL in filtered.columns:
        filtered = filtered[filtered[YEAR_READ_COL].isin(years_read)]
    if min_rating is not None and RATING_COL in filtered.columns:
        ratings = filtered[RATING_COL].map(to_float)
        filtered = filtered[ratings.fillna(-1) >= min_rating]
    return filtered.reset_index(drop=True)


# -------------------------------
# Round generation
# -------------------------------

def decade_label(year: int | None) -> str:
    if year is None:
        return ""
    return f"{(year // 10) * 10}s"


def redact_answer_terms(text: str, terms: Iterable[str], replacement: str = "[answer hidden]") -> str:
    redacted = clean_text(text)
    for term in sorted(unique_nonempty(terms), key=len, reverse=True):
        if len(normalize_answer(term)) < 4:
            continue
        pattern = rf"(?<!\w){re.escape(term)}(?!\w)"
        redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)
    return redacted


def fact_value(facts: dict, key: str) -> str:
    for column in FACT_COLUMNS.get(key, []):
        value = clean_text(facts.get(column))
        if value:
            return value
    return ""


def fact_options(facts: dict, answer_key: str, distractor_keys: list[str]) -> tuple[str, list[str]]:
    answer = fact_value(facts, answer_key)
    options = unique_nonempty([answer] + [fact_value(facts, key) for key in distractor_keys])
    random.shuffle(options)
    return answer, options


def has_complete_enrichment(facts: dict) -> bool:
    return bool(facts) and all(fact_value(facts, key) for key in REQUIRED_ENRICHMENT_FIELDS)


def choose_target(target_mix: str) -> str:
    # Every round begins by identifying the book title. Author recall is always
    # tested only after the title has been identified or revealed. The argument
    # remains for compatibility with older saved app state and history.
    return "title"


def select_opening_clue(book: dict, facts: dict, difficulty: str) -> str:
    if difficulty == "Challenging":
        curated_keys = ["opening_hard", "opening_medium", "opening_easy"]
    elif difficulty == "Easier":
        curated_keys = ["opening_easy", "opening_medium", "opening_hard"]
    else:
        curated_keys = random.choice(
            [["opening_hard", "opening_medium", "opening_easy"], ["opening_medium", "opening_easy", "opening_hard"]]
        )
    for key in curated_keys:
        value = fact_value(facts, key)
        if value:
            return value

    summary = clean_text(book.get(SUMMARY_COL))
    quick_note = clean_text(book.get(QUICK_NOTE_COL))
    later_note = clean_text(book.get(LATER_NOTE_COL))
    note_candidates = [n for n in [quick_note, later_note] if 45 <= len(n) <= 500]

    # Challenging rounds occasionally start from the user's own distinctive note.
    if difficulty == "Challenging" and note_candidates and random.random() < 0.28:
        return "From your own notes: " + random.choice(note_candidates)
    if summary:
        return summary
    if note_candidates:
        return "From your own notes: " + random.choice(note_candidates)

    genre = clean_text(book.get(DETAILED_GENRE_COL) or book.get(GENRE_COL))
    year = to_int(book.get(YEAR_PUBLISHED_COL))
    fallback = f"This is a {genre or 'book'}"
    if year:
        fallback += f" first published in {year}"
    return fallback + "."


def title_initials(title: str) -> str:
    words = [w for w in re.findall(r"[A-Za-z0-9]+", title) if w.lower() not in {"the", "a", "an", "of"}]
    return " ".join(f"{w[0].upper()}…" for w in words)


def build_hints(book: dict, facts: dict, target: str, all_books: list[dict]) -> list[str]:
    custom_1 = fact_value(facts, "hint_1")
    custom_2 = fact_value(facts, "hint_2")
    if custom_1 or custom_2:
        return [h for h in [custom_1, custom_2] if h]

    title = clean_text(book.get(TITLE_COL))
    author = clean_text(book.get(AUTHOR_COL))
    genre = clean_text(book.get(DETAILED_GENRE_COL) or book.get(GENRE_COL))
    year = to_int(book.get(YEAR_PUBLISHED_COL))
    decade = decade_label(year)
    country = clean_text(book.get(COUNTRY_COL))
    series = clean_text(book.get(SERIES_COL))

    if target == "title":
        hint_1_bits = []
        if genre:
            hint_1_bits.append(f"It is categorized as {genre}")
        if decade:
            hint_1_bits.append(f"it was first published in the {decade}")
        if country:
            hint_1_bits.append(f"its author is associated with {country}")
        hint_1 = "; ".join(hint_1_bits) + "." if hint_1_bits else "Think about the central premise in the clue."

        hint_2_bits = []
        if series:
            hint_2_bits.append(f"It belongs to the {series} series")
        initials = title_initials(title)
        if initials:
            hint_2_bits.append(f"its title pattern is {initials}")
        if not hint_2_bits:
            words = [word for word in re.findall(r"[A-Za-z0-9]+", title)]
            hint_2_bits.append(f"The title contains {len(words)} word{'s' if len(words) != 1 else ''}")
        return [" ".join(hint_1.split()), "; ".join(hint_2_bits) + "."]

    # Author target
    same_author_titles = [
        clean_text(item["book"].get(TITLE_COL))
        for item in all_books
        if normalize_answer(item["book"].get(AUTHOR_COL)) == normalize_answer(author)
        and normalize_answer(item["book"].get(TITLE_COL)) != normalize_answer(title)
    ]
    hint_1_bits = []
    if country:
        hint_1_bits.append(f"The author is associated with {country}")
    if decade:
        hint_1_bits.append(f"this book appeared in the {decade}")
    if genre:
        hint_1_bits.append(f"and it is {genre}")
    hint_1 = "; ".join(hint_1_bits) + "." if hint_1_bits else "Think about authors you have read in this genre."

    surname = author_aliases(author)[-1]
    if same_author_titles:
        hint_2 = f"You also logged *{random.choice(same_author_titles)}* by this author. The surname begins with **{surname[0].upper()}**."
    else:
        hint_2 = f"The author's surname begins with **{surname[0].upper()}** and has {len(normalize_answer(surname).replace(' ', ''))} letters."
    return [hint_1, hint_2]


def option_candidates(current: dict, all_books: list[dict], target: str) -> list[str]:
    book = current["book"]
    answer = clean_text(book.get(TITLE_COL if target == "title" else AUTHOR_COL))
    genre = normalize_answer(book.get(GENRE_COL))
    country = normalize_answer(book.get(COUNTRY_COL))
    pub_year = to_int(book.get(YEAR_PUBLISHED_COL))

    scored: list[tuple[int, str]] = []
    for item in all_books:
        other = item["book"]
        candidate = clean_text(other.get(TITLE_COL if target == "title" else AUTHOR_COL))
        if not candidate or normalize_answer(candidate) == normalize_answer(answer):
            continue
        score = 0
        if genre and normalize_answer(other.get(GENRE_COL)) == genre:
            score += 3
        if country and normalize_answer(other.get(COUNTRY_COL)) == country:
            score += 1
        other_year = to_int(other.get(YEAR_PUBLISHED_COL))
        if pub_year and other_year and abs(pub_year - other_year) <= 20:
            score += 1
        scored.append((score, candidate))

    random.shuffle(scored)
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return unique_nonempty(candidate for _, candidate in scored)


def build_options(current: dict, all_books: list[dict], target: str, n: int = 4) -> list[str]:
    book = current["book"]
    answer = clean_text(book.get(TITLE_COL if target == "title" else AUTHOR_COL))
    candidates = option_candidates(current, all_books, target)
    options = candidates[: max(0, n - 1)] + [answer]
    options = unique_nonempty(options)
    random.shuffle(options)
    return options


def make_choice_options(answer: str, pool: Iterable[str], n: int = 4) -> list[str]:
    candidates = [value for value in unique_nonempty(pool) if normalize_answer(value) != normalize_answer(answer)]
    random.shuffle(candidates)
    options = candidates[: n - 1] + [answer]
    options = unique_nonempty(options)
    random.shuffle(options)
    return options


def build_counterpart_question(book: dict, opening_target: str, all_books: list[dict]) -> FollowUpQuestion:
    title = clean_text(book.get(TITLE_COL))
    author = clean_text(book.get(AUTHOR_COL))
    if opening_target == "title":
        return FollowUpQuestion(
            question_id=str(uuid.uuid4()),
            skill="Author identification",
            subtype="book_to_author",
            prompt=f"And who wrote *{title}*?",
            answer=author,
            acceptable_answers=author_aliases(author),
            options=make_choice_options(author, [item["book"].get(AUTHOR_COL) for item in all_books]),
            grading="auto",
            explanation=f"*{title}* is by **{author}**.",
        )
    return FollowUpQuestion(
        question_id=str(uuid.uuid4()),
        skill="Book identification",
        subtype="author_to_book",
        prompt=f"Name the book by **{author}** that this round has been about.",
        answer=title,
        acceptable_answers=title_aliases(title),
        options=make_choice_options(title, [item["book"].get(TITLE_COL) for item in all_books]),
        grading="auto",
        explanation=f"The book is *{title}*.",
    )


def build_plot_question(book: dict, facts: dict) -> FollowUpQuestion | None:
    question = fact_value(facts, "plot_question")
    detailed_answer = fact_value(facts, "plot_answer")
    choice_answer, options = fact_options(
        facts,
        "plot_choice_answer",
        ["plot_distractor_1", "plot_distractor_2", "plot_distractor_3"],
    )
    refresher = fact_value(facts, "plot_refresher")
    if not all([question, detailed_answer, choice_answer, refresher]) or len(options) < 4:
        return None
    return FollowUpQuestion(
        question_id=str(uuid.uuid4()),
        skill="Plot",
        subtype="high_level_plot",
        prompt=question,
        answer=choice_answer,
        acceptable_answers=[choice_answer],
        options=options,
        grading="choice",
        explanation=f"**High-level answer:** {detailed_answer}",
        refresher=refresher,
    )


def build_ending_question(facts: dict) -> FollowUpQuestion | None:
    question = fact_value(facts, "ending_question")
    hint = fact_value(facts, "ending_hint")
    answer = fact_value(facts, "ending_answer")
    choice_answer, options = fact_options(
        facts,
        "ending_choice_answer",
        ["ending_distractor_1", "ending_distractor_2", "ending_distractor_3"],
    )
    if not all([question, hint, answer, choice_answer]) or len(options) < 4:
        return None
    return FollowUpQuestion(
        question_id=str(uuid.uuid4()),
        skill="Ending recall",
        subtype="ending_after_refresher",
        prompt=question,
        answer=answer,
        acceptable_answers=[choice_answer],
        options=options,
        grading="self_help",
        explanation="Use the refresher as a memory cue; exact wording is not important.",
        hint=hint,
        spoiler=True,
    )


def build_theme_question(facts: dict) -> FollowUpQuestion | None:
    question = fact_value(facts, "theme_question")
    answer = fact_value(facts, "theme_answer")
    if not question or not answer:
        return None
    return FollowUpQuestion(
        question_id=str(uuid.uuid4()),
        skill="Themes",
        subtype="broad_theme",
        prompt=question,
        answer=answer,
        grading="self",
        explanation="Grade whether you remembered the broad idea, not the exact phrasing.",
    )


def build_tone_question(facts: dict) -> FollowUpQuestion | None:
    question = fact_value(facts, "tone_question")
    answer, options = fact_options(
        facts,
        "tone_answer",
        ["tone_distractor_1", "tone_distractor_2", "tone_distractor_3"],
    )
    if not question or not answer or len(options) < 4:
        return None
    return FollowUpQuestion(
        question_id=str(uuid.uuid4()),
        skill="Tone & style",
        subtype="tone_style_choice",
        prompt=question,
        answer=answer,
        acceptable_answers=[answer],
        options=options,
        grading="choice",
        explanation="This is a broad description of the book's dominant tone and narrative style.",
    )

def rating_band(rating: float) -> str:
    if rating >= 3.5:
        return "Very highly — 3.5/4 or higher"
    if rating >= 3.0:
        return "Positively — 3.0 to 3.49/4"
    if rating >= 2.5:
        return "Mixed — 2.5 to 2.99/4"
    return "Low — below 2.5/4"


def build_personal_question(book: dict, events: list[dict]) -> FollowUpQuestion | None:
    candidates: list[FollowUpQuestion] = []
    title = clean_text(book.get(TITLE_COL))

    ratings = [to_float(event.get(RATING_COL)) for event in events]
    ratings = [rating for rating in ratings if rating is not None]
    if ratings:
        latest = ratings[-1]
        answer = rating_band(latest)
        options = [
            "Very highly — 3.5/4 or higher",
            "Positively — 3.0 to 3.49/4",
            "Mixed — 2.5 to 2.99/4",
            "Low — below 2.5/4",
        ]
        candidates.append(
            FollowUpQuestion(
                question_id=str(uuid.uuid4()),
                skill="Personal reading memory",
                subtype="rating_band",
                prompt=f"How highly did you rate *{title}* on your most recent reading?",
                answer=answer,
                options=options,
                grading="choice",
                explanation=f"Your recorded rating was **{latest:g}/4**.",
            )
        )

    years = unique_nonempty(event.get(YEAR_READ_COL) for event in events)
    if years:
        answer = " and ".join(years) if len(years) > 1 else years[0]
        # Use actual workbook year labels as options when possible.
        possible_years = ["Pre-2007", "2007", "2010", "2015", "2020", "2022", "2024", "2025", "2026"]
        if len(years) == 1:
            options = make_choice_options(answer, possible_years)
            grading = "choice"
        else:
            options = []
            grading = "self"
        candidates.append(
            FollowUpQuestion(
                question_id=str(uuid.uuid4()),
                skill="Personal reading memory",
                subtype="year_read",
                prompt=f"When did you read *{title}*?" + (" This was a reread." if len(events) > 1 else ""),
                answer=answer,
                acceptable_answers=years,
                options=options,
                grading=grading,
                explanation=f"Recorded reading year(s): **{answer}**.",
            )
        )

    notes = unique_nonempty(
        [event.get(QUICK_NOTE_COL) for event in events] + [event.get(LATER_NOTE_COL) for event in events]
    )
    if notes:
        candidates.append(
            FollowUpQuestion(
                question_id=str(uuid.uuid4()),
                skill="Personal reading memory",
                subtype="note_recall",
                prompt=f"What do you remember saying or feeling about *{title}*?",
                answer="\n\n".join(notes),
                grading="self",
                explanation="Compare your memory with your recorded notes.",
            )
        )

    return random.choice(candidates) if candidates else None


def build_knowledge_question(book: dict, current_key: str, all_books: list[dict]) -> FollowUpQuestion | None:
    candidates: list[FollowUpQuestion] = []
    title = clean_text(book.get(TITLE_COL))
    author = clean_text(book.get(AUTHOR_COL))
    year = to_int(book.get(YEAR_PUBLISHED_COL))
    if year:
        answer = decade_label(year)
        decade_start = max(0, (year // 10) * 10 - 20)
        decades = [f"{d}s" for d in range(decade_start, (year // 10) * 10 + 31, 10)]
        candidates.append(
            FollowUpQuestion(
                question_id=str(uuid.uuid4()),
                skill="Publication knowledge",
                subtype="publication_decade",
                prompt=f"What decade was *{title}* first published?",
                answer=answer,
                acceptable_answers=[answer, str((year // 10) * 10), str(year)],
                options=make_choice_options(answer, decades),
                grading="auto",
                explanation=f"It was first published in **{year}**.",
            )
        )

    series = clean_text(book.get(SERIES_COL))
    if series and normalize_answer(series) != normalize_answer(title):
        candidates.append(
            FollowUpQuestion(
                question_id=str(uuid.uuid4()),
                skill="Connections",
                subtype="series",
                prompt=f"What series is *{title}* part of?",
                answer=series,
                acceptable_answers=[series],
                grading="auto",
                explanation=f"Your workbook lists it under **{series}**.",
            )
        )

    other_titles = unique_nonempty(
        item["book"].get(TITLE_COL)
        for item in all_books
        if item["book_key"] != current_key
        and normalize_answer(item["book"].get(AUTHOR_COL)) == normalize_answer(author)
    )
    if other_titles:
        candidates.append(
            FollowUpQuestion(
                question_id=str(uuid.uuid4()),
                skill="Connections",
                subtype="another_work_by_author",
                prompt=f"Name another book by **{author}** that appears in your reading list.",
                answer="; ".join(other_titles),
                acceptable_answers=other_titles,
                grading="auto",
                explanation="Other logged title(s): " + ", ".join(f"*{name}*" for name in other_titles) + ".",
            )
        )

    country = clean_text(book.get(COUNTRY_COL))
    # Country-of-origin recall is intentionally rare. It can fill a gap when no
    # other context question exists, or appear very occasionally among richer
    # publication/series/connection choices.
    if country and (not candidates or random.random() < 0.05):
        country_pool = [item["book"].get(COUNTRY_COL) for item in all_books]
        candidates.append(
            FollowUpQuestion(
                question_id=str(uuid.uuid4()),
                skill="Publication knowledge",
                subtype="author_country",
                prompt=f"What country is **{author}** associated with in your workbook?",
                answer=country,
                acceptable_answers=[country],
                options=make_choice_options(country, country_pool),
                grading="choice",
                explanation=f"Your workbook lists **{country}**.",
            )
        )

    return random.choice(candidates) if candidates else None


def build_round(
    books: list[dict],
    facts_by_key: dict[str, dict] | None = None,
    *,
    target_mix: str = "Mostly titles",
    difficulty: str = "Challenging",
    avoid_book_key: str | None = None,
) -> QuizRound:
    if not books:
        raise ValueError("No eligible books are available.")
    candidates = [item for item in books if item["book_key"] != avoid_book_key] or books
    current = random.choice(candidates)
    book = current["book"]
    events = current["events"]
    key = current["book_key"]
    facts = (facts_by_key or {}).get(key, {})
    if not has_complete_enrichment(facts):
        raise ValueError("The selected book is missing required Quiz Facts enrichment.")

    title = clean_text(book.get(TITLE_COL))
    author = clean_text(book.get(AUTHOR_COL))
    target = "title"
    clue = select_opening_clue(book, facts, difficulty)
    # Neither the title nor the author may be revealed before their respective
    # recall questions. This also protects against a workbook summary that names
    # the author inside the title-identification clue.
    clue = redact_answer_terms(clue, title_aliases(title), "[title hidden]")
    clue = redact_answer_terms(clue, author_redaction_terms(author), "the author")

    prompt = f"**Which book is this?**\n\n> {clue}"
    answer = title
    aliases = title_aliases(title)

    hints = build_hints(book, facts, target, books)
    safe_hints: list[str] = []
    for hint in hints:
        hint = redact_answer_terms(hint, title_aliases(title), "[title hidden]")
        hint = redact_answer_terms(hint, author_redaction_terms(author), "the author")
        safe_hints.append(hint)
    hints = safe_hints
    options = build_options(current, books, target)

    # Learning-mode sequence: title, author, easy plot, refresher-powered ending,
    # broad theme, tone/style, and one personal-memory prompt.
    followups: list[FollowUpQuestion] = [build_counterpart_question(book, target, books)]
    required_followups = [
        build_plot_question(book, facts),
        build_ending_question(facts),
        build_theme_question(facts),
        build_tone_question(facts),
    ]
    if any(question is None for question in required_followups):
        raise ValueError("The selected book is missing required learning-mode Quiz Facts fields.")
    followups.extend(question for question in required_followups if question is not None)

    personal = build_personal_question(book, events)
    if personal:
        followups.append(personal)

    return QuizRound(
        round_id=str(uuid.uuid4()),
        book_key=key,
        title=title,
        author=author,
        target=target,
        opening_prompt=prompt,
        opening_answer=answer,
        opening_aliases=aliases,
        hints=hints,
        options=options,
        followups=followups,
        book=book,
        reading_events=events,
        facts=facts,
    )


def opening_skill(round_obj: QuizRound) -> str:
    return "Book identification" if round_obj.target == "title" else "Author identification"


def book_notes_markdown(round_obj: QuizRound) -> str:
    book = round_obj.book
    events = round_obj.reading_events
    lines = [f"### *{round_obj.title}* — {round_obj.author}"]

    details: list[str] = []
    year_pub = clean_text(book.get(YEAR_PUBLISHED_COL))
    genre = clean_text(book.get(DETAILED_GENRE_COL) or book.get(GENRE_COL))
    series = clean_text(book.get(SERIES_COL))
    if year_pub:
        details.append(f"published {year_pub}")
    if genre:
        details.append(genre)
    if series:
        details.append(f"series: {series}")
    if details:
        lines.append(" · ".join(details))

    event_lines: list[str] = []
    for event in events:
        date_read = clean_text(event.get(DATE_READ_COL) or event.get(YEAR_READ_COL))
        rating = clean_text(event.get(RATING_COL))
        event_text = date_read or "Reading recorded"
        if rating:
            event_text += f" — {rating}/4"
        event_lines.append(event_text)
    if event_lines:
        lines.append("**Your reading history:** " + "; ".join(event_lines))

    summary = clean_text(book.get(SUMMARY_COL))
    if summary:
        lines.append("**Workbook summary:** " + summary)

    notes = unique_nonempty(
        [event.get(QUICK_NOTE_COL) for event in events] + [event.get(LATER_NOTE_COL) for event in events]
    )
    if notes:
        lines.append("**Your notes:**\n" + "\n".join(f"- {note}" for note in notes))

    ownership = unique_nonempty(event.get(OWNERSHIP_COL) for event in events)
    if ownership:
        lines.append("**Ownership/format:** " + "; ".join(ownership))
    awards = clean_text(book.get(AWARDS_COL))
    if awards:
        lines.append("**Awards:** " + awards)

    curated_facts = unique_nonempty([fact_value(round_obj.facts, "fun_fact_1"), fact_value(round_obj.facts, "fun_fact_2")])
    if curated_facts:
        lines.append("**Curated facts:**\n" + "\n".join(f"- {fact}" for fact in curated_facts))
    return "\n\n".join(lines)
