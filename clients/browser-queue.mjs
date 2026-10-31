// Use one instance per storage key in the extension service worker.
// storage.load/save must read/write durable storage (e.g. chrome.storage.local).
export class BrowserQueue {
  constructor(storage) { this.storage=storage; this.tail=Promise.resolve(); this.upload=null; }
  mutate(action) {
    const work=this.tail.then(async()=>action(await this.storage.load() || []));
    this.tail=work.catch(()=>{});
    return work;
  }
  enqueue(event) {
    return this.mutate(async items=>{
      const event_id=crypto.randomUUID();
      items.push({...event,event_id});
      await this.storage.save(items);
      return event_id;
    });
  }
  flush(send) {
    if (this.upload) return this.upload;
    this.upload=this.runFlush(send).finally(()=>{this.upload=null});
    return this.upload;
  }
  async runFlush(send) {
    const snapshot=await this.mutate(items=>structuredClone(items));
    if (!snapshot.length) return;
    const response=await send(snapshot);
    const sent=new Set(snapshot.map(x=>x.event_id));
    const confirmed=new Set((response?.data?.acknowledgements || [])
      .filter(a=>sent.has(a.event_id) && ['inserted','duplicate'].includes(a.status))
      .map(a=>a.event_id));
    await this.mutate(items=>this.storage.save(items.filter(x=>!confirmed.has(x.event_id))));
    return response;
  }
}
