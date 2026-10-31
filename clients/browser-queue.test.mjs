import {test} from 'node:test';
import assert from 'node:assert/strict';
import {BrowserQueue} from './browser-queue.mjs';
test('durable retry keeps IDs and only removes acknowledged events', async () => {
 let data=[];
 const storage={load: async()=>structuredClone(data), save:async x=>{data=structuredClone(x)}};
 let queue=new BrowserQueue(storage);
 const a=await queue.enqueue({url:'x'});
 const b=await queue.enqueue({url:'y'});
 await assert.rejects(queue.flush(async()=>{throw Error('offline')}));
 queue=new BrowserQueue(storage);
 await queue.flush(async sessions=>{
  assert.deepEqual(sessions.map(x=>x.event_id),[a,b]);
  return {data:{acknowledgements:[{event_id:a,status:'inserted'},{event_id:b,status:'retry'}]}};
 });
 assert.equal(data.length,1);
 await queue.flush(async()=>({data:{acknowledgements:[{event_id:b,status:'duplicate'}]}}));
 assert.deepEqual(data,[]);
});
test('enqueue during upload is retained', async()=>{
 let data=[]; const q=new BrowserQueue({load:async()=>structuredClone(data),save:async x=>{data=structuredClone(x)}});
 const id=await q.enqueue({});
 await q.flush(async()=>{await q.enqueue({}); return {data:{acknowledgements:[{event_id:id,status:'inserted'}]}}});
 assert.equal(data.length,1);
});
