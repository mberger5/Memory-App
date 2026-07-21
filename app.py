from __future__ import annotations

import json
import random
import re
import unicodedata
from urllib.error import URLError, HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
import streamlit as st

APP_DIR = Path(__file__).resolve().parent
DEFAULT_WORKBOOK = APP_DIR / "Maks_Booklist_enriched_2026-04-25.xlsx"
HISTORY_FILE = APP_DIR / ".book_quiz_history.csv"

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
USER_AGENT = "MaksBookQuiz/1.0 (personal Streamlit app)"

REQUIRED_COLUMNS = {"Name", "Author"}

QUIZ_MODES = [
    "Title → Author",
    "Author → Title",
    "Summary → Title",
    "Summary → Author",
    "Published earlier/later",
    "Your rating: higher/lower",
]

MOBILE_CSS = """
<style>
    .block-container {
        padding-top: 1.1rem;
        padding-bottom: 4rem;
        max-width: 720px;
    }
    div.stButton > button {
        min-height: 3.1rem;
        white-space: normal;
        text-align: left;
        border-radius: 0.75rem;
        font-size: 1.02rem;
        line-height: 1.25;
    }
    div[data-testid="stMetric"] {
        background: rgba(250, 250, 250, 0.65);
        padding: .45rem .55rem;
        border-radius: .75rem;
        border: 1px solid rgba(49, 51, 63, 0.12);
    }
    section[data-testid="stSidebar"] div.stButton > button {
        min-height: 2.5rem;
        text-align: center;
    }
    .question-card {
        padding: 0.85rem 1rem;
        border: 1px solid rgba(49, 51, 63, 0.14);
        border-radius: 1rem;
        background: rgba(250, 250, 250, 0.7);
        margin-bottom: 0.75rem;
    }
</style>
"""


@dataclass(frozen=True)
class Question:
    prompt: str
    options: list[str]
    answer: str
    explanation: str
    source_title: str
    source_author: str
    mode: str


def clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat"}:
        return ""
    return text


def normalize_answer(value: object) -> str:
    """Normalize typed quiz answers so minor punctuation/case differences do not matter."""
    text = clean_text(value)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    text = re.sub(r"^(the|a|an)\s+", "", text)
    return re.sub(r"\s+", " ", text)


def answers_match(choice: object, answer: object) -> bool:
    return normalize_answer(choice) == normalize_answer(answer)


def to_number(value: object) -> float | None:
    text = clean_text(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [clean_text(c) for c in df.columns]
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].map(clean_text)
    return df


@st.cache_data(show_spinner=False)
def load_workbook(path_or_bytes, sheet_name: str) -> pd.DataFrame:
    df = pd.read_excel(path_or_bytes, sheet_name=sheet_name, dtype=str)
    return normalize_cols(df)


@st.cache_data(show_spinner=False)
def sheet_names(path_or_bytes) -> list[str]:
    return pd.ExcelFile(path_or_bytes).sheet_names


def has_required_columns(df: pd.DataFrame) -> bool:
    return REQUIRED_COLUMNS.issubset(set(df.columns))


def is_likely_dnf_row(row: pd.Series) -> bool:
    haystack_cols = [
        "Type",
        "Quick Summary/Notes (written at the time)",
        "Notes (written much later)",
        "Favorite Stories",
    ]
    text = " | ".join(clean_text(row.get(c, "")) for c in haystack_cols).lower()
    return "dnf" in text or "did not finish" in text


def filter_books(
    df: pd.DataFrame,
    *,
    exclude_dnf: bool,
    genres: list[str],
    types: list[str],
    authors: list[str],
    min_rating: float | None,
    year_read_values: list[str],
) -> pd.DataFrame:
    filtered = df.copy()
    filtered = filtered[filtered["Name"].map(bool) & filtered["Author"].map(bool)]

    if exclude_dnf:
        filtered = filtered[~filtered.apply(is_likely_dnf_row, axis=1)]

    if genres and "Genre (Simple)" in filtered.columns:
        filtered = filtered[filtered["Genre (Simple)"].isin(genres)]

    if types and "Type" in filtered.columns:
        filtered = filtered[filtered["Type"].isin(types)]

    if authors:
        filtered = filtered[filtered["Author"].isin(authors)]

    if year_read_values and "Year Read" in filtered.columns:
        filtered = filtered[filtered["Year Read"].isin(year_read_values)]

    if min_rating is not None and "Rating (Normalized)" in filtered.columns:
        ratings = filtered["Rating (Normalized)"].map(to_number)
        filtered = filtered[ratings.fillna(-1) >= min_rating]

    return filtered.reset_index(drop=True)


