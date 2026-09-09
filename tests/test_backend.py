import json
import time
from email.utils import formatdate
import pytest
from pydantic import ValidationError
from backend.store import Store,digest
from backend.engine import Engine,fallback_intent,coverage,cluster,resolve_window
from backend.catalog import import_catalog,with_metadata,CATALOG_PATH
from backend.media import domain_of
from backend.publisher_feeds import parse_publisher

class NoVector:
    def search(self,q):return []
    def put(self,*args):pass

@pytest.fixture
def store(tmp_path):return Store(tmp_path/'test.sqlite3')

def request(**kwargs):
    return {'query':'','topic':'All','country':'US','period':'day','timezone':'UTC','refresh':False,'previous':None,**kwargs}

def seed(store,count=60):
    outlet={'id':'source','name':'BBC','domain':'bbc.com','major':1}
    with store.db() as db:
        db.execute('INSERT INTO media VALUES(?,?,?,?,?,1,1,1,0)',('US','source','BBC','bbc.com','https://bbc.com'))
    articles=[]
    for i in range(count):
        title=f'Satellite mission {i} measures ocean temperatures'
        articles.append(dict(id=str(i),url=f'https://bbc.com/news/{i}',publisher='BBC',title=title,excerpt=f'Mission {i} carries instruments for researchers.',topic='Science',region='US',published=time.time()-100-i,fetched=time.time(),content_hash=digest(title),source_id='source',domain='bbc.com',major=1))
    store.upsert_articles(articles)
    with store.db() as db:
        db.executemany('INSERT INTO country_articles VALUES(?,?,?,?,?)',[('US',a['id'],'source',1,'Science') for a in articles])
    store.set_meta('last_fetch:US',time.time());store.set_meta('media_initialized:US',1)
    return articles

@pytest.mark.parametrize('query,count',[('',50),('Give me 5 top stories',5),('Show 75 articles',75),('Give me a 2-minute briefing',50),('1000 stories',100)])
def test_counts(query,count):assert fallback_intent(query,request())['count']==count

def test_count_from_ui_and_followup():
    assert fallback_intent('',request(count=32))['count']==32
    assert fallback_intent('only those',request(),{'count':'bad','terms':7,'country':'IN'})['count']==50
    assert fallback_intent('only those',request(),{'country':'IN'})['country']=='US'

def test_api_count_validation():
    from backend.main import Query
    assert Query(country='US').count==50
    for n in (0,101):
        with pytest.raises(ValidationError):Query(country='US',count=n)

def test_catalog_import_and_preserved_selection(store):
    import_catalog(store,'IN')
    rows=with_metadata(store,'IN',store.rows('SELECT * FROM media WHERE country=? ORDER BY rank',('IN',)))
    assert sum(bool(r['selected']) for r in rows)==50
    document=json.loads(CATALOG_PATH.read_text(encoding='utf-8-sig'))
    assert sum(len(r['catalog']['entries']) for r in rows)==sum(len(v) for v in document['categories'].values())==128
    assert rows[0]['catalog']['priority_score']==97
    youtube=[r for r in rows if not r['catalog']['supported']]
    assert len(youtube)>1 and len({r['id'] for r in youtube})==len(youtube)
    assert not any(r['selected'] for r in youtube)
    with store.db() as db:db.execute('UPDATE media SET selected=0 WHERE country=?',('IN',))
    store.set_meta('catalog:IN','force-reimport')
    import_catalog(store,'IN')
    assert not any(r['selected'] for r in store.rows('SELECT * FROM media'))
    assert not store.rows('SELECT * FROM media WHERE country=?',('US',))

def test_publisher_excerpt_and_original_link():
    rss=f'''<rss version="2.0"><channel><item><title>New mission launches</title><link>https://www.bbc.com/news/mission?utm_source=rss</link><pubDate>{formatdate(time.time(),usegmt=True)}</pubDate><description>&lt;p&gt;Researchers will measure ocean temperatures.&lt;/p&gt;</description></item><item><title>Bad external link</title><link>https://unrelated.com/a</link><pubDate>{formatdate(time.time(),usegmt=True)}</pubDate></item></channel></rss>'''
    result=parse_publisher(rss,{'id':'bbc','name':'BBC','domain':'bbc.com','major':1},'US','Science',False)
    assert len(result)==1
    assert result[0]['excerpt']=='Researchers will measure ocean temperatures.'
    assert result[0]['url']=='https://www.bbc.com/news/mission'

