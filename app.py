from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pandas as pd
import streamlit as st

from quiz_engine import (
    AUTHOR_COL,
    FACT_SHEET,
    GENRE_COL,
    RATING_COL,
    TITLE_COL,
    YEAR_READ_COL,
    FollowUpQuestion,
    QuizRound,
    answer_matches,
    book_notes_markdown,
    build_round,
    clean_text,
    filter_rows,
    group_books,
    normalize_answer,
    opening_skill,
    prepare_book_rows,
    prepare_facts,
    unique_nonempty,
)

APP_DIR = Path(__file__).resolve().parent
DEFAULT_WORKBOOK = APP_DIR / "Maks_Booklist_enriched_2026-06-24_updated_notes_corrected.xlsx"
HISTORY_FILE = APP_DIR / ".book_quiz_history_v2.csv"

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
USER_AGENT = "MaksBookQuiz/2.0 (personal Streamlit app)"

SKILL_ORDER = [
    "Book identification",
    "Author identification",
    "Characters",
    "Plot",
    "Themes",
    "Personal reading memory",
    "Publication knowledge",
    "Connections",
]

MOBILE_CSS = """
<style>
    .block-container {
        padding-top: 1rem;
        padding-bottom: 5rem;
        max-width: 760px;
    }
    div.stButton > button {
        min-height: 3rem;
        white-space: normal;
        border-radius: 0.8rem;
        font-size: 1rem;
        line-height: 1.25;
    }
    div[data-testid="stMetric"] {
        background: rgba(250, 250, 250, 0.68);
        padding: .5rem .6rem;
        border-radius: .8rem;
        border: 1px solid rgba(49, 51, 63, 0.13);
    }
    .question-card {
        padding: 1rem 1.05rem;
        border: 1px solid rgba(49, 51, 63, 0.15);
        border-radius: 1rem;
        background: rgba(250, 250, 250, 0.72);
        margin: .4rem 0 .8rem 0;
    }
    .subtle-card {
        padding: .75rem .9rem;
        border-radius: .8rem;
        background: rgba(128, 128, 128, 0.08);
        margin: .35rem 0;
    }
    section[data-testid="stSidebar"] div.stButton > button {
        min-height: 2.6rem;
    }
</style>
"""


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def load_workbook_bytes(data: bytes) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    excel = pd.ExcelFile(BytesIO(data))
    if "Book List" not in excel.sheet_names:
        raise ValueError("Workbook does not contain a Book List sheet.")
    books = pd.read_excel(excel, sheet_name="Book List", dtype=str)
    facts = pd.read_excel(excel, sheet_name=FACT_SHEET, dtype=str) if FACT_SHEET in excel.sheet_names else None
    return books, facts


@st.cache_data(show_spinner=False)
def load_workbook_path(path: str) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    excel = pd.ExcelFile(path)
    if "Book List" not in excel.sheet_names:
        raise ValueError("Workbook does not contain a Book List sheet.")
    books = pd.read_excel(excel, sheet_name="Book List", dtype=str)
    facts = pd.read_excel(excel, sheet_name=FACT_SHEET, dtype=str) if FACT_SHEET in excel.sheet_names else None
    return books, facts


def pool_signature(books: list[dict], target_mix: str, difficulty: str) -> str:
    raw = "|".join(sorted(item["book_key"] for item in books)) + f"::{target_mix}::{difficulty}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# History and statistics
# ---------------------------------------------------------------------------

HISTORY_COLUMNS = [
    "timestamp",
    "round_id",
    "book_key",
    "title",
    "author",
    "stage",
    "skill",
    "subtype",
    "question",
    "answer_mode",
    "assistance",
    "user_answer",
    "correct_answer",
    "result_score",
    "points_awarded",
    "max_points",
    "response_seconds",
]