def unique_nonempty(values: Iterable[object]) -> list[str]:
    return sorted({clean_text(v) for v in values if clean_text(v)})


def sample_distractors(df: pd.DataFrame, col: str, answer: str, n: int = 3) -> list[str]:
    pool = [x for x in unique_nonempty(df[col]) if x != answer]
    random.shuffle(pool)
    return pool[:n]


def make_question(df: pd.DataFrame, mode: str) -> Question | None:
    if len(df) < 4:
        return None

    rows = df.to_dict("records")

    if mode == "Title → Author":
        row = random.choice(rows)
        answer = clean_text(row["Author"])
        distractors = sample_distractors(df, "Author", answer)
        if len(distractors) < 3:
            return None
        options = distractors + [answer]
        random.shuffle(options)
        title = clean_text(row["Name"])
        return Question(
            prompt=f"Who wrote *{title}*?",
            options=options,
            answer=answer,
            explanation=book_explanation(row),
            source_title=title,
            source_author=answer,
            mode=mode,
        )

    if mode == "Author → Title":
        row = random.choice(rows)
        answer = clean_text(row["Name"])
        distractors = sample_distractors(df, "Name", answer)
        if len(distractors) < 3:
            return None
        options = distractors + [answer]
        random.shuffle(options)
        author = clean_text(row["Author"])
        return Question(
            prompt=f"Which of these did **{author}** write?",
            options=options,
            answer=answer,
            explanation=book_explanation(row),
            source_title=answer,
            source_author=author,
            mode=mode,
        )

    if mode == "Summary → Title":
        eligible = [r for r in rows if clean_text(r.get("Summary (AI)"))]
        if len(eligible) < 4:
            return None
        row = random.choice(eligible)
        answer = clean_text(row["Name"])
        distractors = sample_distractors(df, "Name", answer)
        if len(distractors) < 3:
            return None
        options = distractors + [answer]
        random.shuffle(options)
        return Question(
            prompt=f"Which work is this?\n\n> {clean_text(row.get('Summary (AI)'))}",
            options=options,
            answer=answer,
            explanation=book_explanation(row),
            source_title=answer,
            source_author=clean_text(row["Author"]),
            mode=mode,
        )

    if mode == "Summary → Author":
        eligible = [r for r in rows if clean_text(r.get("Summary (AI)"))]
        if len(eligible) < 4:
            return None
        row = random.choice(eligible)
        answer = clean_text(row["Author"])
        distractors = sample_distractors(df, "Author", answer)
        if len(distractors) < 3:
            return None
        options = distractors + [answer]
        random.shuffle(options)
        return Question(
            prompt=f"Who wrote the work described here?\n\n> {clean_text(row.get('Summary (AI)'))}",
            options=options,
            answer=answer,
            explanation=book_explanation(row),
            source_title=clean_text(row["Name"]),
            source_author=answer,
            mode=mode,
        )

    if mode == "Published earlier/later":
        eligible = [r for r in rows if to_number(r.get("Year Published")) is not None]
        if len(eligible) < 4:
            return None
        a, b = random.sample(eligible, 2)
        while to_number(a.get("Year Published")) == to_number(b.get("Year Published")) and len(eligible) > 2:
            a, b = random.sample(eligible, 2)
        ask_earlier = random.choice([True, False])
        ya = int(to_number(a.get("Year Published")))
        yb = int(to_number(b.get("Year Published")))
        answer_row = a if (ya < yb) == ask_earlier else b
        answer = clean_text(answer_row["Name"])
        prompt_word = "earlier" if ask_earlier else "later"
        options = [clean_text(a["Name"]), clean_text(b["Name"])]
        return Question(
            prompt=f"Which was published **{prompt_word}**?",
            options=options,
            answer=answer,
            explanation=f"*{clean_text(a['Name'])}* was published in {ya}; *{clean_text(b['Name'])}* was published in {yb}.",
            source_title=answer,
            source_author=clean_text(answer_row["Author"]),
            mode=mode,
        )

    if mode == "Your rating: higher/lower":
        eligible = [r for r in rows if to_number(r.get("Rating (Normalized)")) is not None]
        if len(eligible) < 4:
            return None
        a, b = random.sample(eligible, 2)
        while to_number(a.get("Rating (Normalized)")) == to_number(b.get("Rating (Normalized)")) and len(eligible) > 2:
            a, b = random.sample(eligible, 2)
        ask_higher = random.choice([True, False])
        ra = to_number(a.get("Rating (Normalized)")) or 0
        rb = to_number(b.get("Rating (Normalized)")) or 0
        answer_row = a if (ra > rb) == ask_higher else b
        answer = clean_text(answer_row["Name"])
        prompt_word = "higher" if ask_higher else "lower"
        options = [clean_text(a["Name"]), clean_text(b["Name"])]
        return Question(
            prompt=f"Which did you rate **{prompt_word}**?",
            options=options,
            answer=answer,
            explanation=f"You rated *{clean_text(a['Name'])}* {ra:g}/4 and *{clean_text(b['Name'])}* {rb:g}/4.",
            source_title=answer,
            source_author=clean_text(answer_row["Author"]),
            mode=mode,
        )

    return None


