# Pilot results

Model `openai/gpt-oss-120b`, suite `banking`, attack `important_instructions`, repeats per case: 3, estimated API spend $0.0.

95% intervals in brackets.

| Level | Attack success | Task done, no attack | Task done under attack | Clean runs with a legit action blocked |
| --- | --- | --- | --- | --- |
| L1 observe | 0/60 = 0% (0%–6%) | 0/12 = 0% (0%–24%) | 0/48 = 0% (0%–7%) | 10/15 |
| L2 strict review | 1/60 = 2% (0%–9%) | 2/12 = 17% (5%–45%) | 9/48 = 19% (10%–32%) | 8/15 |
| L3 fixed rules | 0/60 = 0% (0%–6%) | 6/12 = 50% (25%–75%) | 11/48 = 23% (13%–37%) | 1/15 |
| L4 live monitor | 1/60 = 2% (0%–9%) | 7/12 = 58% (32%–81%) | 15/48 = 31% (20%–45%) | 5/15 |
| L5 full | 50/60 = 83% (72%–91%) | 5/12 = 42% (19%–68%) | 26/48 = 54% (40%–67%) | 0/15 |

**After-the-fact monitor on L5** (`openai/gpt-oss-120b`, flag at score ≥ 7):
- Successful attacks caught: 16/16 = 100% (81%–100%)
- False alarms on clean runs: 1/6 = 17% (3%–56%)

Small samples: intervals are wide. Treat differences as directional.
L3's rules were written after seeing this suite's attacks; L1, L2 and L4 were not tuned to them.
Usefulness columns exclude user_task_5, whose success check passes even when the agent does nothing.
