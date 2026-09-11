import asyncio
import contextlib
import json
import time
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import FastAPI,HTTPException,Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel,Field,SecretStr
from dotenv import set_key
from .config import ROOT,REFRESH_SECONDS,TOKEN_BUDGET,DEFAULT_ARTICLE_COUNT
from .catalog import import_catalog,with_metadata
from .engine import Engine
from .groq import credentials
from .media import COUNTRIES,NAMES,MAJOR_DOMAINS,domain_of,country_name
from .store import Store,digest
from .vector import VectorCache

store=Store();vector=VectorCache();engine=Engine(store,vector)
@asynccontextmanager
async def lifespan(app):
    async def maintain():
        await asyncio.to_thread(vector.initialize)
        while True:
            country=store.meta('country')
            if country in NAMES:
                try:await engine.collect(country)
                except Exception:pass
            await asyncio.to_thread(vector.prune,{r['id'] for r in store.rows('SELECT id FROM query_cache WHERE expires>? AND revision=?',(time.time(),store.meta('revision')))})
            await asyncio.sleep(REFRESH_SECONDS)
    task=asyncio.create_task(maintain())
    summary_task=asyncio.create_task(engine.summaries.run())
    yield
    task.cancel()
    summary_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):await summary_task
    with contextlib.suppress(asyncio.CancelledError):await task
    if vector.client:vector.client.close()
app=FastAPI(title='World Brief',lifespan=lifespan)
ORIGINS=['http://localhost:3000','http://127.0.0.1:3000','http://127.0.0.1:8000','http://localhost:8000']
app.add_middleware(CORSMiddleware,allow_origins=ORIGINS,allow_methods=['GET','POST','DELETE'],allow_headers=['Content-Type'])
@app.middleware('http')
async def local_only(request:Request,call_next):
    origin=request.headers.get('origin')
    if (origin and origin not in ORIGINS) or request.url.hostname not in ('127.0.0.1','localhost','testserver'):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Local app access only'},status_code=403)
    return await call_next(request)
def valid_country(code):
    code=code.upper()
    if code not in NAMES:raise HTTPException(422,'Choose a valid country.')
    return code
class Query(BaseModel):
    count:int=Field(default=DEFAULT_ARTICLE_COUNT,ge=1,le=100)
    query:str=Field(default='',max_length=1000)
    topic:Literal['All','World','Technology','Business','Science','Climate']='All'
    country:str=Field(min_length=2,max_length=2)
    period:Literal['day','today','yesterday','week']='day'
    timezone:str=Field(default='UTC',max_length=100)
    refresh:bool=False
    previous:dict|None=None
class Preferences(BaseModel):country:str=Field(min_length=2,max_length=2)
class MediaSelection(BaseModel):country:str;ids:list[str]=Field(max_length=500)
class CustomMedia(BaseModel):country:str;website:str=Field(max_length=250);name:str=Field(default='',max_length=100)
class GroqSettings(BaseModel):key:SecretStr

@app.get('/api/countries')
def countries():return COUNTRIES
@app.get('/api/preferences')
def preferences():return {'country':store.meta('country') or None,'article_count':DEFAULT_ARTICLE_COUNT}
@app.post('/api/preferences')
def set_preferences(body:Preferences):
    code=valid_country(body.country);store.set_meta('country',code)
    return {'country':code,'country_name':country_name(code)}
@app.get('/api/status')
def status(country:str=''):
    code=valid_country(country) if country else store.meta('country')
    key,model=credentials()
    return {'groq':engine.groq.status if key else 'missing_key','groq_configured':bool(key),'groq_model':model,'key_location':str(ROOT/'.env'),'semantic_cache':vector.status,'country':code or None,'country_name':country_name(code) if code else None,'articles':store.rows('SELECT COUNT(*) AS n FROM country_articles WHERE country=?',(code,))[0]['n'],'cached_queries':store.rows('SELECT COUNT(*) AS n FROM query_cache')[0]['n'],'refreshing':code in engine.refreshing,'last_fetch':float(store.meta('last_fetch:'+code,'0')),'daily_token_budget':TOKEN_BUDGET}
@app.post('/api/settings/groq')
async def save_groq(body:GroqSettings):
    key=body.key.get_secret_value().strip()
    if len(key)<15 or len(key)>300 or any(c.isspace() for c in key):raise HTTPException(422,'Enter a complete Groq API key with no spaces.')
    set_key(str(ROOT/'.env'),'GROQ_API_KEY',key,quote_mode='always')
    engine.groq.blocked_until=0;engine.groq.minute=[]
    return {'saved':True,'status':await engine.groq.test()}
