import asyncio
import json
import re
import time
from collections import Counter
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from .config import CACHE_SECONDS,REFRESH_SECONDS,DEFAULT_ARTICLE_COUNT
from .groq import Groq,credentials
from .media import NAMES,country_name,collect_country
from .store import digest
from .shorts import short_summary

TOPICS=['All','World','Technology','Business','Science','Climate']
STOP=set('the a an is are was were of to in on for with and or as at by from after over this that it its news latest top world today stories give me about what happened biggest only please show tell headlines brief briefing update updates new s happening going around across recent more story ones those instead changed my country local international global two minute minutes five ten'.split())
def words(text):return set(re.findall(r'[a-z0-9]+',text.lower()))-STOP
def normalized(text):return ' '.join(re.findall(r'[a-z0-9]+',text.lower()))

def fallback_intent(query,filters,previous=None):
    scope=dict(filters);q=query.lower();scope.update(count=filters.get('count',DEFAULT_ARTICLE_COUNT),terms=[])
    if previous and re.search(r'\b(only|those|ones|instead)\b',q):
        scope.update({k:v for k,v in previous.items() if k in ('topic','period','count','terms')})
    for topic in TOPICS[1:]:
        if topic.lower() in q or (topic=='Technology' and re.search(r'\b(tech|ai)\b',q)):scope['topic']=topic
    if 'yesterday' in q:scope['period']='yesterday'
    elif 'week' in q:scope['period']='week'
    elif 'today' in q:scope['period']='today'
    numbers=re.search(r'\b(\d+)\s+(?:top\s+)?(?:stories|headlines|news items|articles)\b',q)
    if numbers:scope['count']=min(100,max(1,int(numbers[1])))
    for name,value in [('five',5),('ten',10),('three',3)]:
        if re.search(r'\b'+name+r'\b',q):scope['count']=value
    structural=words(' '.join(TOPICS)+' articles since yesterday week hours hour all one two three four five ten minute tech '+country_name(scope['country']))
    terms=sorted(words(q)-structural-set(re.findall(r'\d+',q)))
    if terms:scope['terms']=terms
    if scope.get('topic') not in TOPICS:scope['topic']='All'
    if scope.get('period') not in ('day','today','yesterday','week'):scope['period']='day'
    if not isinstance(scope.get('count'),int):scope['count']=DEFAULT_ARTICLE_COUNT
    scope['count']=max(1,min(100,scope['count']))
    if not isinstance(scope.get('terms'),list):scope['terms']=[]
    scope['terms']=[str(t).lower()[:60] for t in scope['terms'][:8]]
    return scope

