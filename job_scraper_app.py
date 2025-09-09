from flask import Flask, request, render_template
import pdfplumber
import nltk
from fuzzywuzzy import fuzz
import sqlite3
from jinja2 import Template
import os
import re
import spacy
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import time
import logging
import csv
import random
from datetime import datetime
import mimetypes

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

nltk.download('punkt')
nltk.download('stopwords')
nlp = spacy.load("en_core_web_sm")

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# File validation constants
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

def validate_file(file):
    if not file or file.filename == '':
        return False
    if file.content_length > MAX_FILE_SIZE:
        return False
    mime = mimetypes.guess_type(file.filename)[0]
    return mime and mime.startswith('application/pdf')

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
        tables = [page.extract_table() for page in pdf.pages if page.extract_table()]
        skills = [cell for table in tables for row in table if row for cell in row if cell] if tables else []
        skills = [s.strip() for s in skills if s and isinstance(s, str)] if skills else []
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

def save_jobs_to_csv(jobs, query):
    filename = f"jobs_{query.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    fieldnames = ['title', 'company', 'link', 'source', 'scraped_at', 'query']
    try:
        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for job in jobs:
                job['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                job['query'] = query
                writer.writerow(job)
        logger.info(f"Saved {len(jobs)} jobs to {filename}")
    except Exception as e:
        logger.error(f"Error saving jobs to CSV: {e}")

async def get_stealth_context(browser):
    context = await browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    """)
    return context

async def scrape_indeed(query, context):
    jobs = []
    site_name = "Indeed"
    logger.info(f"Starting {site_name} scraper for query: {query}")
    try:
        url = f"https://www.indeed.com/jobs?q={query.replace(' ', '+')}&limit=50"
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector('div[data-jk]', timeout=15000)
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        job_elements = soup.find_all('div', {'data-jk': True}, limit=5)
        for job in job_elements:
            title_elem = job.find('h2', class_='jobTitle')
            link_elem = job.find('a', href=True)
            company_elem = job.find('span', class_='companyName')
            title = title_elem.text.strip() if title_elem else "No title"
            company = company_elem.text.strip() if company_elem else "Unknown"
            link = link_elem['href'] if link_elem else f"https://www.indeed.com{link_elem['href']}" if link_elem and not link_elem['href'].startswith('http') else "No link"
            if title != "No title" and link:
                jobs.append({"title": title, "company": company, "link": link, "source": site_name})
        await page.close()
        logger.info(f"{site_name}: Successfully scraped {len(jobs)} jobs")
    except Exception as e:
        logger.error(f"{site_name}: Error during scraping: {e}")
    return jobs

async def scrape_linkedin(query, context):
    jobs = []
    site_name = "LinkedIn"
    logger.info(f"Starting {site_name} scraper for query: {query}")
    try:
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={query.replace(' ', '+')}&start=0"
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector('div.base-card', timeout=15000)
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        job_elements = soup.find_all('div', class_='base-card', limit=5)
        for job in job_elements:
            title_elem = job.find('h3', class_='base-search-card__title')
            link_elem = job.find('a', href=True)
            title = title_elem.text.strip() if title_elem else "No title"
            link = link_elem['href'] if link_elem else "No link"
            if title != "No title" and link:
                jobs.append({"title": title, "company": "Unknown", "link": link, "source": site_name})
        await page.close()
        logger.info(f"{site_name}: Successfully scraped {len(jobs)} jobs")
    except Exception as e:
        logger.error(f"{site_name}: Error during scraping: {e}")
    return jobs

async def scrape_upwork(query, context):
    jobs = []
    site_name = "Upwork"
    logger.info(f"Starting {site_name} scraper for query: {query}")
    try:
        url = f"https://www.upwork.com/nx/search/jobs/?q={query.replace(' ', '+')}&sort=recency"
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector('article[data-test="job-tile"]', timeout=15000)
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        job_elements = soup.find_all('article', {'data-test': 'job-tile'}, limit=5)
        for job in job_elements:
            title_elem = job.find('a', {'data-test': 'job-title-link'})
            link_elem = title_elem if title_elem else job.find('a', href=True)
            title = title_elem.text.strip() if title_elem else "No title"
            link = link_elem['href'] if link_elem else f"https://www.upwork.com{link_elem['href']}" if link_elem and not link_elem['href'].startswith('http') else "No link"
            if title != "No title" and link:
                jobs.append({"title": title, "company": "Unknown", "link": link, "source": site_name})
        await page.close()
        logger.info(f"{site_name}: Successfully scraped {len(jobs)} jobs")
    except Exception as e:
        logger.error(f"{site_name}: Anti-bot or error detected: {e}")
    return jobs

async def scrape_freelancer(query, context):
    jobs = []
    site_name = "Freelancer"
    logger.info(f"Starting {site_name} scraper for query: {query}")
    try:
        url = f"https://www.freelancer.com/job-search/{query.replace(' ', '-')}"
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector('div.JobSearchCard-item', timeout=15000)
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        job_elements = soup.find_all('div', class_='JobSearchCard-item', limit=5)
        for job in job_elements:
            title_elem = job.find('a')
            link_elem = title_elem if title_elem else job.find('a', href=True)
            title = title_elem.text.strip() if title_elem else "No title"
            link = link_elem['href'] if link_elem else f"https://www.freelancer.com{link_elem['href']}" if link_elem and not link_elem['href'].startswith('http') else "No link"
            if title != "No title" and link:
                jobs.append({"title": title, "company": "Unknown", "link": link, "source": site_name})
        await page.close()
        logger.info(f"{site_name}: Successfully scraped {len(jobs)} jobs")
    except Exception as e:
        logger.error(f"{site_name}: Error during scraping: {e}")
    return jobs

async def scrape_fiverr(query, context):
    jobs = []
    site_name = "Fiverr"
    logger.info(f"Starting {site_name} scraper for query: {query}")
    try:
        url = f"https://www.fiverr.com/search/gigs?query={query.replace(' ', '+')}"
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector('div.gig-card', timeout=15000)
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        job_elements = soup.find_all('div', class_='gig-card', limit=5)
        for job in job_elements:
            title_elem = job.find('a', class_='gig-card-title')
            link_elem = title_elem if title_elem else job.find('a', href=True)
            title = title_elem.text.strip() if title_elem else "No title"
            link = link_elem['href'] if link_elem else f"https://www.fiverr.com{link_elem['href']}" if link_elem and not link_elem['href'].startswith('http') else "No link"
            if title != "No title" and link:
                jobs.append({"title": title, "company": "Unknown", "link": link, "source": site_name})
        await page.close()
        logger.info(f"{site_name}: Successfully scraped {len(jobs)} jobs")
    except Exception as e:
        logger.error(f"{site_name}: Error or dynamic loading issue: {e}")
    return jobs

async def scrape_toptal(query, context):
    jobs = []
    site_name = "Toptal"
    logger.info(f"Starting {site_name} scraper for query: {query}")
    try:
        # Note: Toptal's public job search is limited; using talent jobs as proxy
        url = f"https://www.toptal.com/talent/jobs?search={query.replace(' ', '+')}&page=1"
        page = await context.new_page()
        await page.goto(url)
        await page.wait_for_selector('div.job-listing', timeout=15000)
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        job_elements = soup.find_all('div', class_='job-listing', limit=5)
        for job in job_elements:
            title_elem = job.find('a')
            link_elem = title_elem if title_elem else job.find('a', href=True)
            title = title_elem.text.strip() if title_elem else "No title"
            link = link_elem['href'] if link_elem else f"https://www.toptal.com{link_elem['href']}" if link_elem and not link_elem['href'].startswith('http') else "No link"
            if title != "No title" and link:
                jobs.append({"title": title, "company": "Unknown", "link": link, "source": site_name})
        await page.close()
        logger.info(f"{site_name}: Successfully scraped {len(jobs)} jobs")
    except Exception as e:
        logger.error(f"{site_name}: Error or no public listings: {e}")
    return jobs

async def search_jobs(query):
    jobs = []
    logger.info(f"Starting job search for query: '{query}'")
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--disable-extensions',
                '--no-first-run',
                '--disable-default-apps'
            ]
        )
        context = await get_stealth_context(browser)
        scrapers = [scrape_indeed, scrape_linkedin, scrape_upwork, scrape_freelancer, scrape_fiverr, scrape_toptal]
        for scraper in scrapers:
            site_jobs = await scraper(query, context)
            jobs.extend(site_jobs)
            await asyncio.sleep(random.uniform(3, 6))
        await context.close()
        await browser.close()
    logger.info(f"Job search completed. Total jobs found: {len(jobs)}")
    if jobs:
        save_jobs_to_csv(jobs, query)
    return jobs[:15]

def sync_search_jobs(query):
    return asyncio.run(search_jobs(query))

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        resume = request.files.get("resume")
        job_desc = request.form.get("job_desc")
        name = request.form.get("name")
        job_title = request.form.get("job_title")
        company = request.form.get("company")

        if not all([resume, job_desc, name, job_title, company]) or not validate_file(resume):
            return render_template("index.html", error="All fields are required, and resume must be a PDF under 10MB.")

        resume_path = os.path.join(app.config['UPLOAD_FOLDER'], resume.filename)
        resume.save(resume_path)

        resume_text, achievements, skills = parse_resume(resume_path)
        if "Error" in resume_text:
            return render_template("index.html", error=resume_text)

        resume_keywords = extract_keywords(resume_text)
        job_keywords = extract_keywords(job_desc)
        missing_keywords = compare_texts(resume_keywords, job_keywords)
        cover_letter = generate_cover_letter(name, job_title, company, resume_keywords, missing_keywords, achievements)

        save_application(job_title, company, datetime.now().strftime('%Y-%m-%d'), "Applied")

        return render_template("results.html", missing=missing_keywords, cover_letter=cover_letter, achievements=achievements)

    return render_template("index.html", error=None)

@app.route("/find_jobs", methods=["GET", "POST"])
def find_jobs():
    jobs = []
    query = ""
    logger.info("Entering find_jobs route")
    if request.method == "POST":
        query = request.form.get("job_title", "").strip()
        logger.info(f"Received query: '{query}'")
        if query:
            try:
                jobs = sync_search_jobs(query)
                logger.info(f"Jobs retrieved: {len(jobs)}")
            except Exception as e:
                logger.error(f"Error in find_jobs: {e}")
    logger.info("Exiting find_jobs route")
    return render_template("find_jobs.html", jobs=jobs, query=query)

@app.route("/match_resume_jobs", methods=["GET", "POST"])
def match_resume_jobs():
    jobs = []
    logger.info("Entering match_resume_jobs route")
    if request.method == "POST":
        resume = request.files.get("resume")
        if resume and validate_file(resume):
            resume_path = os.path.join(app.config['UPLOAD_FOLDER'], resume.filename)
            resume.save(resume_path)
            resume_text, _, skills = parse_resume(resume_path)
            if "Error" not in resume_text:
                query = ' '.join(skills) + " job" if skills else "general job"
                logger.info(f"Generated query from resume: '{query}'")
                try:
                    jobs = sync_search_jobs(query)
                    logger.info(f"Jobs retrieved: {len(jobs)}")
                except Exception as e:
                    logger.error(f"Error in match_resume_jobs: {e}")
    logger.info("Exiting match_resume_jobs route")
    return render_template("match_resume_jobs.html", jobs=jobs)

if __name__ == "__main__":
    from waitress import serve
    serve(app, host="0.0.0.0", port=8000, threads=1)