def test_outlet_count_not_article_count(store):
    articles=seed(store,2)
    result=coverage(articles)
    assert result['major_count']==result['total_count']==1
    assert result['status']=='outlet_coverage'
    assert len(cluster(articles))==2

def test_domain_aliases():
    assert domain_of('https://www.bbc.co.uk')=='bbc.com'
    assert domain_of('https://economictimes.indiatimes.com')!=domain_of('https://timesofindia.indiatimes.com')
    with pytest.raises(ValueError):domain_of('http://127.0.0.1')

@pytest.mark.asyncio
async def test_count_cache_and_selected_sources(store,monkeypatch):
    seed(store);monkeypatch.setattr('backend.engine.credentials',lambda:('','model'))
    engine=Engine(store,NoVector())
    first=await engine.query(request())
    assert len(first['stories'])==50
    assert first['stories'][0]['summary_kind']=='publisher_excerpt'
    assert first['stories'][0]['sources'][0]['link_kind']=='original'
    cached=await engine.query(request());assert cached['cache']=='exact'
    larger=await engine.query(request(count=60));assert len(larger['stories'])==60 and larger['cache']=='fresh'
    with store.db() as db:db.execute('UPDATE media SET selected=0')
    empty=await engine.query(request());assert not empty['stories']

@pytest.mark.asyncio
async def test_summary_uses_excerpt_rejects_foreign_citations(store,monkeypatch):
    seed(store,2);monkeypatch.setattr('backend.engine.credentials',lambda:('test','model'))
    engine=Engine(store,NoVector())
    async def fake(instruction,payload,max_tokens):
        assert payload[0]['reporting'][0]['excerpt']
        return {'stories':[{'id':payload[0]['id'],'summary':'Valid summary','source_ids':[payload[0]['reporting'][0]['id']]},{'id':payload[1]['id'],'summary':'Invalid summary','source_ids':['invented']}]}
    engine.groq.json=fake
    result=await engine.query(request(count=2))
    assert result['stories'][0]['summary']=='Valid summary'
    assert result['stories'][1]['summary_kind']=='publisher_excerpt'

def test_correction_invalidates_cache(store):
    a=seed(store,1)[0];revision=store.meta('revision')
    store.put_query('q','',{}, {},60,revision)
    assert store.exact('q',revision)
    a['content_hash']='correction';store.upsert_articles([a])
    assert not store.exact('q',store.meta('revision'))

def test_timezone_window():
    scope={'period':'yesterday','timezone':'Asia/Calcutta'}
    start,end=resolve_window(scope);assert end-start==86400

@pytest.mark.asyncio
async def test_source_changes_bypass_collection_cooldown(store,monkeypatch):
    seed(store,1);engine=Engine(store,NoVector());calls=[]
    signature,_=engine.selected_signature('US')
    store.set_meta('fetch_attempt:US',time.time());store.set_meta('collected_sources:US',signature)
    async def fake(*args):calls.append(args);return {'articles':0}
    monkeypatch.setattr('backend.engine.collect_country',fake)
    await engine.collect('US',force=True);assert not calls
    with store.db() as db:db.execute('UPDATE media SET selected=0')
    await engine.collect('US',force=True);assert len(calls)==1

@pytest.mark.parametrize('value',[ 'Brief factual report.', 'word '*90, 'A complete first sentence. '+'More detail '*50])
def test_short_summary_bound(value):
    from backend.shorts import short_summary
    result=short_summary(value)
    assert len(result.split())<=60
    if len(value.split())<=60:assert result==value

def test_publisher_image_validation():
    from backend.shorts import publisher_image,safe_image_url
    assert publisher_image({'media_thumbnail':[{'url':'https://images.example.com/news.jpg'}]})=='https://images.example.com/news.jpg'
    assert publisher_image({'summary':'<p>Reporting</p><img src="https://cdn.example.com/article.jpg">'})=='https://cdn.example.com/article.jpg'
    for url in ('javascript:alert(1)','http://example.com/a.jpg','https://127.0.0.1/a','https://localhost/a','https://user:password@example.com/a','https://example.internal/a','https://example.com:8000/a'):
        assert not safe_image_url(url)

def test_image_change_invalidates_cache(store):
    article=seed(store,1)[0];revision=store.meta('revision')
    article['image_url']='https://images.example.com/photo.jpg';store.upsert_articles([article])
    assert store.meta('revision')!=revision
    assert store.rows('SELECT * FROM article_images')[0]['url']==article['image_url']
