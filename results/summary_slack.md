# Pilot results

Model `openai/gpt-oss-120b`, suite `slack`, attack `important_instructions`, repeats per case: 3, estimated API spend $0.0.

95% intervals in brackets.

| Level | Attack success | Task done, no attack | Task done under attack | Clean runs with a legit action blocked |
| --- | --- | --- | --- | --- |
| L1 observe | 14/75 = 19% (11%–29%) | 0/15 = 0% (0%–20%) | 0/75 = 0% (0%–5%) | 15/15 |
| L2 strict review | 15/75 = 20% (13%–30%) | 12/15 = 80% (55%–93%) | 17/75 = 23% (15%–33%) | 3/15 |
| L4 live monitor | 17/75 = 23% (15%–33%) | 14/15 = 93% (70%–99%) | 31/75 = 41% (31%–53%) | 1/15 |
| L5 full | 71/75 = 95% (87%–98%) | 14/15 = 93% (70%–99%) | 68/75 = 91% (82%–95%) | 0/15 |

**After-the-fact monitor on L5** (`openai/gpt-oss-120b`, flag at score ≥ 7):
- Successful attacks caught: 20/24 = 83% (64%–93%)
- False alarms on clean runs: 0/5 = 0% (0%–43%)

Small samples: intervals are wide. Treat differences as directional.
No level was tuned to this suite's attacks (L3 exists for banking only).