@app.post('/api/settings/groq/test')
async def test_groq():return {'status':await engine.groq.test()}
@app.get('/api/media')
def media(country:str):
    code=valid_country(country)
    import_catalog(store,code)
    outlets=store.rows('SELECT * FROM media WHERE country=? ORDER BY rank,name',(code,))
    return {'country':code,'country_name':country_name(code),'outlets':with_metadata(store,code,outlets),'selected_count':sum(bool(x['selected']) for x in outlets),'initialized':bool(store.meta('media_initialized:'+code)),'ranking':('India sources are ordered by your JSON priority scores. Scores are heuristic priorities, not truth ratings. Existing selections are preserved; Select top 50 applies the ranked list.' if code=='IN' else 'Top 50 by recent indexed coverage in this country edition. This is not an audience-size ranking.')}
@app.post('/api/media/select')
def select_media(body:MediaSelection):
    code=valid_country(body.country);known={r['id'] for r in store.rows('SELECT id FROM media WHERE country=?',(code,))}
    if any(i not in known for i in body.ids):raise HTTPException(422,'One of these outlets is not in this country’s source list.')
    unsupported={r['id'] for r in store.rows('SELECT id,metadata FROM source_catalog WHERE country=?',(code,)) if not json.loads(r['metadata'])['supported']}
    if unsupported.intersection(body.ids):raise HTTPException(422,'YouTube-only channels are listed for reference; video ingestion is not supported.')
    with store.db() as db:
        db.execute('UPDATE media SET selected=0 WHERE country=?',(code,))
        db.executemany('UPDATE media SET selected=1 WHERE country=? AND id=?',[(code,i) for i in set(body.ids)])
    return media(code)
@app.post('/api/media/add')
def add_media(body:CustomMedia):
    code=valid_country(body.country)
    try:domain=domain_of(body.website)
    except ValueError as e:raise HTTPException(422,str(e))
    ident=digest(domain)[:24]
    with store.db() as db:
        db.execute('INSERT INTO media VALUES(?,?,?,?,?,10000,1,?,1) ON CONFLICT(country,id) DO UPDATE SET selected=1,custom=1',(code,ident,body.name.strip() or domain,domain,'https://'+domain,int(domain in MAJOR_DOMAINS)))
    return media(code)
@app.post('/api/query')
async def query(body:Query,request:Request):
    data=body.model_dump();data['country']=valid_country(data['country'])
    task=asyncio.create_task(engine.query(data))
    deadline=time.monotonic()+65
    try:
        while not task.done():
            if await request.is_disconnected():task.cancel();raise HTTPException(499,'Request cancelled')
            if time.monotonic()>deadline:task.cancel();raise HTTPException(504,'Collection took too long. Please retry; cached articles are kept.')
            await asyncio.wait({task},timeout=0.25)
        return await task
    finally:
        if not task.done():task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            if task.cancelled() or not task.done():await task
@app.get('/api/saved')
def saved():
    stories=[json.loads(r['data']) for r in store.rows('SELECT * FROM saved ORDER BY created DESC')]
    for s in stories:
        s['saved']=True
        if s.get('verification',{}).get('status')!='outlet_coverage':
            sources=s.get('sources',[]);identities={}
            for source in sources:
                try:d=domain_of(source.get('domain') or source['url'])
                except ValueError:continue
                if d!='news.google.com':identities[d]=source
            major=[{'id':digest(d)[:24],'name':v['publisher'],'domain':d} for d,v in identities.items() if d in MAJOR_DOMAINS]
            s['verification']={'status':'outlet_coverage','major_count':len(major),'total_count':len(identities),'major_outlets':major,'checked_at':0,'expires_at':0,'explanation':'Coverage from this saved snapshot. Refresh the country briefing for current reporting.'}
        s['verification']['stale']=s['verification'].get('expires_at',0)<time.time()
    return stories
@app.post('/api/saved/{ident}')
def save(ident:str):
    rows=store.rows('SELECT data FROM story_cache WHERE id=?',(ident,))
    if not rows:raise HTTPException(404,'Story is not available')
    with store.db() as db:db.execute('INSERT OR REPLACE INTO saved VALUES(?,?,?)',(ident,rows[0]['data'],time.time()))
    return {'saved':True}
@app.delete('/api/saved/{ident}')
def unsave(ident:str):
    with store.db() as db:db.execute('DELETE FROM saved WHERE id=?',(ident,))
    return {'saved':False}

class BriefingUpdates(BaseModel):
    ids:list[str]=Field(max_length=100)

@app.post('/api/briefing-updates')
def briefing_updates(body:BriefingUpdates):
    return engine.summaries.updates(body.ids)