def append_history(row: dict) -> None:
    clean_row = {column: row.get(column, "") for column in HISTORY_COLUMNS}
    st.session_state.session_results.append(clean_row)
    try:
        frame = pd.DataFrame([clean_row], columns=HISTORY_COLUMNS)
        if HISTORY_FILE.exists():
            frame.to_csv(HISTORY_FILE, mode="a", index=False, header=False)
        else:
            frame.to_csv(HISTORY_FILE, index=False)
    except OSError:
        # Streamlit Community Cloud storage can be temporary. Part 6 will replace
        # this with durable storage; the current session still retains the result.
        pass


def history_df() -> pd.DataFrame:
    if not HISTORY_FILE.exists():
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    try:
        hist = pd.read_csv(HISTORY_FILE)
        for col in ["result_score", "points_awarded", "max_points", "response_seconds"]:
            if col in hist.columns:
                hist[col] = pd.to_numeric(hist[col], errors="coerce")
        return hist
    except Exception:
        return pd.DataFrame(columns=HISTORY_COLUMNS)


def reset_saved_history() -> None:
    try:
        if HISTORY_FILE.exists():
            HISTORY_FILE.unlink()
    except OSError:
        pass


def combined_history() -> pd.DataFrame:
    saved = history_df()
    session = pd.DataFrame(st.session_state.session_results, columns=HISTORY_COLUMNS)
    if saved.empty:
        return session
    if session.empty:
        return saved
    combined = pd.concat([saved, session], ignore_index=True)
    dedupe_cols = ["timestamp", "round_id", "stage", "subtype", "user_answer"]
    return combined.drop_duplicates(subset=dedupe_cols, keep="last").reset_index(drop=True)


def skill_stats(hist: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for skill in SKILL_ORDER:
        subset = hist[hist["skill"] == skill] if not hist.empty and "skill" in hist.columns else pd.DataFrame()
        attempts = len(subset)
        recall = float(subset["result_score"].mean() * 100) if attempts else 0.0
        unaided = subset[subset["assistance"] == "unaided"] if attempts and "assistance" in subset.columns else pd.DataFrame()
        unaided_attempts = len(unaided)
        unaided_recall = float(unaided["result_score"].mean() * 100) if unaided_attempts else 0.0
        rows.append(
            {
                "Skill": skill,
                "Attempts": attempts,
                "Recall": f"{recall:.0f}%" if attempts else "—",
                "Unaided attempts": unaided_attempts,
                "Unaided recall": f"{unaided_recall:.0f}%" if unaided_attempts else "—",
            }
        )
    return pd.DataFrame(rows)


def log_question_result(
    round_obj: QuizRound,
    *,
    stage: str,
    skill: str,
    subtype: str,
    question: str,
    answer_mode: str,
    assistance: str,
    user_answer: str,
    correct_answer: str,
    result_score: float,
    points_awarded: float,
    max_points: float,
    started_at: float,
) -> None:
    append_history(
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "round_id": round_obj.round_id,
            "book_key": round_obj.book_key,
            "title": round_obj.title,
            "author": round_obj.author,
            "stage": stage,
            "skill": skill,
            "subtype": subtype,
            "question": re.sub(r"\s+", " ", question).strip(),
            "answer_mode": answer_mode,
            "assistance": assistance,
            "user_answer": user_answer,
            "correct_answer": correct_answer,
            "result_score": result_score,
            "points_awarded": points_awarded,
            "max_points": max_points,
            "response_seconds": round(max(0.0, time.time() - started_at), 1),
        }
    )


# ---------------------------------------------------------------------------
# Live web facts (fallback until Part 5 supplies curated facts)
# ---------------------------------------------------------------------------

def http_json(url: str, timeout: int = 8) -> dict | None:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def first_sentences(text: str, max_sentences: int = 2, max_chars: int = 430) -> str:
    text = re.sub(r"\s+", " ", clean_text(text))
    if not text:
        return ""
    pieces = re.split(r"(?<=[.!?])\s+", text)
    output = " ".join(pieces[:max_sentences]).strip()
    if len(output) > max_chars:
        output = output[:max_chars].rsplit(" ", 1)[0].rstrip() + "…"
    return output


