# Research Workflow Bookmarklet

Use this bookmarklet to ingest the current browser page into the local Research Workflow evidence store.

## Install

1. Create a new browser bookmark.
2. Name it `Research Ingest`.
3. Paste this entire one-line URL as the bookmark location:

```text
javascript:(()=>{const endpoint="http://127.0.0.1:9082/v1/ingest";const payload={urls:[location.href],fetch:false};fetch(endpoint,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(payload)}).then(async r=>{const text=await r.text();if(!r.ok)throw new Error(text||r.status);alert('Research ingest queued: '+location.href);}).catch(e=>alert('Research ingest failed: '+e.message));})()
```

## Behavior

- Sends the current `location.href` to `http://127.0.0.1:9082/v1/ingest`.
- Uses `fetch: false` by default so ingestion is fast and conservative.
- The local API supports CORS/OPTIONS for this use case.
- If ingestion succeeds, the browser shows a short alert.

## When To Use

- You manually find a primary source during research.
- Search recall is weak and you want to add a source by hand.
- You want OpenClaw/Hermes or another agent to verify claims against a page you selected.
