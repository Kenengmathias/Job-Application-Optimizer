from flask import Flask, request, render_template
import pdfplumber
import nltk
from fuzzywuzzy import fuzz
import sqlite3
from jinja2 import Template
import os
import re
import spacy
import requests
from bs4 import BeautifulSoup
import time

nltk.download('punkt')
nltk.download('stopwords')
nlp = spacy.load("en_core_web_sm")

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def parse_resume(pdf_path):
    try:
        with pdfplumber.open(pdf_path) as pdf:
            text = "".join(page.extract_text() for page in pdf.pages if page.extract_text())
        patterns = [
            r"(?:Experience|Work History|Professional Experience|Employment History):?\s*.*?((?=\n[A-Z])|\Z)",
            r"(?:Experience|Work History|Professional Experience|Employment History).*?(\n\s*-.*?(?=\n[A-Z]|\Z))"
        ]
        achievements = []
        for pattern in patterns:
            matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
            if matches:
                achievements.extend(matches)
        achievements = "\n".join(achievements).strip() if achievements else "No experience section found."
        skills_pattern = r"(?:Skills):?\s*.*?((?=\n[A-Z])|\Z)"
        skills_match = re.findall(skills_pattern, text, re.DOTALL | re.IGNORECASE)
        skills = skills_match[0].strip().split(', ') if skills_match else []
        return text, achievements, skills
    except Exception as e:
        return f"Error parsing resume: {e}", "", []

def extract_keywords(text):
    skill_list = {
        'programming': ['python', 'javascript', 'c++', 'java'],
        'web development': ['react', 'node.js', 'django', 'html', 'css'],
        'databases': ['mysql', 'postgresql', 'mongodb', 'sql'],
        'tools': ['git', 'docker', 'aws', 'linux'],
        'soft skills': ['problem-solving', 'teamwork', 'communication']
    }
    doc = nlp(text.lower())
    stopwords = set(nltk.corpus.stopwords.words('english') + ['seeking', 'proficient', 'experienced', 'developer'])
    keywords = []
    for token in doc:
        if token.text in [skill for skills in skill_list.values() for skill in skills] and token.text not in stopwords:
            keywords.append(token.text)
    return list(set(keywords))[:5]

def compare_texts(resume_keywords, job_keywords):
    missing = [k for k in job_keywords if k not in resume_keywords and max(fuzz.ratio(k, r) for r in resume_keywords) < 80]
    return missing[:5]

def generate_cover_letter(name, job_title, company, resume_keywords, missing_keywords, achievements):
    with open("templates/cover_letter_template.txt") as f:
        template = Template(f.read())
    achievements = achievements if achievements != "No experience section found." else "relevant professional experience."
    all_skills = list(set(resume_keywords + missing_keywords))[:5]
    all_skills = all_skills if all_skills else ["strong technical skills", "problem-solving"]
    return template.render(name=name, job_title=job_title, company=company, skills=all_skills, achievements=achievements)

def save_application(job_title, company, date, status):
    conn = sqlite3.connect("applications.db")
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS apps (job_title TEXT, company TEXT, date TEXT, status TEXT)")
    c.execute("INSERT INTO apps VALUES (?, ?, ?, ?)", (job_title, company, date, status))
    conn.commit()
    conn.close()

def search_jobs(query):
    jobs = []
    sites = [
        ("Upwork", f"https://www.upwork.com/nx/jobs/search/?q={query.replace(' ', '+')}", 'li', 'job-tile', 'a', 'job-title-link', 'href'),
        ("Freelancer", f"https://www.freelancer.com/job-search/{query.replace(' ', '-')}", 'div', 'JobSearchCard-item', 'a', 'JobSearchCard-primary-heading-link', 'href'),
        ("Fiverr", f"https://www.fiverr.com/search/gigs?query={query.replace(' ', '+')}", 'div', 'gig-card', 'h3', 'gig-title', 'href'),
        ("Indeed", f"https://www.indeed.com/jobs?q={query.replace(' ', '+')}", 'div', 'jobsearch-SerpJobCard', 'h2', 'title', 'href'),
        ("LinkedIn", f"https://www.linkedin.com/jobs/search?keywords={query.replace(' ', '+')}", 'div', 'job-card-container', 'a', 'base-card__title-link', 'href'),
        ("Toptal", f"https://www.toptal.com/jobs?search={query.replace(' ', '+')}", 'div', 'job-listing-card', 'a', 'job-title', 'href')
    ]
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    for site_name, url, container_tag, container_class, title_tag, title_class, link_attr in sites:
        try:
            print(f"Scraping {site_name}: {url}")
            response = requests.get(url, headers=headers, timeout=5)
            print(f"Status code: {response.status_code}")
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            time.sleep(2)  # Delay to avoid rate limiting
            job_elements = soup.find_all(container_tag, class_=container_class, limit=3)
            print(f"Found {len(job_elements)} job elements for {site_name}")
            if not job_elements:
                print(f"HTML snippet for {site_name}: {soup.prettify()[:500]}...")
            for job in job_elements:
                title_elem = job.find(title_tag, class_=title_class) if title_class else job.find(title_tag)
                link_elem = job.find('a', href=True)
                title = title_elem.text.strip() if title_elem else "No title"
                base_url = f"https://www.{site_name.lower()}.com"
                link = link_elem['href'] if link_elem and link_attr in link_elem.attrs else base_url + (job.find('a')['href'] if job.find('a') else "")
                if title != "No title" and link:
                    if not link.startswith('http'):
                        link = base_url + link if not link.startswith('/') else base_url + link
                    jobs.append({"title": title, "link": link, "source": site_name})
        except requests.RequestException as e:
            print(f"Error scraping {site_name}: {e}")
            continue
    print(f"Total jobs collected: {len(jobs)}")
    return jobs[:9]  # Limit to 9 total

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

        resume_text, achievements, _ = parse_resume(resume_path)
        if "Error" in resume_text:
            return render_template("index.html", error=resume_text)

        resume_keywords = extract_keywords(resume_text)
        job_keywords = extract_keywords(job_desc)
        missing_keywords = compare_texts(resume_keywords, job_keywords)
        cover_letter = generate_cover_letter(name, job_title, company, resume_keywords, missing_keywords, achievements)

        save_application(job_title, company, "2025-09-03", "Applied")

        return render_template("results.html", missing=missing_keywords, cover_letter=cover_letter, achievements=achievements)

    return render_template("index.html", error=None)

@app.route("/find_jobs", methods=["GET", "POST"])
def find_jobs():
    jobs = []
    query = ""
    if request.method == "POST":
        query = request.form.get("job_title", "")
        if query:
            jobs = search_jobs(query)
    return render_template("find_jobs.html", jobs=jobs, query=query)

@app.route("/match_resume_jobs", methods=["GET", "POST"])
def match_resume_jobs():
    jobs = []
    if request.method == "POST":
        resume = request.files.get("resume")
        if resume and resume.filename != '':
            resume_path = os.path.join(app.config['UPLOAD_FOLDER'], resume.filename)
            resume.save(resume_path)
            resume_text, _, skills = parse_resume(resume_path)
            if "Error" not in resume_text:
                query = ' '.join(skills) + " job" if skills else "general job"
                jobs = search_jobs(query)
    return render_template("match_resume_jobs.html", jobs=jobs)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
