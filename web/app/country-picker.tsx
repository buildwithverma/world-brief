'use client';
import {Combobox,ComboboxInput,ComboboxContent,ComboboxList,ComboboxItem,ComboboxEmpty} from '@/components/ui/combobox';
import type {Country} from '@/lib/brief-api';
export function CountryPicker({countries,value,onChange,label='Country'}:{countries:Country[];value:string;onChange:(v:string)=>void;label?:string}){
 const selected=countries.find(c=>c.code===value)||null;
 return <Combobox items={countries} value={selected} itemToStringLabel={c=>c.name} isItemEqualToValue={(a,b)=>a.code===b.code} onValueChange={v=>{if(v)onChange(v.code)}}><ComboboxInput aria-label={label} placeholder="Choose your country…" className="country-picker"/><ComboboxContent><ComboboxEmpty>No country found.</ComboboxEmpty><ComboboxList>{(c:Country)=><ComboboxItem key={c.code} value={c}>{c.name}</ComboboxItem>}</ComboboxList></ComboboxContent></Combobox>;
}
