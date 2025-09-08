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

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

nltk.download('punkt')
nltk.download('stopwords')
nlp = spacy.load("en_core_web_sm")

app = Flask(__name__)

UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Create directories if they don't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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

def save_jobs_to_csv(jobs, filename="jobs.csv"):
    """Save jobs to CSV file"""
    try:
        fieldnames = ['title', 'company', 'link', 'source', 'scraped_at']
        
        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            for job in jobs:
                job['scraped_at'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                writer.writerow(job)
        
        logger.info(f"Saved {len(jobs)} jobs to {filename}")
    except Exception as e:
        logger.error(f"Error saving jobs to CSV: {e}")

async def get_stealth_context(browser):
    """Create a stealth browser context to avoid detection"""
    context = await browser.new_context(
        viewport={'width': 1920, 'height': 1080},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )
    
    # Add stealth scripts
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
        
        window.chrome = {
            runtime: {}
        };
        
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });
    """)
    
    return context

async def scrape_indeed(query, context):
    """Scrape Indeed jobs"""
    jobs = []
    site_name = "Indeed"
    logger.info(f"Starting {site_name} scraper for query: {query}")
    
    try:
        url = f"https://www.indeed.com/jobs?q={query.replace(' ', '+')}&limit=50"
        page = await context.new_page()
        
        logger.debug(f"{site_name}: Navigating to {url}")
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        
        # Wait for job cards to load
        try:
            await page.wait_for_selector('div[data-jk]', timeout=15000)
            logger.debug(f"{site_name}: Job cards loaded successfully")
        except:
            logger.warning(f"{site_name}: Job cards took too long to load, proceeding anyway")
        
        # Random delay to appear more human-like
        await asyncio.sleep(random.uniform(2, 4))
        
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        
        # Updated selectors for Indeed 2025
        job_cards = soup.find_all('div', {'data-jk': True})
        logger.debug(f"{site_name}: Found {len(job_cards)} job cards")
        
        for card in job_cards[:5]:  # Limit to 5 jobs per site
            try:
                # Extract job title
                title_elem = card.find('h2', class_='jobTitle') or card.find('a', {'data-jk': True})
                title = title_elem.get_text(strip=True) if title_elem else "No title"
                
                # Extract company
                company_elem = card.find('span', class_='companyName') or card.find('a', {'data-testid': 'company-name'})
                company = company_elem.get_text(strip=True) if company_elem else "Unknown Company"
                
                # Extract link
                link_elem = card.find('h2', class_='jobTitle')
                if link_elem:
                    link_tag = link_elem.find('a')
                    link = f"https://www.indeed.com{link_tag['href']}" if link_tag and link_tag.get('href') else f"https://www.indeed.com/jobs?q={query}"
                else:
                    link = f"https://www.indeed.com/jobs?q={query}"
                
                if title != "No title":
                    jobs.append({
                        "title": title,
                        "company": company,
                        "link": link,
                        "source": site_name
                    })
                    logger.debug(f"{site_name}: Added job - {title} at {company}")
            
            except Exception as e:
                logger.error(f"{site_name}: Error processing job card: {e}")
        
        await page.close()
        logger.info(f"{site_name}: Successfully scraped {len(jobs)} jobs")
        
    except Exception as e:
        logger.error(f"{site_name}: Critical error during scraping: {e}")
    
    return jobs

async def scrape_linkedin(query, context):
    """Scrape LinkedIn jobs"""
    jobs = []
    site_name = "LinkedIn"
    logger.info(f"Starting {site_name} scraper for query: {query}")
    
    try:
        # Use the guest API endpoint for better success rate
        url = f"https://www.linkedin.com/jobs/search?keywords={query.replace(' ', '%20')}&location=&geoId=&f_TPR=&position=1&pageNum=0"
        page = await context.new_page()
        
        logger.debug(f"{site_name}: Navigating to {url}")
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        
        # Wait for job listings to load
        try:
            await page.wait_for_selector('div.base-card', timeout=15000)
            logger.debug(f"{site_name}: Job listings loaded successfully")
        except:
            logger.warning(f"{site_name}: Job listings took too long to load, proceeding anyway")
        
        # Random delay
        await asyncio.sleep(random.uniform(3, 5))
        
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        
        # Updated selectors for LinkedIn 2025
        job_cards = soup.find_all('div', class_='base-card')
        logger.debug(f"{site_name}: Found {len(job_cards)} job cards")
        
        for card in job_cards[:5]:  # Limit to 5 jobs per site
            try:
                # Extract job title
                title_elem = card.find('h3', class_='base-search-card__title') or card.find('h4', class_='base-search-card__title')
                title = title_elem.get_text(strip=True) if title_elem else "No title"
                
                # Extract company
                company_elem = card.find('h4', class_='base-search-card__subtitle') or card.find('a', class_='hidden-nested-link')
                company = company_elem.get_text(strip=True) if company_elem else "Unknown Company"
                
                # Extract link
                link_elem = card.find('a', class_='base-card__full-link')
                link = link_elem['href'] if link_elem and link_elem.get('href') else f"https://www.linkedin.com/jobs/search?keywords={query}"
                
                if title != "No title":
                    jobs.append({
                        "title": title,
                        "company": company,
                        "link": link,
                        "source": site_name
                    })
                    logger.debug(f"{site_name}: Added job - {title} at {company}")
            
            except Exception as e:
                logger.error(f"{site_name}: Error processing job card: {e}")
        
        await page.close()
        logger.info(f"{site_name}: Successfully scraped {len(jobs)} jobs")
        
    except Exception as e:
        logger.error(f"{site_name}: Critical error during scraping: {e}")
    
    return jobs

async def scrape_upwork(query, context):
    """Scrape Upwork jobs"""
    jobs = []
    site_name = "Upwork"
    logger.info(f"Starting {site_name} scraper for query: {query}")
    
    try:
        url = f"https://www.upwork.com/nx/search/jobs/?q={query.replace(' ', '%20')}&sort=recency"
        page = await context.new_page()
        
        logger.debug(f"{site_name}: Navigating to {url}")
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        
        # Wait for job tiles to load
        try:
            await page.wait_for_selector('article[data-test="job-tile"]', timeout=15000)
            logger.debug(f"{site_name}: Job tiles loaded successfully")
        except:
            logger.warning(f"{site_name}: Job tiles took too long to load, proceeding anyway")
        
        # Random delay
        await asyncio.sleep(random.uniform(2, 4))
        
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        
        # Updated selectors for Upwork 2025
        job_cards = soup.find_all('article', {'data-test': 'job-tile'})
        logger.debug(f"{site_name}: Found {len(job_cards)} job cards")
        
        for card in job_cards[:5]:  # Limit to 5 jobs per site
            try:
                # Extract job title
                title_elem = card.find('a', {'data-test': 'job-title-link'}) or card.find('h4')
                title = title_elem.get_text(strip=True) if title_elem else "No title"
                
                # Extract company (client info)
                company_elem = card.find('span', {'data-test': 'client-name'}) or card.find('div', class_='client-info')
                company = company_elem.get_text(strip=True) if company_elem else "Upwork Client"
                
                # Extract link
                link_elem = card.find('a', {'data-test': 'job-title-link'})
                link = f"https://www.upwork.com{link_elem['href']}" if link_elem and link_elem.get('href') else f"https://www.upwork.com/nx/search/jobs/?q={query}"
                
                if title != "No title":
                    jobs.append({
                        "title": title,
                        "company": company,
                        "link": link,
                        "source": site_name
                    })
                    logger.debug(f"{site_name}: Added job - {title} from {company}")
            
            except Exception as e:
                logger.error(f"{site_name}: Error processing job card: {e}")
        
        await page.close()
        logger.info(f"{site_name}: Successfully scraped {len(jobs)} jobs")
        
    except Exception as e:
        logger.error(f"{site_name}: Critical error during scraping: {e}")
    
    return jobs

async def scrape_freelancer(query, context):
    """Scrape Freelancer jobs"""
    jobs = []
    site_name = "Freelancer"
    logger.info(f"Starting {site_name} scraper for query: {query}")
    
    try:
        url = f"https://www.freelancer.com/job-search/{query.replace(' ', '-')}"
        page = await context.new_page()
        
        logger.debug(f"{site_name}: Navigating to {url}")
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        
        # Wait for job cards to load
        try:
            await page.wait_for_selector('div.JobSearchCard-item', timeout=15000)
            logger.debug(f"{site_name}: Job cards loaded successfully")
        except:
            logger.warning(f"{site_name}: Job cards took too long to load, proceeding anyway")
        
        # Random delay
        await asyncio.sleep(random.uniform(2, 4))
        
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        
        # Updated selectors for Freelancer 2025
        job_cards = soup.find_all('div', class_='JobSearchCard-item')
        logger.debug(f"{site_name}: Found {len(job_cards)} job cards")
        
        for card in job_cards[:5]:  # Limit to 5 jobs per site
            try:
                # Extract job title
                title_elem = card.find('a', class_='JobSearchCard-primary-heading-link') or card.find('h3')
                title = title_elem.get_text(strip=True) if title_elem else "No title"
                
                # Extract company (employer info)
                company_elem = card.find('span', class_='JobSearchCard-primary-heading-days') or card.find('div', class_='employer-info')
                company = company_elem.get_text(strip=True) if company_elem else "Freelancer Client"
                
                # Extract link
                link_elem = card.find('a', class_='JobSearchCard-primary-heading-link')
                link = f"https://www.freelancer.com{link_elem['href']}" if link_elem and link_elem.get('href') else f"https://www.freelancer.com/job-search/{query}"
                
                if title != "No title":
                    jobs.append({
                        "title": title,
                        "company": company,
                        "link": link,
                        "source": site_name
                    })
                    logger.debug(f"{site_name}: Added job - {title} from {company}")
            
            except Exception as e:
                logger.error(f"{site_name}: Error processing job card: {e}")
        
        await page.close()
        logger.info(f"{site_name}: Successfully scraped {len(jobs)} jobs")
        
    except Exception as e:
        logger.error(f"{site_name}: Critical error during scraping: {e}")
    
    return jobs

async def scrape_toptal(query, context):
    """Scrape Toptal jobs"""
    jobs = []
    site_name = "Toptal"
    logger.info(f"Starting {site_name} scraper for query: {query}")
    
    try:
        url = f"https://www.toptal.com/developers/job-board?search={query.replace(' ', '+')}"
        page = await context.new_page()
        
        logger.debug(f"{site_name}: Navigating to {url}")
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        
        # Wait for job listings to load
        try:
            await page.wait_for_selector('div.job-listing', timeout=15000)
            logger.debug(f"{site_name}: Job listings loaded successfully")
        except:
            logger.warning(f"{site_name}: Job listings took too long to load, proceeding anyway")
        
        # Random delay
        await asyncio.sleep(random.uniform(2, 4))
        
        content = await page.content()
        soup = BeautifulSoup(content, 'html.parser')
        
        # Updated selectors for Toptal 2025
        job_cards = soup.find_all('div', class_='job-listing')
        logger.debug(f"{site_name}: Found {len(job_cards)} job cards")
        
        for card in job_cards[:5]:  # Limit to 5 jobs per site
            try:
                # Extract job title
                title_elem = card.find('h3') or card.find('a', class_='job-title')
                title = title_elem.get_text(strip=True) if title_elem else "No title"
                
                # Extract company
                company_elem = card.find('span', class_='company-name') or card.find('div', class_='company')
                company = company_elem.get_text(strip=True) if company_elem else "Toptal Client"
                
                # Extract link
                link_elem = card.find('a')
                link = f"https://www.toptal.com{link_elem['href']}" if link_elem and link_elem.get('href') else f"https://www.toptal.com/developers/job-board?search={query}"
                
                if title != "No title":
                    jobs.append({
                        "title": title,
                        "company": company,
                        "link": link,
                        "source": site_name
                    })
                    logger.debug(f"{site_name}: Added job - {title} at {company}")
            
            except Exception as e:
                logger.error(f"{site_name}: Error processing job card: {e}")
        
        await page.close()
        logger.info(f"{site_name}: Successfully scraped {len(jobs)} jobs")
        
    except Exception as e:
        logger.error(f"{site_name}: Critical error during scraping: {e}")
    
    return jobs

async def search_jobs(query):
    """Main job scraping function with improved error handling and stealth"""
    jobs = []
    logger.info(f"Starting job search for query: '{query}'")
    
    async with async_playwright() as p:
        # Launch browser with stealth options
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
        
        # Define scraper functions
        scrapers = [
            scrape_indeed,
            scrape_linkedin,
            scrape_upwork,
            scrape_freelancer,
            scrape_toptal
        ]
        
        # Run scrapers with individual error handling
        for scraper in scrapers:
            try:
                site_jobs = await scraper(query, context)
                jobs.extend(site_jobs)
                
                # Add delay between sites to avoid rate limiting
                await asyncio.sleep(random.uniform(3, 6))
                
            except Exception as e:
                logger.error(f"Error running scraper {scraper.__name__}: {e}")
                continue
        
        await context.close()
        await browser.close()
    
    logger.info(f"Job search completed. Total jobs found: {len(jobs)}")
    
    # Save jobs to CSV
    if jobs:
        save_jobs_to_csv(jobs)
    
    return jobs[:15]  # Limit to 15 total jobs

def sync_search_jobs(query):
    """Synchronous wrapper for the async search_jobs function"""
    return asyncio.run(search_jobs(query))

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
        
        save_application(job_title, company, "2025-09-07", "Applied")
        
        return render_template("results.html", missing=missing_keywords, cover_letter=cover_letter, achievements=achievements)
    
    return render_template("index.html", error=None)

@app.route("/find_jobs", methods=["GET", "POST"])
def find_jobs():
    jobs = []
    query = ""
    
    logger.info("Entering find_jobs route")
    
    if request.method == "POST":
        query = request.form.get("job_title", "")
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
        if resume and resume.filename != '':
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