def book_explanation(row: dict) -> str:
    title = clean_text(row.get("Name"))
    author = clean_text(row.get("Author"))
    bits = [f"*{title}* is by **{author}**."]

    year_read = clean_text(row.get("Year Read"))
    date_read = clean_text(row.get("Date Read"))
    year_pub = clean_text(row.get("Year Published"))
    rating = clean_text(row.get("Rating (Normalized)"))
    genre = clean_text(row.get("Genre (Simple)"))
    summary = clean_text(row.get("Summary (AI)"))

    details = []
    if date_read:
        details.append(f"read {date_read}")
    elif year_read:
        details.append(f"read in {year_read}")
    if year_pub:
        details.append(f"published {year_pub}")
    if genre:
        details.append(genre)
    if rating:
        details.append(f"rated {rating}/4")
    if details:
        bits.append("Details: " + "; ".join(details) + ".")
    if summary:
        bits.append("**Spreadsheet summary:** " + summary)

    note_parts = []
    for label, col in [
        ("At-the-time note", "Quick Summary/Notes (written at the time)"),
        ("Later note", "Notes (written much later)"),
        ("Favorite stories", "Favorite Stories"),
        ("Awards", "Awards"),
        ("Ownership", "Own a copy?"),
    ]:
        value = clean_text(row.get(col))
        if value:
            note_parts.append(f"- **{label}:** {value}")
    if note_parts:
        bits.append("**Your notes:**\n" + "\n".join(note_parts))
    return "\n\n".join(bits)


def http_json(url: str, timeout: int = 8) -> dict | None:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def first_sentences(text: str, max_sentences: int = 2, max_chars: int = 450) -> str:
    text = re.sub(r"\s+", " ", clean_text(text))
    if not text:
        return ""
    pieces = re.split(r"(?<=[.!?])\s+", text)
    out = " ".join(pieces[:max_sentences]).strip()
    if len(out) > max_chars:
        out = out[:max_chars].rsplit(" ", 1)[0].rstrip() + "…"
    return out


def wikipedia_search_title(query: str) -> str:
    if not clean_text(query):
        return ""
    url = (
        f"{WIKIPEDIA_API}?action=query&list=search&format=json"
        f"&srlimit=1&srsearch={quote(query)}"
    )
    data = http_json(url)
    try:
        return clean_text(data["query"]["search"][0]["title"]) if data else ""
    except (KeyError, IndexError, TypeError):
        return ""


