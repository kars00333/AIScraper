"""Configuration constants for the resolver."""

import os

# ── HTTP settings ──
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

PER_DOMAIN_DELAY = 0.5   # seconds between requests to the same domain

# Hosts that need more headroom than the global default. LinkedIn starts
# returning 429 at ~2 req/s sustained and then keeps refusing for a while,
# so a single global delay silently turned ~70% of a full run into
# lookup_unavailable. Measured: 1 req/s serial is accepted cleanly; 1.5s
# leaves margin for the retry traffic riding on top.
DOMAIN_DELAY_OVERRIDES = {
    "linkedin.com": 1.5,
}

# Ceiling for the adaptive back-off applied when a host answers 429.
MAX_DOMAIN_DELAY = 6.0
MAX_RETRIES = 2
REQUEST_TIMEOUT = 8       # seconds per request

# ── Career page path patterns to probe ──
CAREER_PATHS = [
    "/careers",
    "/jobs",
    "/careers/",
    "/jobs/",
    "/company/careers",
    "/en/careers",
    "/about/careers",
    "/career",
    "/join-us",
    "/work-with-us",
    "/open-positions",
]

# ── ATS URL signatures (domain fragments) ──
ATS_SIGNATURES = {
    "workday": ["myworkdayjobs.com", "wd1.myworkday", "wd3.myworkday", "wd5.myworkday"],
    "greenhouse": ["greenhouse.io", "boards.greenhouse.io"],
    "icims": ["icims.com"],
    "neogov": ["neogov.com", "governmentjobs.com"],
    "lever": ["jobs.lever.co", "lever.co"],
    "bamboohr": ["bamboohr.com/careers", "bamboohr.com/jobs"],
    "taleo": ["taleo.net"],
    "smartrecruiters": ["smartrecruiters.com"],
    "avature": ["avature.net"],
    "phenompeople": ["phenompeople.com"],
    "ashby": ["ashbyhq.com"],
    "successfactors": ["successfactors.com", "oraclecloud.com"],
    "applitrack": ["applitrack.com"],
    "usajobs": ["usajobs.gov"],
    "adp": ["workforcenow.adp.com"],
    "ultipro": ["ultipro.com"],
    "paycom": ["paycomonline.net"],
    "paylocity": ["paylocity.com"],
    "jobvite": ["jobvite.com"],
    "recruitee": ["recruitee.com"],
    "workable": ["workable.com"],
    "dayforce": ["dayforcehcm.com"],
    "eightfold": ["eightfold.ai"],
    "applytojob": ["applytojob.com"],
    "jazzhq": ["jazzhq.com", "resumator.com"],
    "breezy": ["breezy.hr"],
    "clearcompany": ["clearcompany.com"],
}



# ── Paths ──
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)))
DEV_SET_PATH = os.path.join(DATA_DIR, "dev-set.csv")
INPUT_3000_PATH = os.path.join(DATA_DIR, "task1-input-3000.csv")
CHECKPOINT_DB_PATH = os.path.join(os.path.dirname(__file__), "data", "checkpoints.db")
PREDICTIONS_PATH = os.path.join(os.path.dirname(__file__), "output", "predictions.csv")
RESULTS_PATH = os.path.join(os.path.dirname(__file__), "output", "results.csv")
