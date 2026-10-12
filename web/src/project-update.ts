type Obj = Record<string, any>;

/** A write returns canonical data; only the read endpoint adds the reader. */
export function mergeProjectWrite(current:Obj,written:Obj):Obj {
  const merged={...current,...written};
  if(written.documents){
    merged.documents=Object.fromEntries(Object.entries(written.documents).map(([kind,value])=>{
      const next=value as Obj,previous=current.documents?.[kind];
      const reader=previous?.id===next.id&&previous?.brief_hash===next.brief_hash?previous.reader:undefined;
      return [kind,reader?{...next,reader}:next];
    }));
  }
  return merged;
}
