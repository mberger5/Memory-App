# Maks Book Memory Quiz — Part 4 interactive shell

This version replaces isolated flashcards with multi-step book rounds:

1. Identify a book or author from a real clue.
2. Type an answer first.
3. Retry, request up to two progressively easier hints, use multiple choice, or reveal.
4. Continue with the same book for author/title, plot, personal-memory, publication, series, and connection questions.
5. End with workbook notes and fresh web facts.

## Scope

- Uses only the `Book List` sheet.
- Excludes likely DNF entries.
- Excludes individual rows whose `Type` is `Short Story`.
- Keeps short-story collections and other books recorded as Book List entries.
- Combines reread rows for book-knowledge questions while preserving each reading event in personal-memory questions.

## Statistics

Every answer records its own skill, including:

- Book identification
- Author identification
- Characters
- Plot
- Themes
- Personal reading memory
- Publication knowledge
- Connections

The history also records whether the response was unaided, retried, hinted, multiple choice, self-graded, or revealed. The current version writes this to `.book_quiz_history_v2.csv` on the Streamlit server. Streamlit hosting can rebuild or reset that file; durable cross-device storage is planned for Part 6.

## Preparing for Part 5

The app already detects an optional `Quiz Facts` sheet and can use columns such as:

- `Name`
- `Author`
- `Opening Clue Hard`
- `Opening Clue Medium`
- `Opening Clue Easy`
- `Hint 1`
- `Hint 2`
- `Plot Question`
- `Plot Answer`
- `Character Question`
- `Character Answer`
- `Theme Question`
- `Theme Answer`
- `Fun Fact 1`
- `Fun Fact 2`
- `Source 1`
- `Source 2`

Part 5 will research and populate these fields. Without that sheet, the Part 4 app falls back to the existing AI summary, notes, ratings, dates, series, genre, and publication metadata.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Update the deployed GitHub app

Upload/replace these files in the root of the GitHub repository:

- `app.py`
- `quiz_engine.py` (new)
- `requirements.txt`
- `README.md`
- `Maks_Booklist_enriched_2026-06-24_updated_notes_corrected.xlsx`

Remove the older bundled workbook or leave it unused. Streamlit should redeploy after the commit.