def resolve_window(scope):
    try:zone=ZoneInfo(scope.get('timezone','UTC'))
    except Exception:zone=ZoneInfo('UTC')
    now=datetime.now(zone);midnight=now.replace(hour=0,minute=0,second=0,microsecond=0)
    period=scope.get('period','day')
    if period=='today':start,end=midnight.timestamp(),now.timestamp()
    elif period=='yesterday':start,end=(midnight-timedelta(days=1)).timestamp(),midnight.timestamp()
    else:start,end=now.timestamp()-(7 if period=='week' else 1)*86400,now.timestamp()
    scope['date']=now.date().isoformat();scope['window_bucket']=int(time.time()//CACHE_SECONDS)
    return start,end

def cluster(articles):
    groups=[]
    for a in articles:
        tokens=words(a['title']);match=None
        for g in groups:
            b=g[0];other=words(b['title']);common=tokens&other
            # Require multiple shared content words and a narrow event window.
            if len(common)>=4 and len(common)/max(1,len(tokens|other))>=0.43 and abs(a['published']-b['published'])<36*3600:
                numbers=set(re.findall(r'\b\d+\b',a['title']));other_numbers=set(re.findall(r'\b\d+\b',b['title']))
                if numbers and other_numbers and numbers!=other_numbers:continue
                match=g;break
        if match is None:groups.append([a])
        else:match.append(a)
    return groups

def coverage(group):
    outlets={a['source_id']:a for a in group}
    major=[{'id':k,'name':a['publisher'],'domain':a['domain']} for k,a in outlets.items() if a['major']]
    return {'status':'outlet_coverage','major_count':len(major),'total_count':len(outlets),'major_outlets':major,'checked_at':time.time(),'expires_at':time.time()+CACHE_SECONDS,'explanation':'Distinct outlets in your selected media list reporting a matching story. Matching is based on headline similarity and publication time. Multiple outlets may share syndicated reporting; this count is not a true/false verdict.'}

class Engine:
    def __init__(self,store,vector):
        self.store=store;self.vector=vector;self.groq=Groq(store);self.fetch_locks={};self.refreshing=set()
    def config_signature(self):
        key,model=credentials();return 'country-v3-shorts:'+model+':'+digest(key)[:12]
    async def collect(self,country,force=False,query=''):
        lock=self.fetch_locks.setdefault(country,asyncio.Lock())
        async with lock:
            last=float(self.store.meta('fetch_attempt:'+country,'0'))
            source_sig,_=self.selected_signature(country)
            if not query and self.store.meta('collected_sources:'+country)==source_sig and time.time()-last<(10 if force else REFRESH_SECONDS):return
            self.refreshing.add(country)
            try:
                result=await collect_country(self.store,country,query)
                self.store.set_meta('collected_sources:'+country,source_sig)
                return result
            finally:self.refreshing.discard(country)
    def selected_signature(self,country):
        ids=[r['id'] for r in self.store.rows('SELECT id FROM media WHERE country=? AND selected=1 ORDER BY id',(country,))]
        return digest('|'.join(ids)),ids
    async def query(self,request):
        from .catalog import import_catalog
        country=request['country'];q=request.get('query','').strip()
        import_catalog(self.store,country)
        filters={k:request[k] for k in ('topic','country','period','timezone')}
        filters['count']=request.get('count',DEFAULT_ARTICLE_COUNT)
        config=self.config_signature();source_sig,ids=self.selected_signature(country)
        key_scope=dict(filters);resolve_window(key_scope)
        exact_key=digest(json.dumps({'query':normalized(q),'filters':key_scope,'previous':request.get('previous'),'config':config,'sources':source_sig},sort_keys=True))
        if not request.get('refresh'):
            cached=self.store.exact(exact_key,self.store.meta('revision'))
            if cached:return self.decorate(json.loads(cached['data']),'exact')
        # Cached country data remains useful while new collection is unavailable.
        if request.get('refresh') or not self.store.meta('last_fetch:'+country):
            await self.collect(country,force=bool(request.get('refresh')))
        scope=fallback_intent(q,filters,request.get('previous'))
        if q and credentials()[0]:
            intent=await self.groq.json('Interpret a news request. Return {"topic":"All|World|Technology|Business|Science|Climate","period":"day|today|yesterday|week","count":integer 1-100,"terms":[specific subject keywords]}. Country is explicitly chosen in the interface and must not be changed. Keep the supplied count unless the user explicitly requests a different number of stories. Broad briefings have empty terms. A reading duration is not a story count.',{'query':q,'filters':filters,'previous':request.get('previous')},450)
            if isinstance(intent,dict):
                if intent.get('topic') in TOPICS:scope['topic']=intent['topic']
                if intent.get('period') in ('day','today','yesterday','week'):scope['period']=intent['period']
                if isinstance(intent.get('count'),int):scope['count']=max(1,min(100,intent['count']))
                if isinstance(intent.get('terms'),list):scope['terms']=[str(t).lower()[:60] for t in intent['terms'][:8]]
        if scope['terms']:
            # Search public news only on a cache miss, scoped to the chosen country.
            search_key='search:'+digest(country+' '.join(scope['terms']))
            if time.time()-float(self.store.meta(search_key,'0'))>CACHE_SECONDS:
                await self.collect(country,query=' '.join(scope['terms']))
                self.store.set_meta(search_key,time.time())
        start,end=resolve_window(scope);source_sig,ids=self.selected_signature(country)
        scope.update(sources=source_sig,config=config)
        snapshot=self.store.meta('revision')
        exact_key=digest(json.dumps({'query':normalized(q),'filters':key_scope,'previous':request.get('previous'),'config':config,'sources':source_sig},sort_keys=True))
        if q and not request.get('refresh'):
            for ident in await asyncio.to_thread(self.vector.search,q):
                rows=self.store.rows('SELECT * FROM query_cache WHERE id=? AND expires>? AND revision=?',(ident,time.time(),snapshot))
                if rows and json.loads(rows[0]['scope'])==scope:return self.decorate(json.loads(rows[0]['data']),'semantic')
        articles=self.store.rows('SELECT a.*,c.domestic,c.topic AS country_topic,m.id AS source_id,m.major,m.domain FROM articles a JOIN country_articles c ON c.article_id=a.id JOIN media m ON m.id=c.source_id AND m.country=c.country WHERE c.country=? AND m.selected=1 AND a.published>=? AND a.published<=? ORDER BY a.published DESC LIMIT 1400',(country,start,end))
        images={r['article_id']:r['url'] for r in self.store.rows('SELECT i.* FROM article_images i JOIN country_articles c ON c.article_id=i.article_id WHERE c.country=?',(country,))}
        for a in articles:
            a['topic']=a['country_topic'];a['image_url']=images.get(a['id'],'')
        if scope['topic']!='All':articles=[a for a in articles if a['topic']==scope['topic']]
        if scope['terms']:articles=[a for a in articles if any(t in (a['title']+' '+a['excerpt']).lower() for t in scope['terms'])]
        groups=cluster(articles)
        priorities={r['id']:json.loads(r['metadata'])['priority_score'] for r in self.store.rows('SELECT id,metadata FROM source_catalog WHERE country=?',(country,))}
        def score(g):
            age=max(0,time.time()-g[0]['published'])/3600
            priority=max(priorities.get(a['source_id'],0) for a in g)/25
            return 20*max(a['domestic'] for a in g)+12/(1+age/8)+min(8,len(set(a['source_id'] for a in g)))*2+priority
        selected=[];publishers=Counter();topics=Counter()
        while groups and len(selected)<scope['count']:
            best=max(groups,key=lambda g:score(g)-publishers[g[0]['source_id']]*3-topics[g[0]['topic']]*0.6)
            groups.remove(best);selected.append(best);publishers[best[0]['source_id']]+=1;topics[best[0]['topic']]+=1
        stories=[];missing=[]
        for group in selected:
            group=sorted(group,key=lambda a:bool(a['excerpt']),reverse=True)
            lead=group[0];ident=digest(country+'|'+source_sig+'|'+lead['id']);version=digest('|'.join(sorted(a['content_hash']+a['source_id']+a.get('image_url','') for a in group))+config)
            cached=self.store.rows('SELECT data FROM story_cache WHERE id=? AND version=? AND expires>?',(ident,version,time.time()))
            if cached:story=json.loads(cached[0]['data'])
            else:
                sources=[{'id':a['id'],'url':a['url'],'publisher':a['publisher'],'domain':a['domain'],'major':bool(a['major']),'title':a['title'],'excerpt':a['excerpt'],'link_kind':'news_index' if 'news.google.com/' in a['url'] else 'original','published_at':a['published']} for a in group]
                story={'id':ident,'title':lead['title'],'image_url':next((a['image_url'] for a in group if a.get('image_url')),None),'summary':short_summary(lead['excerpt']) or ('Reported by '+', '.join(dict.fromkeys(a['publisher'] for a in group))+'. Publisher excerpt unavailable; open the reporting for details.'),'summary_kind':'publisher_excerpt' if lead['excerpt'] else 'headline','topic':lead['topic'],'country':country,'country_name':country_name(country),'local_priority':bool(max(a['domestic'] for a in group)),'published_at':max(a['published'] for a in group),'sources':sources,'coverage_count':len(set(a['source_id'] for a in group)),'verification':coverage(group),'saved':False}
                missing.append((story,version))
            stories.append(story)
        if missing and credentials()[0]:
            # Small batches fit the free-tier budget. Excerpts remain available
            # when later batches hit a limit or the bounded summary window ends.
            deadline = time.monotonic() + 20
            for offset in range(0, len(missing), 5):
                batch = missing[offset:offset+5]
                payload=[{'id':s['id'],'reporting':[{'id':a['id'],'publisher':a['publisher'],'title':a['title'],'excerpt':a.get('excerpt','')[:700]} for a in s['sources'][:3]]} for s,v in batch]
                remaining = deadline-time.monotonic()
                if remaining <= 0:break
                try:
                    result=await asyncio.wait_for(self.groq.json('Write a clear news brief of at most 60 words per story, strictly from the supplied headlines and publisher excerpts. Explain what happened and key details only when present. Use fewer words when evidence is thin. Do not repeat the headline or pad with commentary about the article. Never say the article provides context or implications unless those facts are explicitly supplied. Do not invent context, causes, numbers or conclusions. Preserve attribution and uncertainty. Return {"stories":[{"id":string,"summary":string,"source_ids":[string]}]}. Every summary needs a supplied source ID from that story.',payload,1600),timeout=remaining)
                except TimeoutError:break
                if not isinstance(result,dict) or not isinstance(result.get('stories'),list):break
                by_id={s['id']:s for s,v in batch}
                supplied={p['id']:{a['id'] for a in p['reporting']} for p in payload}
                for item in result['stories']:
                    if not isinstance(item,dict) or not isinstance(item.get('id'),str):continue
                    s=by_id.get(item['id']);refs=item.get('source_ids')
                    if s and isinstance(item.get('summary'),str) and item['summary'].strip() and isinstance(refs,list) and refs and all(isinstance(i,str) and i in supplied[item['id']] for i in refs):
                        s.update(summary=short_summary(item['summary']),summary_kind='groq_summary')
        for story,version in missing:
            with self.store.db() as db:db.execute('INSERT OR REPLACE INTO story_cache VALUES(?,?,?,?)',(story['id'],version,json.dumps(story),time.time()+CACHE_SECONDS))
        now=time.time();notice=None
        if not credentials()[0]:notice='Add your Groq key in Settings for AI summaries. You can still search and read source headlines.'
        elif self.groq.status not in ('ready','configured'):notice='Groq '+self.groq.status.replace('_',' ')+'. Source headlines are available; try again later or check your connection in Settings.'
        result={'stories':stories,'query':q,'intent':scope,'country':country,'country_name':country_name(country),'generated_at':now,'source_updated_at':float(self.store.meta('last_fetch:'+country,'0')),'cache':'fresh','briefing':None,'matched_articles':len(articles),'notice':notice,'selected_sources':len(ids),'requested_count':scope['count']}
        if stories and snapshot==self.store.meta('revision'):
            ident=self.store.put_query(exact_key,q,scope,result,CACHE_SECONDS,snapshot)
            if q:await asyncio.to_thread(self.vector.put,ident,q)
        return self.decorate(result,'fresh')
    def decorate(self,result,cache):
        saved={r['id'] for r in self.store.rows('SELECT id FROM saved')}
        for s in result['stories']:s['saved']=s['id'] in saved
        result['cache']=cache;result['stale']=time.time()-result['source_updated_at']>REFRESH_SECONDS*2
        return result