def wikipedia_summary_for_query(query: str) -> dict:
    page_title = wikipedia_search_title(query)
    if not page_title:
        return {}
    data = http_json(WIKIPEDIA_SUMMARY + quote(page_title.replace(" ", "_")))
    if not data:
        return {}
    extract = first_sentences(clean_text(data.get("extract")))
    url = clean_text(data.get("content_urls", {}).get("desktop", {}).get("page"))
    title = clean_text(data.get("title")) or page_title
    if not extract:
        return {}
    return {"title": title, "extract": extract, "url": url}


def web_facts_markdown(title: str, author: str) -> str:
    """Fetch fresh contextual facts after each answered question.

    Uses Wikipedia's public APIs because they do not require API keys on Streamlit Cloud.
    Results are best-effort; obscure books may return author-only or no result.
    """
    book_queries = [
        f'"{title}" "{author}" book',
        f'{title} {author} novel OR book',
        f'{title} book',
    ]
    author_query = f'"{author}" writer author'

    book_fact = {}
    for query in book_queries:
        book_fact = wikipedia_summary_for_query(query)
        if book_fact:
            break
    author_fact = wikipedia_summary_for_query(author_query)

    lines = []
    if book_fact:
        src = f" ([source]({book_fact['url']}))" if book_fact.get("url") else ""
        lines.append(f"- **About the book/work:** {book_fact['extract']}{src}")
    if author_fact and normalize_answer(author_fact.get("title")) != normalize_answer(book_fact.get("title", "")):
        src = f" ([source]({author_fact['url']}))" if author_fact.get("url") else ""
        lines.append(f"- **About the author:** {author_fact['extract']}{src}")

    if not lines:
        return "I could not find a reliable quick web fact for this one. This can happen with obscure titles, short fiction, or ambiguous titles."
    return "\n".join(lines)


def log_answer(question: Question, choice: str, correct: bool) -> None:
    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mode": question.mode,
        "title": question.source_title,
        "author": question.source_author,
        "choice": choice,
        "answer": question.answer,
        "correct": correct,
    }
    history = pd.DataFrame([row])
    try:
        if HISTORY_FILE.exists():
            history.to_csv(HISTORY_FILE, mode="a", index=False, header=False)
        else:
            history.to_csv(HISTORY_FILE, index=False)
    except OSError:
        # Hosted apps may have temporary or read-only storage; session tracking still works.
        pass


def history_df() -> pd.DataFrame:
    if not HISTORY_FILE.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(HISTORY_FILE)
    except Exception:
        return pd.DataFrame()


def bool_series(values: pd.Series) -> pd.Series:
    """Handle booleans read back from CSV as True/False or strings."""
    return values.map(lambda x: str(x).strip().lower() in {"true", "1", "yes"})


def cumulative_stats() -> tuple[int, int, float]:
    hist = history_df()
    if hist.empty or "correct" not in hist.columns:
        return 0, 0, 0.0
    attempts = len(hist)
    score = int(bool_series(hist["correct"]).sum())
    accuracy = (score / attempts * 100) if attempts else 0.0
    return score, attempts, accuracy


def reset_saved_history() -> None:
    try:
        if HISTORY_FILE.exists():
            HISTORY_FILE.unlink()
    except OSError:
        pass


def sidebar_filters(df: pd.DataFrame) -> dict:
    with st.sidebar:
        st.header("Quiz setup")
        exclude_dnf = st.checkbox("Exclude likely DNF entries", value=True)

        min_rating = None
        if "Rating (Normalized)" in df.columns:
            enable_min = st.checkbox("Filter by minimum rating", value=False)
            if enable_min:
                min_rating = st.slider("Minimum normalized rating", 0.0, 5.0, 3.0, 0.1)

        genres = []
        if "Genre (Simple)" in df.columns:
            genres = st.multiselect("Genre", unique_nonempty(df["Genre (Simple)"]))

        types = []
        if "Type" in df.columns:
            types = st.multiselect("Type", unique_nonempty(df["Type"]))

        authors = []
        if "Author" in df.columns:
            authors = st.multiselect("Author", unique_nonempty(df["Author"]), max_selections=20)

        year_read_values = []
        if "Year Read" in df.columns:
            year_read_values = st.multiselect("Year read", unique_nonempty(df["Year Read"]))

    return {
        "exclude_dnf": exclude_dnf,
        "genres": genres,
        "types": types,
        "authors": authors,
        "min_rating": min_rating,
        "year_read_values": year_read_values,
    }


