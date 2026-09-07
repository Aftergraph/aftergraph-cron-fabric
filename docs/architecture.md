# Architecture - sensor > state > dedupe > significance > Telegram

No-agent sensors run the cheap check every tick. Empty stdout means
silence - the agent never wakes. Changed output wakes the agent with the
diff; `continuity` lets it dedupe against its own last report. Only
ACTIONABLE and above reach the shared Telegram Ops topic. Heavy analysis
runs only when the signal requires it. Max ~6 parallel gh-heavy agents
(a 10-way fan-out hit HTTP 429).