def wikipedia_search_title(query: str) -> str:
    url = f"{WIKIPEDIA_API}?action=query&list=search&format=json&srlimit=1&srsearch={quote(query)}"
    data = http_json(url)
    try:
        return clean_text(data["query"]["search"][0]["title"]) if data else ""
    except (KeyError, IndexError, TypeError):
        return ""


def wikipedia_summary(query: str) -> dict:
    page_title = wikipedia_search_title(query)
    if not page_title:
        return {}
    data = http_json(WIKIPEDIA_SUMMARY + quote(page_title.replace(" ", "_")))
    if not data:
        return {}
    extract = first_sentences(clean_text(data.get("extract")))
    if not extract:
        return {}
    return {
        "title": clean_text(data.get("title")) or page_title,
        "extract": extract,
        "url": clean_text(data.get("content_urls", {}).get("desktop", {}).get("page")),
    }


@st.cache_data(show_spinner=False, ttl=86400)
def web_facts_markdown(title: str, author: str) -> str:
    book_fact = {}
    for query in [f'"{title}" "{author}" book', f"{title} {author} novel book", f'"{title}" book']:
        book_fact = wikipedia_summary(query)
        if book_fact:
            break
    author_fact = wikipedia_summary(f'"{author}" writer author')
    lines = []
    if book_fact:
        source = f" ([source]({book_fact['url']}))" if book_fact.get("url") else ""
        lines.append(f"- **About the book:** {book_fact['extract']}{source}")
    if author_fact and normalize_answer(author_fact.get("title")) != normalize_answer(book_fact.get("title", "")):
        source = f" ([source]({author_fact['url']}))" if author_fact.get("url") else ""
        lines.append(f"- **About the author:** {author_fact['extract']}{source}")
    return "\n".join(lines) if lines else "No reliable quick web fact was found for this title."


# ---------------------------------------------------------------------------
# Session and round state
# ---------------------------------------------------------------------------

