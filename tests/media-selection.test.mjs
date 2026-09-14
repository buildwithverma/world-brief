import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mergeSelection} from '../web/lib/media-selection.ts';
test('background discovery preserves deselections and includes newly discovered sources',()=>{
 const outlets=[{id:'a',selected:1},{id:'b',selected:0},{id:'new',selected:1}];
 assert.deepEqual([...mergeSelection(outlets,new Map([['a',false],['b',true]]))],['b','new']);
});
test('fresh country or saved selection uses server preferences',()=>{
 assert.deepEqual([...mergeSelection([{id:'a',selected:0},{id:'b',selected:1}],new Map())],['b']);
});
