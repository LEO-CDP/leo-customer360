# Ad server

## Flow 
```
ads.loader.js will scan all ad placements in html
        ↓
Fast API Ads Endpoint
        ↓
JSON AD DATA LIKE ads.data.json
        ↓
SOURCE
Google / Affiliate / Internal / Sponsor
        ↓
CREATIVE
Native / Banner / Video / Product
        ↓
RENDERER
NativeRenderer / GPTRenderer / IframeRenderer / VideoRenderer
        ↓
TRACKING
Impression / Viewability / Click / Conversion
```

## schema

```
JSON Schema here 
```

