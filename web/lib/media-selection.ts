/** Apply local choices to refreshed server data without discarding unsaved edits. */
export function mergeSelection(outlets: {id:string;selected:number|boolean}[], edits: Map<string,boolean>): Set<string> {
 return new Set(outlets.filter(o=>edits.get(o.id) ?? !!o.selected).map(o=>o.id));
}
