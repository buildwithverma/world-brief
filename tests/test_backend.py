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
        return {'stories':[{'id':payload[0]['id'],'summary':'A valid summary with supplied source details','source_ids':[payload[0]['reporting'][0]['id']]},{'id':payload[1]['id'],'summary':'Invalid summary','source_ids':['invented']}]}
    engine.groq.json=fake
    result=await engine.query(request(count=2))
    assert result['stories'][0]['summary']=='A valid summary with supplied source details'
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
    assert len(result.split())<=56
    if len(value.split())<=56:assert result==value

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

@pytest.mark.asyncio
async def test_background_summary_repairs_exact_cache(store,monkeypatch):
    seed(store,1);monkeypatch.setattr('backend.engine.credentials',lambda:('','model'))
    monkeypatch.setattr('backend.summary_queue.credentials',lambda:('test','model'))
    engine=Engine(store,NoVector());first=await engine.query(request(count=1))
    assert first['summary_pending']==1
    async def fake(prompt,payload,tokens):
        return {'stories':[{'id':payload[0]['id'],'summary':'Researchers launched a satellite to study ocean temperatures.','source_ids':[payload[0]['reporting'][0]['id']]}]}
    engine.groq.json=fake
    await engine.summaries.process()
    second=await engine.query(request(count=1))
    assert second['cache']=='exact' and second['summary_pending']==0
    assert second['stories'][0]['summary_kind']=='groq_summary'

@pytest.mark.asyncio
async def test_background_summary_defers_rate_limit(store,monkeypatch):
    seed(store,1);monkeypatch.setattr('backend.engine.credentials',lambda:('','model'))
    monkeypatch.setattr('backend.summary_queue.credentials',lambda:('test','model'))
    engine=Engine(store,NoVector());await engine.query(request(count=1))
    async def limited(*args):engine.groq.status='rate_limited';return None
    engine.groq.json=limited;await engine.summaries.process()
    job=store.rows('SELECT * FROM summary_jobs')[0]
    assert job['status']=='pending' and job['attempts']==0 and job['next_attempt']>time.time()+50

@pytest.mark.asyncio
async def test_background_rejects_foreign_sources(store,monkeypatch):
    seed(store,1);monkeypatch.setattr('backend.engine.credentials',lambda:('','model'))
    monkeypatch.setattr('backend.summary_queue.credentials',lambda:('test','model'))
    engine=Engine(store,NoVector());await engine.query(request(count=1))
    async def bad(prompt,payload,tokens):return {'stories':[{'id':payload[0]['id'],'summary':'An unsupported claim that should never be displayed.','source_ids':['foreign']}]}
    engine.groq.json=bad;await engine.summaries.process()
    assert store.rows('SELECT attempts FROM summary_jobs')[0]['attempts']==1
    assert json.loads(store.rows('SELECT data FROM story_cache')[0]['data'])['summary_kind']=='publisher_excerpt'

@pytest.mark.asyncio
async def test_summary_queue_survives_restart(store,monkeypatch):
    from backend.summary_queue import SummaryQueue
    seed(store,1);monkeypatch.setattr('backend.engine.credentials',lambda:('','model'))
    monkeypatch.setattr('backend.summary_queue.credentials',lambda:('test','model'))
    engine=Engine(store,NoVector());await engine.query(request(count=1))
    restarted=SummaryQueue(store,engine.groq)
    async def fake(prompt,payload,tokens):return {'stories':[{'id':payload[0]['id'],'summary':'The satellite will measure ocean temperatures for researchers.','source_ids':[payload[0]['reporting'][0]['id']]}]}
    engine.groq.json=fake;await restarted.process()
    assert store.rows('SELECT status FROM summary_jobs')[0]['status']=='done'

