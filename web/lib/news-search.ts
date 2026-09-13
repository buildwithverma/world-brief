import type {Story} from './brief-api';
const tokenize=(s:string):string[]=>s.toLocaleLowerCase().match(/[\p{L}\p{N}]+/gu)||[];
export function localMatches(stories:Story[],query:string):Story[]{
 const filler=new Set(['news','latest','recent','today','stories','headlines','show','me','the','about','in','of','for','please']);
 const region=query.toLowerCase().match(/\b(?:uttar|madhya|andhra|himachal|arunachal) pradesh\b/)?.[0];
 const aliases=region==='uttar pradesh'?['lucknow','kanpur','noida','agra','varanasi','prayagraj','ghaziabad','gorakhpur','meerut','ayodhya','bareilly','aligarh','mathura','jhansi','saharanpur','moradabad']:[];
 const evidence=(s:Story)=>s.title+' '+(s.sources||[]).map(a=>a.title+' '+(a.excerpt||'')).join(' ');
 if(region)stories=stories.filter(s=>[region,...aliases].some(place=>(' '+tokenize(evidence(s)).join(' ')+' ').includes(' '+place+' ')));
 const terms=[...new Set([...tokenize(query).filter(t=>!filler.has(t)),...aliases])];if(!terms.length)return [...stories].sort((a,b)=>b.published_at-a.published_at);
 const docs=stories.map(s=>tokenize(evidence(s)));
 const avg=docs.reduce((n,d)=>n+d.length,0)/Math.max(1,docs.length)||1;
 const frequency=new Map(terms.map(t=>[t,docs.filter(d=>d.includes(t)).length]));
 return stories.map((story,i)=>({story,score:terms.reduce((score,t)=>{
  const tf=docs[i].filter(w=>w===t).length,df=frequency.get(t)||0;
  return score+(tf?Math.log(1+(docs.length-df+.5)/(df+.5))*tf*2.5/(tf+1.5*(.25+.75*docs[i].length/avg)):0);
 },0)})).filter(x=>x.score>0).sort((a,b)=>b.score-a.score).map(x=>x.story);
}
