import pdfplumber
import nltk
from fuzzywuzzy import fuzz
import sqlite3
from jinja2 import Template
import os

# Download NLTK data
nltk.download('punkt')

def parse_resume(pdf_path):
    """Extract text from a resume PDF."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = "".join(page.extract_text() for page in pdf.pages if page.extract_text())
        return text
    except Exception as e:
        return f"Error parsing resume: {e}"

def extract_keywords(text):
    """Extract keywords from text using basic tokenization."""
    tokens = nltk.word_tokenize(text.lower())
    return [t for t in tokens if t.isalpha() and len(t) > 3]

def compare_texts(resume_keywords, job_keywords):
    """Compare resume and job description keywords, return missing ones."""
    missing = [k for k in job_keywords if max(fuzz.ratio(k, r) for r in resume_keywords) < 80]
    return missing[:5]  # Limit to top 5 for simplicity

def generate_cover_letter(name, job_title, company, skills):
    """Generate a cover letter from a template."""
    with open("templates/cover_letter_template.txt") as f:
        template = Template(f.read())
    return template.render(name=name, job_title=job_title, company=company, skills=skills)

def save_application(job_title, company, date, status):
    """Save application details to SQLite database."""
    conn = sqlite3.connect("applications.db")
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS apps (job_title TEXT, company TEXT, date TEXT, status TEXT)")
    c.execute("INSERT INTO apps VALUES (?, ?, ?, ?)", (job_title, company, date, status))
    conn.commit()
    conn.close()

def main():
    # Get user inputs
    pdf_path = input("Enter path to resume PDF (e.g., uploads/resume.pdf): ")
    job_desc = input("Paste job description: ")
    name = input("Enter your name: ")
    job_title = input("Enter job title: ")
    company = input("Enter company name: ")

    # Process resume and job description
    resume_text = parse_resume(pdf_path)
    if "Error" in resume_text:
        print(resume_text)
        return

    resume_keywords = extract_keywords(resume_text)
    job_keywords = extract_keywords(job_desc)
    missing_keywords = compare_texts(resume_keywords, job_keywords)

    # Output suggestions
    if missing_keywords:
        print("Suggested keywords to add to your resume:")
        for kw in missing_keywords:
            print(f"- {kw}")
    else:
        print("Your resume aligns well with the job description!")

    # Generate and display cover letter
    cover_letter = generate_cover_letter(name, job_title, company, missing_keywords)
    print("\nGenerated Cover Letter:")
    print(cover_letter)

    # Save application
    save_application(job_title, company, "2025-09-01", "Applied")
    print("\nApplication saved to database.")

if __name__ == "__main__":
    main()
