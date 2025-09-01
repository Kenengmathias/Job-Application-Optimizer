# Job Application Optimizer

A tool to optimize job applications by analyzing resumes against job descriptions, suggesting improvements, generating cover letters, and tracking applications.

## Setup

1. Open in GitHub Codespaces.
2. Ensure dependencies are installed: `pip install -r requirements.txt`.
3. Run the main script: `python main.py`.

## Usage

- Upload a resume (PDF) to the `uploads/` folder.
- Paste a job description when prompted.
- View suggested keywords and generated cover letter.
- Track applications in `applications.db`.

## Tech Stack

- Python 3.12
- Libraries: pdfplumber, nltk, spacy, fuzzywuzzy, flask, jinja2
- Database: SQLite
