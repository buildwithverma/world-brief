import {test} from 'node:test';
import assert from 'node:assert/strict';
import {localMatches} from '../web/lib/news-search.ts';
const stories=[{id:'a',title:'AI investment expands',summary:'',published_at:1},{id:'b',title:'Railway investment expands',summary:'',published_at:2}];
test('typing AI does not match railway substrings',()=>assert.deepEqual(localMatches(stories,'AI').map(s=>s.id),['a']));
test('clearing search restores newest-first results',()=>assert.deepEqual(localMatches(stories,'').map(s=>s.id),['b','a']));
test('multiple query terms prioritize the closer match',()=>assert.equal(localMatches(stories,'AI investment')[0].id,'a'));

test('Uttar Pradesh query excludes Andhra Pradesh even when it contains news',()=>{
 const sample=[{id:'up',title:'Uttar Pradesh exports rise',sources:[],published_at:2},{id:'ap',title:'Andhra Pradesh news',sources:[],published_at:3}];
 assert.deepEqual(localMatches(sample,'uttar pradesh news').map(s=>s.id),['up']);
});
