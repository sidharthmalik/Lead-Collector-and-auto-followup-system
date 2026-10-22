"""Bridge between the web UI and your existing scrapper / lead_scorer / email_sender."""
import asyncio
import contextlib
import glob
import os
import threading
import traceback
 
import pandas as pd
 
from scrapper import LeadScraper
from lead_scorer import LeadScoringModel
from email_sender import ColdEmailCampaign
from main import export_scored_leads  # safe: main.py only runs under __main__
 
REQUIRED_COLS = ["source", "business_name", "email", "phone",
                 "address", "website", "rating", "category"]
SCRAPERS = {
    "google_maps": "scrape_google_maps",
    "justdial": "scrape_justdial",
    "indiamart": "scrape_indiamart",
}
 
 
class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = "idle"        # idle | running | done | error
        self.kind = ""              # scrape | campaign
        self.lines = []
        self.scored = None          # latest scored DataFrame
 
    def log(self, text):
        for ln in str(text).splitlines():
            if ln.strip():
                self.lines.append(ln.rstrip())
 
 
job = Job()
 
 
class _Tee:
    """Captures print() output from your modules so the UI can show it live."""
    def write(self, s):
        if s:
            job.log(s)
        return len(s or "")
 
    def flush(self):
        pass
 
 
def start(kind, fn, *args):
    if not job.lock.acquire(blocking=False):
        raise RuntimeError("A job is already running. Wait for it to finish.")
    job.status, job.kind, job.lines = "running", kind, []
 
    def runner():
        result = "done"
        try:
            with contextlib.redirect_stdout(_Tee()):
                fn(*args)
        except Exception:
            job.log(traceback.format_exc())
            result = "error"
        job.lock.release()
        job.status = result
 
    threading.Thread(target=runner, daemon=True).start()
 
 
# ── Step 1-3: scrape → enrich → score → export ───────────────
def run_scrape(cfg):
    asyncio.run(_scrape(cfg))
 
 
async def _scrape(cfg):
    query, loc, n = cfg["query"], cfg["location"], cfg["max_results"]
    print("█" * 55)
    print("  STEP 1 — SCRAPING LEADS")
    print("█" * 55)
    scraper = LeadScraper(headless=not cfg["show_browser"])
 
    for key in cfg["sources"]:
        try:
            await getattr(scraper, SCRAPERS[key])(query, loc, n)
        except Exception as e:      # one failing site shouldn't lose the others
            print(f"  ✗ {key} failed: {e}")
 
    if not scraper.leads:
        print("\n✗ No leads scraped. Check your settings and try again.")
        return
 
    hunter = os.getenv("HUNTER_API_KEY", "")
    if hunter:
        print("\n  STEP 2 — EMAIL ENRICHMENT")
        await scraper.enrich_leads_with_emails(hunter)
 
    print("\n  STEP 3 — ML LEAD SCORING")
    df = pd.DataFrame(scraper.leads)
    for c in REQUIRED_COLS:
        if c not in df.columns:
            df[c] = "N/A"
    model = LeadScoringModel()
    model.train(df)
    scored = model.score(df)
    export_scored_leads(scored, f"{query.replace(' ', '_')}_{loc}")
    job.scored = scored
    print("\n✅ Done. Open the Leads tab.")
 
 
def latest_scored():
    """In-memory result from this session, else newest *_SCORED.csv on the Desktop."""
    if job.scored is not None:
        return job.scored
    files = glob.glob(os.path.expanduser("~/Desktop/*_SCORED.csv"))
    if not files:
        return None
    return pd.read_csv(max(files, key=os.path.getmtime)).fillna("N/A")
 
 
# ── Step 4: cold email campaign ──────────────────────────────
def run_campaign(cfg):
    df = latest_scored()
    if df is None:
        print("✗ No scored leads yet. Run the pipeline first.")
        return
    dry = cfg["dry_run"]
    key = os.getenv("ANTHROPIC_API_KEY", "")
    gmail = os.getenv("GMAIL_ADDRESS", "")
    pw = os.getenv("GMAIL_APP_PASS", "")
    if not dry and not (key and gmail and pw):
        print("✗ Live send needs ANTHROPIC_API_KEY, GMAIL_ADDRESS and GMAIL_APP_PASS in your .env")
        return
    if dry and not key:
        print("ℹ No ANTHROPIC_API_KEY set: previews use the fallback template.")
 
    ColdEmailCampaign(
        anthropic_api_key=key or "missing",
        gmail_address=gmail,
        gmail_app_password=pw,
        your_name=cfg["name"],
        your_company=cfg["company"],
        your_service=cfg["service"],
        your_website=cfg["website"],
    ).run(
        scored_df=df,
        min_grade=cfg["min_grade"],
        dry_run=dry,
        delay_seconds=(45, 90),
        daily_limit=cfg["daily_limit"],
    )