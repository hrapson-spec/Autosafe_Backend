# data_requests.md — the agent drives its own substrate

If a hypothesis needs data not yet in the frame, declare it here. The runner/human ingests it (this is
how you drive Stage P — the lake re-ingest). You cannot fabricate data, but you decide what the search
should have access to.

Append a block per request:

```
## <date> — <field/signal name>
Source: <DVSA field | lake table | item-detail text | external table>
Why:    <the hypothesis it unlocks>
Parity: <can it compute identically at train and serve? if not, say so>
Status: REQUESTED          # human/runner sets -> INGESTED / UNAVAILABLE
```

Known already-parsed-but-unused signals you can ask for cheaply: `manufacture_date`/`firstUsedDate`
(→ raw age), `fuel_type`, `engine_size` (all parsed by `dvsa_client.py` and discarded today).

(Empty until the agent runs.)