def initialize_session() -> None:
    defaults = {
        "session_results": [],
        "round_obj": None,
        "stage": "identify",
        "opening_attempts": 0,
        "hints_used": 0,
        "opening_show_mc": False,
        "opening_resolved": False,
        "opening_feedback": "",
        "opening_started_at": time.time(),
        "followup_index": 0,
        "followup_attempts": 0,
        "followup_show_mc": False,
        "followup_revealed": False,
        "followup_completed": False,
        "followup_feedback": "",
        "followup_user_answer": "",
        "followup_started_at": time.time(),
        "last_book_key": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_followup_state() -> None:
    st.session_state.followup_attempts = 0
    st.session_state.followup_show_mc = False
    st.session_state.followup_revealed = False
    st.session_state.followup_completed = False
    st.session_state.followup_feedback = ""
    st.session_state.followup_user_answer = ""
    st.session_state.followup_started_at = time.time()


def start_new_round(books: list[dict], facts_by_key: dict[str, dict], target_mix: str, difficulty: str) -> None:
    new_round = build_round(
        books,
        facts_by_key,
        target_mix=target_mix,
        difficulty=difficulty,
        avoid_book_key=st.session_state.get("last_book_key"),
    )
    st.session_state.round_obj = new_round
    st.session_state.last_book_key = new_round.book_key
    st.session_state.stage = "identify"
    st.session_state.opening_attempts = 0
    st.session_state.hints_used = 0
    st.session_state.opening_show_mc = False
    st.session_state.opening_resolved = False
    st.session_state.opening_feedback = ""
    st.session_state.opening_started_at = time.time()
    st.session_state.followup_index = 0
    reset_followup_state()


def resolve_opening(
    round_obj: QuizRound,
    *,
    user_answer: str,
    result_score: float,
    points: float,
    answer_mode: str,
    assistance: str,
    feedback: str,
) -> None:
    if st.session_state.opening_resolved:
        return
    st.session_state.opening_resolved = True
    st.session_state.opening_feedback = feedback
    log_question_result(
        round_obj,
        stage="opening",
        skill=opening_skill(round_obj),
        subtype="identify_title" if round_obj.target == "title" else "identify_author",
        question=round_obj.opening_prompt,
        answer_mode=answer_mode,
        assistance=assistance,
        user_answer=user_answer,
        correct_answer=round_obj.opening_answer,
        result_score=result_score,
        points_awarded=points,
        max_points=3.0,
        started_at=st.session_state.opening_started_at,
    )


def resolve_followup(
    round_obj: QuizRound,
    question: FollowUpQuestion,
    *,
    user_answer: str,
    result_score: float,
    points: float,
    answer_mode: str,
    assistance: str,
    feedback: str,
) -> None:
    if st.session_state.followup_completed:
        return
    st.session_state.followup_completed = True
    st.session_state.followup_feedback = feedback
    st.session_state.followup_user_answer = user_answer
    log_question_result(
        round_obj,
        stage="followup",
        skill=question.skill,
        subtype=question.subtype,
        question=question.prompt,
        answer_mode=answer_mode,
        assistance=assistance,
        user_answer=user_answer,
        correct_answer=question.answer,
        result_score=result_score,
        points_awarded=points,
        max_points=question.max_points,
        started_at=st.session_state.followup_started_at,
    )


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def current_points() -> tuple[float, float]:
    session = pd.DataFrame(st.session_state.session_results)
    if session.empty:
        return 0.0, 0.0
    return float(pd.to_numeric(session["points_awarded"], errors="coerce").fillna(0).sum()), float(
        pd.to_numeric(session["max_points"], errors="coerce").fillna(0).sum()
    )


def render_opening(round_obj: QuizRound) -> None:
    st.progress(0.05, text="Opening identification")
    st.markdown('<div class="question-card">', unsafe_allow_html=True)
    st.markdown(round_obj.opening_prompt)
    st.markdown("</div>", unsafe_allow_html=True)

    if st.session_state.opening_feedback and not st.session_state.opening_resolved:
        st.warning(st.session_state.opening_feedback)

    if not st.session_state.opening_resolved:
        with st.form(key=f"opening_form_{round_obj.round_id}_{st.session_state.opening_attempts}"):
            answer = st.text_input("Write your answer", placeholder="Take a guess — getting it wrong is fine.")
            submitted = st.form_submit_button("Submit answer", type="primary", use_container_width=True)
        if submitted:
            if not clean_text(answer):
                st.warning("Type an answer, request a hint, or use multiple choice.")
            elif answer_matches(answer, round_obj.opening_aliases):
                attempts = st.session_state.opening_attempts
                hints = st.session_state.hints_used
                if st.session_state.opening_show_mc:
                    points, assistance = 1.0, "multiple_choice"
                elif hints >= 2:
                    points, assistance = 1.0, "hint_2"
                elif hints == 1:
                    points, assistance = 2.0, "hint_1"
                elif attempts >= 1:
                    points, assistance = 2.0, "retry"
                else:
                    points, assistance = 3.0, "unaided"
                resolve_opening(
                    round_obj,
                    user_answer=answer,
                    result_score=1.0,
                    points=points,
                    answer_mode="typed",
                    assistance=assistance,
                    feedback="Correct.",
                )
                st.rerun()
            else:
                st.session_state.opening_attempts += 1
                st.session_state.opening_feedback = "Not quite. Try again, ask for a hint, or switch to multiple choice."
                st.rerun()

        for idx in range(st.session_state.hints_used):
            if idx < len(round_obj.hints):
                st.info(f"**Hint {idx + 1}:** {round_obj.hints[idx]}")

        col1, col2 = st.columns(2)
        with col1:
            if st.session_state.hints_used < len(round_obj.hints):
                if st.button("Give me a hint", use_container_width=True):
                    st.session_state.hints_used += 1
                    st.rerun()
        with col2:
            if not st.session_state.opening_show_mc:
                if st.button("Show multiple choice", use_container_width=True):
                    st.session_state.opening_show_mc = True
                    st.rerun()

        if st.session_state.opening_show_mc:
            st.markdown("#### Multiple choice")
            for index, option in enumerate(round_obj.options):
                if st.button(option, key=f"opening_option_{round_obj.round_id}_{index}", use_container_width=True):
                    correct = normalize_answer(option) == normalize_answer(round_obj.opening_answer)
                    resolve_opening(
                        round_obj,
                        user_answer=option,
                        result_score=1.0 if correct else 0.0,
                        points=1.0 if correct else 0.0,
                        answer_mode="choice",
                        assistance="multiple_choice",
                        feedback="Correct." if correct else f"Not quite. The answer is {round_obj.opening_answer}.",
                    )
                    st.rerun()

        if st.button("Reveal the answer", use_container_width=True):
            resolve_opening(
                round_obj,
                user_answer="",
                result_score=0.0,
                points=0.0,
                answer_mode="revealed",
                assistance="revealed",
                feedback=f"The answer is {round_obj.opening_answer}.",
            )
            st.rerun()

    else:
        if "Correct" in st.session_state.opening_feedback:
            st.success(st.session_state.opening_feedback)
        else:
            st.info(st.session_state.opening_feedback)
        st.markdown(f"### The book is *{round_obj.title}*")
        st.caption(f"By {round_obj.author}")
        if st.button(
            "Continue with this book",
            type="primary",
            use_container_width=True,
            key=f"continue_opening_{round_obj.round_id}",
        ):
            st.session_state.stage = "followup"
            reset_followup_state()
            st.rerun()


def auto_question_assistance() -> str:
    if st.session_state.followup_show_mc:
        return "multiple_choice"
    if st.session_state.followup_attempts:
        return "retry"
    return "unaided"


def render_auto_followup(round_obj: QuizRound, question: FollowUpQuestion) -> None:
    if st.session_state.followup_feedback and not st.session_state.followup_completed:
        st.warning(st.session_state.followup_feedback)

    if not st.session_state.followup_completed:
        with st.form(key=f"followup_form_{question.question_id}_{st.session_state.followup_attempts}"):
            answer = st.text_input("Your answer", placeholder="Take your best shot.")
            submitted = st.form_submit_button("Submit", type="primary", use_container_width=True)
        if submitted:
            accepted = question.acceptable_answers or [question.answer]
            if not clean_text(answer):
                st.warning("Type an answer or use one of the help options.")
            elif answer_matches(answer, accepted):
                assistance = auto_question_assistance()
                points = 0.5 if assistance == "multiple_choice" else (0.75 if assistance == "retry" else 1.0)
                resolve_followup(
                    round_obj,
                    question,
                    user_answer=answer,
                    result_score=1.0,
                    points=points,
                    answer_mode="typed",
                    assistance=assistance,
                    feedback="Correct.",
                )
                st.rerun()
            else:
                st.session_state.followup_attempts += 1
                st.session_state.followup_feedback = "Not quite. Try again, show choices, or reveal the answer."
                st.rerun()

        if question.options and not st.session_state.followup_show_mc:
            if st.button("I'm not sure — show choices", use_container_width=True):
                st.session_state.followup_show_mc = True
                st.rerun()

        if st.session_state.followup_show_mc and question.options:
            for index, option in enumerate(question.options):
                if st.button(option, key=f"followup_option_{question.question_id}_{index}", use_container_width=True):
                    accepted = question.acceptable_answers or [question.answer]
                    correct = answer_matches(option, accepted)
                    resolve_followup(
                        round_obj,
                        question,
                        user_answer=option,
                        result_score=1.0 if correct else 0.0,
                        points=0.5 if correct else 0.0,
                        answer_mode="choice",
                        assistance="multiple_choice",
                        feedback="Correct." if correct else "Not quite.",
                    )
                    st.rerun()

        if st.button("Reveal answer", use_container_width=True):
            resolve_followup(
                round_obj,
                question,
                user_answer="",
                result_score=0.0,
                points=0.0,
                answer_mode="revealed",
                assistance="revealed",
                feedback="Answer revealed.",
            )
            st.rerun()


def render_choice_followup(round_obj: QuizRound, question: FollowUpQuestion) -> None:
    if not st.session_state.followup_completed:
        for index, option in enumerate(question.options):
            if st.button(option, key=f"choice_{question.question_id}_{index}", use_container_width=True):
                correct = normalize_answer(option) == normalize_answer(question.answer)
                resolve_followup(
                    round_obj,
                    question,
                    user_answer=option,
                    result_score=1.0 if correct else 0.0,
                    points=1.0 if correct else 0.0,
                    answer_mode="choice",
                    assistance="built_in_choice",
                    feedback="Correct." if correct else "Not quite.",
                )
                st.rerun()


def render_self_followup(round_obj: QuizRound, question: FollowUpQuestion) -> None:
    if st.session_state.followup_completed:
        if st.session_state.followup_user_answer:
            st.markdown("**Your answer:**")
            st.markdown(f"> {st.session_state.followup_user_answer}")
        st.markdown("**Prepared answer:**")
        st.markdown(question.answer)
        return
    if not st.session_state.followup_revealed:
        with st.form(key=f"self_form_{question.question_id}"):
            answer = st.text_area(
                "What do you remember?",
                placeholder="A rough answer is enough. You will grade yourself after seeing the prepared answer.",
                height=120,
            )
            submitted = st.form_submit_button("Show prepared answer", type="primary", use_container_width=True)
        if submitted:
            st.session_state.followup_user_answer = answer
            st.session_state.followup_revealed = True
            st.rerun()
    else:
        if st.session_state.followup_user_answer:
            st.markdown("**Your answer:**")
            st.markdown(f"> {st.session_state.followup_user_answer}")
        else:
            st.caption("You left your answer blank.")
        st.markdown("**Prepared answer:**")
        st.markdown(question.answer)
        st.caption("Grade the substance of your recall, not whether your wording matched exactly.")
        col1, col2, col3 = st.columns(3)
        with col1:
            remembered = st.button("Remembered", key=f"remembered_{question.question_id}", use_container_width=True)
        with col2:
            partial = st.button("Partial", key=f"partial_{question.question_id}", use_container_width=True)
        with col3:
            missed = st.button("Missed", key=f"missed_{question.question_id}", use_container_width=True)
        if remembered or partial or missed:
            if remembered:
                score, label = 1.0, "Remembered."
            elif partial:
                score, label = 0.5, "Partially remembered."
            else:
                score, label = 0.0, "Missed."
            resolve_followup(
                round_obj,
                question,
                user_answer=st.session_state.followup_user_answer,
                result_score=score,
                points=score,
                answer_mode="self_grade",
                assistance="unaided",
                feedback=label,
            )
            st.rerun()


def render_followup(round_obj: QuizRound) -> None:
    index = st.session_state.followup_index
    if index >= len(round_obj.followups):
        st.session_state.stage = "end"
        st.rerun()
        return

    question = round_obj.followups[index]
    progress = (index + 1) / (len(round_obj.followups) + 1)
    st.progress(progress, text=f"Follow-up {index + 1} of {len(round_obj.followups)} · {question.skill}")
    st.markdown('<div class="question-card">', unsafe_allow_html=True)
    st.markdown(f"**{question.prompt}**")
    st.markdown("</div>", unsafe_allow_html=True)

    if question.spoiler:
        st.warning("This follow-up contains a spoiler.")

    if question.grading == "self":
        render_self_followup(round_obj, question)
    elif question.grading == "choice":
        render_choice_followup(round_obj, question)
    else:
        render_auto_followup(round_obj, question)

    if st.session_state.followup_completed:
        if st.session_state.followup_feedback.startswith("Correct") or st.session_state.followup_feedback.startswith("Remembered"):
            st.success(st.session_state.followup_feedback)
        elif st.session_state.followup_feedback.startswith("Partially"):
            st.warning(st.session_state.followup_feedback)
        else:
            st.info(st.session_state.followup_feedback)
        st.markdown(question.explanation)
        if question.grading != "self" or not st.session_state.followup_revealed:
            st.markdown(f"**Answer:** {question.answer}")
        button_label = "Finish round" if index + 1 >= len(round_obj.followups) else "Next follow-up"
        if st.button(
            button_label,
            type="primary",
            use_container_width=True,
            key=f"advance_{round_obj.round_id}_{question.question_id}",
        ):
            st.session_state.followup_index += 1
            if st.session_state.followup_index >= len(round_obj.followups):
                st.session_state.stage = "end"
            reset_followup_state()
            st.rerun()


def render_round_end(round_obj: QuizRound, books: list[dict], facts_by_key: dict[str, dict], target_mix: str, difficulty: str) -> None:
    st.progress(1.0, text="Round complete")
    st.success("Round complete.")
    st.markdown(book_notes_markdown(round_obj))

    st.markdown("### Fun facts")
    if round_obj.facts:
        curated = [
            clean_text(round_obj.facts.get("Fun Fact 1")),
            clean_text(round_obj.facts.get("Fun Fact 2")),
        ]
        curated = [fact for fact in curated if fact]
        if curated:
            st.markdown("\n".join(f"- {fact}" for fact in curated))
    with st.spinner("Checking for a couple of additional web facts..."):
        st.markdown(web_facts_markdown(round_obj.title, round_obj.author))

    if st.button("Start another round", type="primary", use_container_width=True):
        start_new_round(books, facts_by_key, target_mix, difficulty)
        st.rerun()


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="Maks Book Memory Quiz", page_icon="📚", layout="centered")
    st.markdown(MOBILE_CSS, unsafe_allow_html=True)
    initialize_session()

    st.title("📚 Maks Book Memory Quiz")
    st.caption("Identify the book title first, then stay with that book for author, plot, personal-memory, and connection questions.")

    with st.sidebar:
        st.header("Booklist")
        uploaded = st.file_uploader("Upload a newer .xlsx booklist", type=["xlsx"])
        st.caption("The app uses only the Book List sheet. DNF entries and individual short stories are excluded.")

    try:
        if uploaded is not None:
            raw_books, raw_facts = load_workbook_bytes(uploaded.getvalue())
        else:
            if not DEFAULT_WORKBOOK.exists():
                st.error("Bundled workbook is missing. Upload your booklist in the sidebar.")
                return
            raw_books, raw_facts = load_workbook_path(str(DEFAULT_WORKBOOK))
        prepared_rows = prepare_book_rows(raw_books)
        facts_by_key = prepare_facts(raw_facts)
    except Exception as exc:
        st.error(f"Could not load the workbook: {exc}")
        return

    with st.sidebar:
        st.header("Quiz setup")
        genre_options = sorted(unique_nonempty(prepared_rows[GENRE_COL])) if GENRE_COL in prepared_rows.columns else []
        genres = st.multiselect("Genre", genre_options)
        author_options = sorted(unique_nonempty(prepared_rows[AUTHOR_COL]))
        authors = st.multiselect("Author", author_options, max_selections=20)
        year_options = sorted(unique_nonempty(prepared_rows[YEAR_READ_COL])) if YEAR_READ_COL in prepared_rows.columns else []
        years_read = st.multiselect("Year read", year_options)
        min_rating = None
        if RATING_COL in prepared_rows.columns and st.checkbox("Use a minimum rating", value=False):
            min_rating = st.slider("Minimum rating", 0.0, 4.0, 2.5, 0.25)

        target_mix = "Only titles"
        st.caption("Every round begins by identifying the book title. Author recall comes next.")
        difficulty = st.selectbox("Opening difficulty", ["Challenging", "Mixed", "Easier"])

    filtered_rows = filter_rows(
        prepared_rows,
        genres=genres,
        authors=authors,
        years_read=years_read,
        min_rating=min_rating,
    )
    books = group_books(filtered_rows)

    with st.sidebar:
        st.divider()
        if st.button("New round", type="primary", use_container_width=True, disabled=not books):
            start_new_round(books, facts_by_key, target_mix, difficulty)
            st.rerun()
        if st.button("Reset current session", use_container_width=True):
            st.session_state.session_results = []
            st.session_state.round_obj = None
            st.rerun()
        if st.button("Reset saved statistics", use_container_width=True):
            reset_saved_history()
            st.session_state.session_results = []
            st.session_state.round_obj = None
            st.rerun()

    if not books:
        st.warning("No books remain under the current filters.")
        return

    signature = pool_signature(books, target_mix, difficulty)
    if st.session_state.get("pool_signature") != signature:
        st.session_state.pool_signature = signature
        start_new_round(books, facts_by_key, target_mix, difficulty)

    round_obj: QuizRound = st.session_state.round_obj

    session_points, session_max = current_points()
    hist = combined_history()
    total_points = float(hist["points_awarded"].sum()) if not hist.empty else 0.0
    total_max = float(hist["max_points"].sum()) if not hist.empty else 0.0
    total_recall = float(hist["result_score"].mean() * 100) if not hist.empty else 0.0

    col1, col2, col3 = st.columns(3)
    col1.metric("Session points", f"{session_points:g}/{session_max:g}")
    col2.metric("Cumulative points", f"{total_points:g}/{total_max:g}")
    col3.metric("Recall", f"{total_recall:.0f}%" if not hist.empty else "—")
    st.caption(f"Current quiz pool: **{len(books)} books**. This Part 4 version saves detailed history to a server file; durable cross-device storage comes in Part 6.")

    if st.session_state.stage == "identify":
        render_opening(round_obj)
    elif st.session_state.stage == "followup":
        render_followup(round_obj)
    else:
        render_round_end(round_obj, books, facts_by_key, target_mix, difficulty)

    with st.expander("Skill statistics"):
        st.caption("Each question is stored separately, so title, author, plot, character, theme, and personal-memory performance can diverge.")
        st.dataframe(skill_stats(hist), hide_index=True, use_container_width=True)
        if not facts_by_key:
            st.info("Character and theme rows will begin filling after Part 5 adds the curated Quiz Facts sheet.")

    with st.expander("Recent results"):
        if hist.empty:
            st.write("No results yet.")
        else:
            columns = [
                "timestamp",
                "title",
                "skill",
                "assistance",
                "result_score",
                "points_awarded",
            ]
            st.dataframe(hist[columns].tail(50), hide_index=True, use_container_width=True)
            st.download_button(
                "Download detailed history",
                hist.to_csv(index=False).encode("utf-8"),
                file_name="book_memory_quiz_history.csv",
                mime="text/csv",
                use_container_width=True,
            )

    with st.expander("Browse current book pool"):
        display_columns = [col for col in [TITLE_COL, AUTHOR_COL, YEAR_READ_COL, GENRE_COL, RATING_COL] if col in filtered_rows.columns]
        st.dataframe(filtered_rows[display_columns], hide_index=True, use_container_width=True)


if __name__ == "__main__":
    main()
