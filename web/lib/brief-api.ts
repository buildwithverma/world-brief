let accessTokenProvider: (()=>Promise<string>)|null=null;
export function setAccessTokenProvider(provider:()=>Promise<string>){accessTokenProvider=provider}
const apiBase=(import.meta.env.VITE_API_URL||" ").trim().replace(/\/$/, "");
export type Country={code:string;name:string};
export type Outlet={id:string;name:string;domain:string;url:string;rank:number;selected:number;major:number;custom:number;catalog?:{priority_score:number;priority_tier:string;as_of:string;supported:boolean;entries:{name:string;category:string;primary_language?:string;language?:string;region?:string;focus?:string;format?:string;type?:string}[]}|null};
export type Media={country:string;country_name:string;outlets:Outlet[];selected_count:number;initialized:boolean;ranking:string};
export type Story={id:string;image_url?:string;image_urls?:string[];summary_limited?:boolean;title:string;summary:string;summary_kind:string;topic:string;country?:string;country_name?:string;local_priority?:boolean;published_at:number;coverage_count:number;saved:boolean;sources:{id:string;url:string;publisher:string;excerpt?:string;link_kind?:string;domain?:string;major?:boolean;title:string;published_at:number}[];verification:{status:string;major_count:number;total_count:number;major_outlets:{id:string;name:string;domain:string}[];checked_at:number;expires_at:number;explanation:string;stale?:boolean}};
export type Brief={backfilled?:boolean;search_window_days?:number|null;summary_pending?:number;summary_failed?:number;stories:Story[];query:string;country:string;country_name:string;generated_at:number;source_updated_at:number;cache:string;stale:boolean;intent:Record<string,unknown>;matched_articles:number;notice:string|null;selected_sources:number;requested_count:number};
export type Status={hosted?:boolean;groq:string;groq_configured:boolean;groq_model:string;key_location:string;semantic_cache:string;country:string|null;country_name:string|null;articles:number;cached_queries:number;refreshing:boolean;last_fetch:number};
export async function api<T>(path:string,options:RequestInit={}):Promise<T>{
 const controller=new AbortController();const timeout=setTimeout(()=>controller.abort('timeout'),70000);
 const signal=options.signal?AbortSignal.any([options.signal,controller.signal]):controller.signal;
 try{const headers=new Headers(options.headers);if(accessTokenProvider){const token=await accessTokenProvider();if(token)headers.set("Authorization",`Bearer ${token}`)}const r=await fetch(`${apiBase}/api/${path}`,{...options,headers,signal});if(!r.ok){const body=await r.json().catch(()=>({})) as {detail?:unknown};throw new Error(typeof body.detail==='string'?body.detail:`Request failed (${r.status}). Try again.`)}return r.json()}
 catch(e){if(controller.signal.aborted)throw new Error('This took too long. Please retry; your cached stories are safe.');if(e instanceof TypeError)throw new Error(apiBase?'The news service is waking up or unavailable. Please retry shortly.':'Cannot reach the local news service. Run start.ps1 and keep its terminal open.');throw e}
 finally{clearTimeout(timeout)}
}
export const post=(body:unknown):RequestInit=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
export function ago(seconds:number){if(!seconds)return 'Not yet updated';const m=Math.max(0,Math.floor((Date.now()/1000-seconds)/60));return m<1?'Just now':m<60?`${m}m ago`:m<1440?`${Math.floor(m/60)}h ago`:`${Math.floor(m/1440)}d ago`}
