from flask import Flask, request, render_template
import pdfplumber
import nltk
from fuzzywuzzy import fuzz
import sqlite3
from jinja2 import Template
import os
import re

nltk.download('punkt')

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def parse_resume(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = "".join(page.extract_text() for page in pdf.pages if page.extract_text())
        # Extract achievements from Sonia
        achievements = re.findall(r"Experience:.*?(?=\n[A-Z]|\Z)", text, re.DOTALL)
        achievements = achievements[0] if achievements else "No experience section found."
        return text, achievements
    except Exception as e:
        return f"Error parsing resume: {e}", ""

def extract_keywords(text):
    tokens = nltk.word_tokenize(text.lower())
    return [t for t in tokens if t.isalpha() and len(t) > 3]

def compare_texts(resume_keywords, job_keywords):
    missing = [k for k in job_keywords if max(fuzz.ratio(k, r) for r in resume_keywords) < 80]
    return missing[:5]

def generate_cover_letter(name, job_title, company, skills, achievements):
    with open("templates/cover_letter_template.txt") as f:
        template = Template(f.read())
    return template.render(name=name, job_title=job_title, company=company, skills=skills, achievements=achievements)

def save_application(job_title, company, date, status):
    conn = sqlite3.connect("applications.db")
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS apps (job_title TEXT, company TEXT, date TEXT, status TEXT)")
    c.execute("INSERT INTO apps VALUES (?, ?, ?, ?)", (job_title, company, date, status))
    conn.commit()
    conn.close()

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        resume = request.files.get("resume")
        job_desc = request.form.get("job_desc")
        name = request.form.get("name")
        job_title = request.form.get("job_title")
        company = request.form.get("company")

        if not resume or not job_desc or not name or not job_title or not company:
            return render_template("index.html", error="All fields are required.")

        resume_path = os.path.join(app.config['UPLOAD_FOLDER'], resume.filename)
        resume.save(resume_path)

        resume_text, achievements = parse_resume(resume_path)
        if "Error" in resume_text:
            return render_template("index.html", error=resume_text)

        resume_keywords = extract_keywords(resume_text)
        job_keywords = extract_keywords(job_desc)
        missing_keywords = compare_texts(resume_keywords, job_keywords)
        cover_letter = generate_cover_letter(name, job_title, company, missing_keywords, achievements)

        save_application(job_title, company, "2025-09-01", "Applied")

        return render_template("results.html", missing=missing_keywords, cover_letter=cover_letter, achievements=achievements)

    return render_template("index.html", error=None)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
