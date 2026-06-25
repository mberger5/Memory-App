# Maks Book Quiz

A phone-friendly Streamlit web app that quizzes you on your reading database.

## What it does

- Uses the bundled `Maks_Booklist_enriched_2026-04-25.xlsx` by default.
- Lets you upload a newer `.xlsx` booklist from the sidebar.
- Supports the `Book List` and `Short Fiction` sheets when present.
- Excludes likely DNF rows by default.
- Includes quiz modes for:
  - Title → Author
  - Author → Title
  - Summary → Title
  - Summary → Author
  - Published earlier/later
  - Your rating higher/lower
- Tracks score and missed questions for the current session.
- Lets you download missed questions/history as CSV.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to Streamlit Community Cloud

1. Create a GitHub account if you do not already have one.
2. Create a new GitHub repository, ideally private if you are bundling your personal booklist.
3. Upload these files to the root of the repository:
   - `app.py`
   - `requirements.txt`
   - `runtime.txt`
   - `.streamlit/config.toml`
   - `Maks_Booklist_enriched_2026-04-25.xlsx`
4. Go to Streamlit Community Cloud.
5. Create a new app from the GitHub repo.
6. Set the main file path to `app.py`.
7. Deploy.

## Using it on iPhone

After deployment, open the Streamlit URL in Safari. Use Share → Add to Home Screen to create an app-like icon.

## Privacy note

If the Excel file is committed to GitHub, it is stored with the repo. Use a private GitHub repository if you do not want the booklist public. Streamlit Community Cloud can deploy from GitHub repositories, including private repositories connected to your account.
