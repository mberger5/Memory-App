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
# Part 5 pilot enables curated plot, character, and theme follow-ups only for
# books that have independent entries on the Quiz Facts sheet.
CURATED_DEEP_QUESTIONS_ENABLED = True
FACT_COLUMNS = {
    "opening_hard": ["Opening Clue Hard", "Opening Clue"],
    "opening_medium": ["Opening Clue Medium", "Opening Clue"],
    "opening_easy": ["Opening Clue Easy", "Opening Clue"],
    "hint_1": ["Hint 1"],
    "hint_2": ["Hint 2"],
    "plot_question": ["Plot Question"],
    "plot_answer": ["Plot Answer", "Central Conflict"],
    "character_question": ["Character Question"],
    "character_answer": ["Character Answer", "Major Characters"],
    "theme_question": ["Theme Question"],
    "theme_answer": ["Theme Answer", "Major Themes"],
    "fun_fact_1": ["Fun Fact 1"],
    "fun_fact_2": ["Fun Fact 2"],
    "source_1": ["Source 1"],
    "source_2": ["Source 2"],
}


@dataclass
class FollowUpQuestion:
    question_id: str
    skill: str
    subtype: str
    prompt: str
    answer: str
    acceptable_answers: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)
    grading: str = "auto"  # auto | choice | self
    explanation: str = ""
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


def redact_answer_terms(text: str, terms: Iterable[str]) -> str:
    redacted = clean_text(text)
    for term in sorted(unique_nonempty(terms), key=len, reverse=True):
        if len(normalize_answer(term)) < 4:
            continue
        redacted = re.sub(re.escape(term), "[answer hidden]", redacted, flags=re.IGNORECASE)
    return redacted


def fact_value(facts: dict, key: str) -> str:
    for column in FACT_COLUMNS.get(key, []):
        value = clean_text(facts.get(column))
        if value:
            return value
    return ""


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
            hint_1_bits.append(f"the author is associated with {country}")
        hint_1 = "; ".join(hint_1_bits) + "." if hint_1_bits else "Think about the central premise in the clue."

        hint_2_bits = [f"It was written by {author}"]
        if series:
            hint_2_bits.append(f"and belongs to the {series} series")
        else:
            initials = title_initials(title)
            if initials:
                hint_2_bits.append(f"and its title pattern is {initials}")
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
    # A generic question based on Summary (AI) simply repeats the opening clue,
    # so plot follow-ups are included only when Part 5 supplies a distinct,
    # curated question and answer.
    custom_question = fact_value(facts, "plot_question")
    custom_answer = fact_value(facts, "plot_answer")
    if not custom_question or not custom_answer:
        return None
    return FollowUpQuestion(
        question_id=str(uuid.uuid4()),
        skill="Plot",
        subtype="curated_plot",
        prompt=custom_question,
        answer=custom_answer,
        acceptable_answers=[],
        grading="self",
        explanation="Compare your answer with the prepared plot answer, then grade your own recall.",
    )


def meaningful_tokens(value: object) -> set[str]:
    stopwords = {
        "about", "after", "again", "against", "also", "another", "because",
        "been", "before", "book", "central", "does", "from", "have", "into",
        "major", "more", "most", "novel", "question", "story", "that", "their",
        "theme", "themes", "there", "these", "they", "this", "through", "what",
        "when", "where", "which", "while", "with", "without", "would",
    }
    return {
        token
        for token in normalize_answer(value).split()
        if len(token) >= 4 and token not in stopwords
    }


def overlaps_opening(opening_clue: str, question: FollowUpQuestion) -> bool:
    opening = meaningful_tokens(opening_clue)
    if not opening:
        return False
    answer_tokens = meaningful_tokens(question.answer)
    prompt_tokens = meaningful_tokens(question.prompt)
    # Skip a follow-up when most of its substantive answer was already supplied
    # in the opening clue, or when the question itself substantially repeats it.
    answer_overlap = len(opening & answer_tokens) / max(1, len(answer_tokens))
    prompt_overlap = len(opening & prompt_tokens) / max(1, len(prompt_tokens))
    return answer_overlap >= 0.65 or prompt_overlap >= 0.75


def build_curated_deep_questions(facts: dict) -> list[FollowUpQuestion]:
    output: list[FollowUpQuestion] = []
    character_question = fact_value(facts, "character_question")
    character_answer = fact_value(facts, "character_answer")
    if character_question and character_answer:
        output.append(
            FollowUpQuestion(
                question_id=str(uuid.uuid4()),
                skill="Characters",
                subtype="curated_character",
                prompt=character_question,
                answer=character_answer,
                acceptable_answers=[character_answer],
                grading="self",
                explanation="Compare your response with the prepared character answer.",
            )
        )
    theme_question = fact_value(facts, "theme_question")
    theme_answer = fact_value(facts, "theme_answer")
    if theme_question and theme_answer:
        output.append(
            FollowUpQuestion(
                question_id=str(uuid.uuid4()),
                skill="Themes",
                subtype="curated_theme",
                prompt=theme_question,
                answer=theme_answer,
                grading="self",
                explanation="Compare your response with the prepared theme answer.",
            )
        )
    return output


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
    if country:
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

    title = clean_text(book.get(TITLE_COL))
    author = clean_text(book.get(AUTHOR_COL))
    target = "title"
    clue = select_opening_clue(book, facts, difficulty)
    # The author may be part of a fair title clue; only the title itself must be
    # hidden before identification.
    clue = redact_answer_terms(clue, title_aliases(title))

    prompt = f"**Which book is this?**\n\n> {clue}"
    answer = title
    aliases = title_aliases(title)

    hints = build_hints(book, facts, target, books)
    options = build_options(current, books, target)

    # Title is always identified first; author is always the first follow-up.
    followups: list[FollowUpQuestion] = [build_counterpart_question(book, target, books)]

    # Plot, character, and theme questions are intentionally paused in Part 4.2.
    # The opening clue often uses the only plot summary currently available, so
    # reusing that summary would test short-term memory rather than book recall.
    # Part 5 will enable these only after adding independent curated facts.
    if CURATED_DEEP_QUESTIONS_ENABLED:
        deep_questions: list[FollowUpQuestion] = []
        plot_question = build_plot_question(book, facts)
        if plot_question is not None:
            deep_questions.append(plot_question)
        deep_questions.extend(build_curated_deep_questions(facts))
        followups.extend(q for q in deep_questions if not overlaps_opening(clue, q))

    personal = build_personal_question(book, events)
    if personal:
        followups.append(personal)
    knowledge = build_knowledge_question(book, key, books)
    if knowledge:
        followups.append(knowledge)

    # Keep rounds brisk now; curated Part 5 data can add one character and one theme
    # prompt while preserving the plot/personal/bibliographic mix.
    if len(followups) > 5:
        fixed = followups[:2]
        extras = followups[2:]
        random.shuffle(extras)
        followups = fixed + extras[:3]

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