def reset_quiz_state() -> None:
    for key in ["question", "answered", "last_choice", "mode_for_question", "show_options", "typed_answer"]:
        st.session_state.pop(key, None)


def next_question(df: pd.DataFrame, mode: str) -> None:
    st.session_state.question = make_question(df, mode)
    st.session_state.mode_for_question = mode
    st.session_state.answered = False
    st.session_state.last_choice = None
    st.session_state.show_options = False
    st.session_state.typed_answer = ""


def main() -> None:
    st.set_page_config(page_title="Maks Book Quiz", page_icon="📚", layout="centered")
    st.markdown(MOBILE_CSS, unsafe_allow_html=True)
    st.title("📚 Maks Book Quiz")
    st.caption("A phone-friendly quiz for your reading database.")

    for key, default in [("score", 0), ("attempts", 0), ("missed", [])]:
        if key not in st.session_state:
            st.session_state[key] = default

    with st.sidebar:
        st.header("Booklist")
        uploaded = st.file_uploader("Upload newer booklist .xlsx", type=["xlsx"])
        st.caption("Without an upload, the bundled workbook is used.")

    source = uploaded if uploaded is not None else DEFAULT_WORKBOOK

    if not DEFAULT_WORKBOOK.exists() and uploaded is None:
        st.error("No bundled workbook found. Upload your booklist .xlsx in the sidebar.")
        return

    try:
        sheets = sheet_names(source)
    except Exception as exc:
        st.error(f"Could not read workbook: {exc}")
        return

    preferred = [s for s in ["Book List", "Short Fiction"] if s in sheets]
    sheet_options = preferred or sheets
    with st.sidebar:
        sheet = st.selectbox("Sheet", sheet_options)

    try:
        raw_df = load_workbook(source, sheet)
    except Exception as exc:
        st.error(f"Could not load sheet: {exc}")
        return

    if not has_required_columns(raw_df):
        st.error("This sheet needs at least `Name` and `Author` columns.")
        st.write("Columns found:", list(raw_df.columns))
        return

    filters = sidebar_filters(raw_df)
    df = filter_books(raw_df, **filters)

    with st.sidebar:
        st.divider()
        mode = st.selectbox("Quiz mode", QUIZ_MODES)
        if st.button("New question", use_container_width=True):
            next_question(df, mode)
        if st.button("Reset cumulative score", use_container_width=True):
            reset_saved_history()
            st.session_state.score = 0
            st.session_state.attempts = 0
            st.session_state.missed = []
            reset_quiz_state()
            st.rerun()

    total = len(df)
    st.info(f"Quiz pool: **{total}** entries from **{sheet}**.")
    if total < 4:
        st.warning("The current filters leave fewer than 4 entries. Loosen the filters to generate multiple-choice questions.")
        return

    cumulative_score, cumulative_attempts, cumulative_accuracy = cumulative_stats()

    col1, col2, col3 = st.columns(3)
    col1.metric("Cumulative score", cumulative_score)
    col2.metric("Cumulative attempts", cumulative_attempts)
    col3.metric("Cumulative accuracy", f"{cumulative_accuracy:.0f}%")

    if cumulative_attempts:
        st.caption("Cumulative score is saved to the app's quiz-history file and should survive closing/reopening the app. On Streamlit Cloud, it may still reset if the hosted app is rebuilt or restarted.")

    if (
        "question" not in st.session_state
        or st.session_state.question is None
        or st.session_state.get("mode_for_question") != mode
    ):
        next_question(df, mode)

    question: Question | None = st.session_state.question
    if question is None:
        st.warning("Could not make a question for this mode. Try a different mode or loosen filters.")
        return

    st.markdown('<div class="question-card">', unsafe_allow_html=True)
    st.subheader("Question")
    st.markdown(question.prompt)
    st.markdown('</div>', unsafe_allow_html=True)

    disabled = st.session_state.get("answered", False)

    if not disabled:
        with st.form(key=f"typed_answer_form_{question.mode}_{question.source_title}_{question.source_author}"):
            typed_answer = st.text_input("Write your answer", value=st.session_state.get("typed_answer", ""))
            submitted = st.form_submit_button("Check answer", type="primary", use_container_width=True)

        if submitted:
            if not clean_text(typed_answer):
                st.warning("Type an answer, or use the multiple-choice button if you are not sure.")
            else:
                correct = answers_match(typed_answer, question.answer)
                st.session_state.answered = True
                st.session_state.last_choice = typed_answer
                st.session_state.typed_answer = typed_answer
                st.session_state.attempts += 1
                if correct:
                    st.session_state.score += 1
                else:
                    st.session_state.missed.append(
                        {
                            "mode": question.mode,
                            "title": question.source_title,
                            "author": question.source_author,
                            "your_answer": typed_answer,
                            "correct_answer": question.answer,
                        }
                    )
                log_answer(question, typed_answer, correct)
                st.rerun()

        if not st.session_state.get("show_options", False):
            if st.button("I'm not sure — show multiple choice options", use_container_width=True):
                st.session_state.show_options = True
                st.rerun()

    if st.session_state.get("show_options", False) and not disabled:
        st.markdown("#### Multiple choice")
        for i, option in enumerate(question.options):
            if st.button(option, key=f"option_{i}_{option}", use_container_width=True):
                correct = option == question.answer
                st.session_state.answered = True
                st.session_state.last_choice = option
                st.session_state.attempts += 1
                if correct:
                    st.session_state.score += 1
                else:
                    st.session_state.missed.append(
                        {
                            "mode": question.mode,
                            "title": question.source_title,
                            "author": question.source_author,
                            "your_answer": option,
                            "correct_answer": question.answer,
                        }
                    )
                log_answer(question, option, correct)
                st.rerun()

    if st.session_state.get("answered", False):
        choice = st.session_state.get("last_choice")
        if answers_match(choice, question.answer):
            st.success("Correct.")
        else:
            st.error(f"Not quite. Correct answer: **{question.answer}**")
        st.markdown(question.explanation)

        st.markdown("#### Web facts")
        with st.spinner("Pulling a couple of fresh facts from the web..."):
            st.markdown(web_facts_markdown(question.source_title, question.source_author))

        if st.button("Next question", type="primary", use_container_width=True):
            next_question(df, mode)
            st.rerun()

    with st.expander("Missed this session"):
        if st.session_state.missed:
            missed = pd.DataFrame(st.session_state.missed)
            st.dataframe(missed, hide_index=True, use_container_width=True)
            st.download_button(
                "Download missed questions",
                missed.to_csv(index=False).encode("utf-8"),
                file_name="book_quiz_missed.csv",
                mime="text/csv",
                use_container_width=True,
            )
        else:
            st.write("No misses yet.")

    with st.expander("Saved quiz history"):
        hist = history_df()
        if hist.empty:
            st.write("No saved history yet. On hosted Streamlit, saved history may reset when the app is rebuilt or restarted.")
        else:
            saved_score, saved_attempts, saved_accuracy = cumulative_stats()
            st.write(f"Cumulative: **{saved_score}/{saved_attempts}** correct — **{saved_accuracy:.0f}%**.")
            st.dataframe(hist.tail(50), hide_index=True, use_container_width=True)
            st.download_button(
                "Download full history",
                hist.to_csv(index=False).encode("utf-8"),
                file_name="book_quiz_history.csv",
                mime="text/csv",
                use_container_width=True,
            )

    with st.expander("Browse current quiz pool"):
        display_cols = [
            c
            for c in [
                "Name",
                "Author",
                "Year Read",
                "Year Published",
                "Genre (Simple)",
                "Type",
                "Rating (Normalized)",
            ]
            if c in df.columns
        ]
        st.dataframe(df[display_cols], hide_index=True, use_container_width=True)


if __name__ == "__main__":
    main()