@pytest.mark.asyncio
async def test_old_summary_job_cannot_replace_new_version(store,monkeypatch):
    seed(store,1);monkeypatch.setattr('backend.engine.credentials',lambda:('','model'))
    monkeypatch.setattr('backend.summary_queue.credentials',lambda:('test','model'))
    engine=Engine(store,NoVector());brief=await engine.query(request(count=1));story=brief['stories'][0]
    async def fake(prompt,payload,tokens):
        newer={**story,'summary':'New corrected evidence','summary_kind':'groq_summary'}
        with store.db() as db:db.execute('UPDATE story_cache SET version=?,data=? WHERE id=?',('newer',json.dumps(newer),story['id']))
        return {'stories':[{'id':story['id'],'summary':'This stale summary must never replace corrected evidence.','source_ids':[story['sources'][0]['id']]}]}
    engine.groq.json=fake;await engine.summaries.process()
    assert json.loads(store.rows('SELECT data FROM story_cache')[0]['data'])['summary']=='New corrected evidence'
    assert engine.summaries.updates([story['id']])['pending']==0


def test_summary_target_requires_detail_when_available():
    from backend.shorts import summary_is_usable,short_summary
    rich=[{'excerpt':'supported fact '*50}]
    assert not summary_is_usable('A short summary with too little detail.',rich)
    assert summary_is_usable('word '*40,rich)
    assert summary_is_usable('word '*56,rich)
    assert not summary_is_usable('word '*57,rich)
    assert summary_is_usable('Only limited headline information is available for this story.',[{'excerpt':''}])
    assert len(short_summary('word '*100).split())==56


@pytest.mark.asyncio
async def test_manual_refresh_retries_failed_but_preserves_cooldown(store,monkeypatch):
    seed(store,1);monkeypatch.setattr('backend.engine.credentials',lambda:('','model'))
    engine=Engine(store,NoVector())
    async def collect(*args,**kwargs):pass
    engine.collect=collect
    await engine.query(request(count=1))
    with store.db() as db:db.execute("UPDATE summary_jobs SET status='failed',attempts=3")
    assert (await engine.query(request(count=1)))['summary_failed']==1
    assert (await engine.query(request(count=1,refresh=True)))['summary_pending']==1
    assert store.rows('SELECT attempts FROM summary_jobs')[0]['attempts']==0
    later=time.time()+3600
    with store.db() as db:db.execute('UPDATE summary_jobs SET next_attempt=?',(later,))
    await engine.query(request(count=1,refresh=True))
    assert store.rows('SELECT next_attempt FROM summary_jobs')[0]['next_attempt']==later

@pytest.mark.asyncio
async def test_expired_summaries_do_not_use_quota_and_can_resume(store,monkeypatch):
    seed(store,1);monkeypatch.setattr('backend.engine.credentials',lambda:('','model'))
    monkeypatch.setattr('backend.summary_queue.credentials',lambda:('test','model'))
    engine=Engine(store,NoVector());brief=await engine.query(request(count=1));calls=[]
    async def fake(*args):calls.append(args);return None
    engine.groq.json=fake
    with store.db() as db:db.execute('UPDATE story_cache SET expires=0')
    assert engine.summaries.updates([brief['stories'][0]['id']])['pending']==0
    await engine.summaries.process();assert not calls
    with store.db() as db:db.execute('UPDATE story_cache SET expires=?',(time.time()+300,))
    engine.summaries.enqueue(brief['stories'])
    await engine.summaries.process();assert len(calls)==1

@pytest.mark.asyncio
async def test_topic_search_keeps_requested_category(store,monkeypatch):
    from backend.media import feed_urls
    seed(store,1);monkeypatch.setattr('backend.engine.credentials',lambda:('','model'))
    engine=Engine(store,NoVector());calls=[]
    async def collect(country,force=False,query='',topic='All'):
        calls.append(topic)
        spec=feed_urls(country,query,topic)
        assert spec[0][0]=='Technology' and 'Technology' in spec[0][2]
        with store.db() as db:db.execute('UPDATE country_articles SET topic=?',(spec[0][0],))
    engine.collect=collect
    result=await engine.query(request(query='satellite',topic='Technology',count=1))
    assert calls==['Technology'] and len(result['stories'])==1
