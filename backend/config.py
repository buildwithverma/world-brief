import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
HOSTED = os.getenv('APP_ENV', 'local') == 'production'
if not HOSTED:
    load_dotenv(ROOT / '.env')
DATABASE_URL = os.getenv('DATABASE_URL', '')
PUBLIC_ORIGIN = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
SUPABASE_URL = os.getenv('SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY = os.getenv('SUPABASE_PUBLISHABLE_KEY', '')
OWNER_USER_ID = os.getenv('OWNER_USER_ID', '')
if HOSTED and not all((DATABASE_URL, PUBLIC_ORIGIN.startswith('https://'), SUPABASE_URL.startswith('https://'), SUPABASE_KEY, OWNER_USER_ID)):
    raise RuntimeError('Hosted mode requires database, HTTPS frontend origin, Supabase Auth, and owner configuration.')
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
MODEL_CACHE = Path(os.getenv('MODEL_CACHE_DIR', str(DATA / 'models')))
GROQ_KEY = os.getenv('GROQ_API_KEY', '')
GROQ_MODEL = os.getenv('GROQ_MODEL', 'openai/gpt-oss-20b')
FACT_KEY = os.getenv('GOOGLE_FACT_CHECK_API_KEY', '')
SEMANTIC = os.getenv('ENABLE_SEMANTIC_CACHE', 'true').lower() == 'true'
REFRESH_SECONDS = max(300, int(os.getenv('NEWS_REFRESH_MINUTES', '30')) * 60)
CACHE_SECONDS = max(60, int(os.getenv('CACHE_MINUTES', '20')) * 60)
TOKEN_BUDGET = int(os.getenv('GROQ_DAILY_TOKEN_BUDGET', '120000'))
DEFAULT_ARTICLE_COUNT = max(1, min(100, int(os.getenv('NEWS_ARTICLE_COUNT', '50'))))

FEEDS = [
    ('bbc-world', 'BBC News', 'World', 'Global', 'https://feeds.bbci.co.uk/news/world/rss.xml'),
    ('aljazeera', 'Al Jazeera', 'World', 'Global', 'https://www.aljazeera.com/xml/rss/all.xml'),
    ('guardian-world', 'The Guardian', 'World', 'Global', 'https://www.theguardian.com/world/rss'),
    ('dw', 'DW', 'World', 'Europe', 'https://rss.dw.com/atom/rss-en-all'),
    ('france24', 'France 24', 'World', 'Global', 'https://www.france24.com/en/rss'),
    ('cna', 'CNA', 'World', 'Asia', 'https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511'),
    ('hindu', 'The Hindu', 'World', 'Global', 'https://www.thehindu.com/news/international/feeder/default.rss'),
    ('rnz', 'RNZ', 'World', 'Pacific', 'https://www.rnz.co.nz/rss/pacific.xml'),
    ('africanews', 'Africanews', 'World', 'Africa', 'https://www.africanews.com/feed/rss'),
    ('guardian-business', 'The Guardian', 'Business', 'Global', 'https://www.theguardian.com/business/rss'),
    ('bbc-tech', 'BBC News', 'Technology', 'Global', 'https://feeds.bbci.co.uk/news/technology/rss.xml'),
    ('verge', 'The Verge', 'Technology', 'Global', 'https://www.theverge.com/rss/index.xml'),
    ('ars', 'Ars Technica', 'Technology', 'Global', 'https://feeds.arstechnica.com/arstechnica/index'),
    ('nasa', 'NASA', 'Science', 'Global', 'https://www.nasa.gov/feed/'),
    ('mit', 'MIT News', 'Science', 'Global', 'https://news.mit.edu/rss/feed'),
    ('carbonbrief', 'Carbon Brief', 'Climate', 'Global', 'https://www.carbonbrief.org/feed/'),
    ('mongabay', 'Mongabay', 'Climate', 'Global', 'https://news.mongabay.com/feed/'),
    ('un', 'UN News', 'World', 'Global', 'https://news.un.org/feed/subscribe/en/news/all/rss.xml'),
]
