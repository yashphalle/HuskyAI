"""
Challenge system — REST endpoints + seed data.

GET  /challenges              -> list active challenges
GET  /challenges/{id}         -> single challenge detail
POST /challenges/{id}/start   -> start / resume a session  (returns session info)
GET  /challenges/{id}/progress -> user progress across all sessions
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import decode_token
from database import (
    AsyncSessionLocal,
    Challenge,
    Classroom,
    ClassroomChallenge,
    ClassroomMembership,
    EvalResult,
    InstructorTestEnrollment,
    User,
    UserChallengeSession,
)

log = logging.getLogger("challenges")

router = APIRouter(prefix="/challenges", tags=["challenges"])

# ---------------------------------------------------------------------------
# Seed data — 4 demo challenges aligned with "Collective AI over Distance"
# Each challenge has 3-4 sessions with escalating complexity.
# sessions_data is a list indexed by session_number (1-based).
# ---------------------------------------------------------------------------

SEED_CHALLENGES = [
    {
        "title": "Debug a Failing Web App",
        "description": (
            "A production web application is broken. Users report login failures, "
            "slow page loads, and occasional 500 errors. Your job is to diagnose "
            "the root causes, propose fixes, and document your reasoning — using AI "
            "as a collaborative problem-solving partner."
        ),
        "category": "Technical",
        "difficulty": "Beginner",
        "week": 1,
        "total_sessions": 3,
        "sessions_data": [
            {   # session 1
                "title": "Reproduce & Triage",
                "goal": "Identify the most likely root causes from symptoms alone.",
                "brief": (
                    "You've just joined an on-call rotation and received three user reports:\n"
                    "1. 'Login gives me a 500 error about 30% of the time.'\n"
                    "2. 'The dashboard takes 8-12 seconds to load.'\n"
                    "3. 'I get logged out randomly mid-session.'\n\n"
                    "You have no access to logs yet — only the symptoms above and the knowledge "
                    "that this is a Node.js + PostgreSQL app deployed on a single VM."
                ),
                "seed_question": (
                    "Based on these three symptoms alone, what are the most likely root causes? "
                    "Walk me through your diagnostic reasoning."
                ),
                "system_prompt_extra": (
                    "The user is triaging a broken web app. Help them think systematically "
                    "about root causes from symptoms. Encourage hypothesis formation and "
                    "prioritisation. Do NOT just list all possible bugs — guide them to reason "
                    "from evidence to hypotheses."
                ),
            },
            {   # session 2
                "title": "Reproduce with Logs",
                "goal": "Interpret real log data to confirm or reject hypotheses.",
                "brief": (
                    "You now have access to the last 200 lines of the app log. "
                    "Notable entries:\n"
                    "  [ERROR] Connection pool exhausted (max=10) — seen 47 times in 1 hr\n"
                    "  [WARN]  JWT secret is 'changeme' — hardcoded in config.js\n"
                    "  [ERROR] Query timeout after 30000ms on SELECT * FROM events\n"
                    "  [INFO]  Sessions stored in-memory (not Redis)\n\n"
                    "Previous session: you hypothesised connection pooling and auth issues."
                ),
                "seed_question": (
                    "Given these log entries, which of your previous hypotheses are confirmed? "
                    "Which new issues did you find? What would you fix first and why?"
                ),
                "system_prompt_extra": (
                    "Help the user connect log evidence to their earlier hypotheses. "
                    "Encourage them to prioritise fixes by impact and effort. "
                    "Ask follow-up questions if their reasoning jumps to solutions without evidence."
                ),
            },
            {   # session 3
                "title": "Write the Fix Plan",
                "goal": "Produce a structured remediation plan with trade-offs.",
                "brief": (
                    "You've confirmed three bugs: (1) DB connection pool too small, "
                    "(2) JWT secret is hardcoded/weak, (3) SELECT * on a large table with no index.\n\n"
                    "Your team lead asks for a written fix plan you can hand off to any engineer."
                ),
                "seed_question": (
                    "Write a short but complete remediation plan covering all three issues. "
                    "For each fix: describe the change, the risk if left unfixed, and any "
                    "trade-offs or rollout considerations."
                ),
                "system_prompt_extra": (
                    "Help the user write a clear, structured fix plan. Encourage specificity "
                    "(exact config values, SQL index syntax, secret rotation steps). "
                    "Ask about rollout risk if they skip it."
                ),
            },
        ],
    },
    {
        "title": "Design a Public Awareness Campaign",
        "description": (
            "A non-profit wants to reduce plastic waste in a mid-sized city. "
            "You'll use AI to brainstorm, critique, and refine a multi-channel "
            "public awareness campaign — from audience research to message testing."
        ),
        "category": "Creative & Strategy",
        "difficulty": "Intermediate",
        "week": 2,
        "total_sessions": 4,
        "sessions_data": [
            {   # session 1
                "title": "Audience Mapping",
                "goal": "Define and segment your target audience with precision.",
                "brief": (
                    "The non-profit has a $20 k budget and wants to reduce single-use "
                    "plastic by 15% in downtown Greenfield (pop. 80,000) within 6 months. "
                    "They have no existing data on behaviours or attitudes."
                ),
                "seed_question": (
                    "Who are the most important audience segments to reach, and why? "
                    "How would you find out what actually drives their plastic use behaviour?"
                ),
                "system_prompt_extra": (
                    "Guide the user to think about behavioural segmentation, not just demographics. "
                    "Push them to ground claims in evidence or admitted assumptions. "
                    "Ask: 'How do you know that?' when they assert audience motivations."
                ),
            },
            {   # session 2
                "title": "Message & Channel Strategy",
                "goal": "Match messages to channels and audiences.",
                "brief": (
                    "Based on session 1, you identified two primary segments: "
                    "young professionals (25-35) who care about convenience, and "
                    "parents (30-45) who respond to cost savings and kids' health.\n\n"
                    "You have: social media, transit ads, local radio, school partnerships, "
                    "and a street event budget."
                ),
                "seed_question": (
                    "Propose a message + channel combination for each segment. "
                    "Justify why each channel fits the audience and how the messages differ."
                ),
                "system_prompt_extra": (
                    "Help the user think about message-audience fit and channel reach vs. cost. "
                    "Challenge vague messages ('raise awareness') — ask for specific calls to action."
                ),
            },
            {   # session 3
                "title": "Creative Brief",
                "goal": "Write a brief tight enough that a designer could execute it.",
                "brief": (
                    "Your strategy is approved. Now write a creative brief for the social "
                    "media arm targeting young professionals. Budget: $5 k for 4 posts + "
                    "2 short videos."
                ),
                "seed_question": (
                    "Write the creative brief. Include: objective, audience insight, key message, "
                    "tone of voice, mandatory elements, and success metrics."
                ),
                "system_prompt_extra": (
                    "Act as a creative director reviewing the brief. Push for specificity "
                    "in tone ('witty but not sarcastic'), concrete metrics (not just 'engagement'), "
                    "and a single sharp key message."
                ),
            },
            {   # session 4
                "title": "Measurement & Iteration Plan",
                "goal": "Design a feedback loop to improve the campaign mid-flight.",
                "brief": (
                    "The campaign launches next week. Your funder asks: "
                    "'How will you know if it's working after 30 days, and what will you change?'"
                ),
                "seed_question": (
                    "Design a 30-day check-in plan. What metrics matter, how will you collect them, "
                    "and what would trigger a pivot vs. stay-the-course decision?"
                ),
                "system_prompt_extra": (
                    "Help the user think about leading vs. lagging indicators. "
                    "Push them to define decision thresholds upfront, not after the data arrives."
                ),
            },
        ],
    },
    {
        "title": "Analyze Transit Data for a Policy Brief",
        "description": (
            "You have raw bus ridership and delay data for a city. Use AI to explore, "
            "interpret, and communicate findings to non-technical city councillors "
            "who must decide whether to fund a new express line."
        ),
        "category": "Data & Analysis",
        "difficulty": "Intermediate",
        "week": 3,
        "total_sessions": 3,
        "sessions_data": [
            {   # session 1
                "title": "Data Exploration",
                "goal": "Identify the key patterns and anomalies in the dataset.",
                "brief": (
                    "Dataset summary (you don't have the raw file, but here's the schema):\n"
                    "- route_id, date, hour, boarding_count, alighting_count, delay_minutes\n"
                    "- 18 months of data, 12 routes, hourly granularity\n"
                    "- Known issue: routes 7 and 11 had a 3-month service reduction starting month 8\n\n"
                    "A councillor asks: 'Is ridership growing or declining overall?'"
                ),
                "seed_question": (
                    "Before answering the councillor, what questions would you ask about the data, "
                    "and what analyses would you run first? Walk me through your exploratory approach."
                ),
                "system_prompt_extra": (
                    "Guide the user to think about data quality issues, confounders (the service reduction), "
                    "and appropriate aggregations before jumping to conclusions. "
                    "Prompt them to consider seasonality and the routes 7/11 anomaly."
                ),
            },
            {   # session 2
                "title": "Insight Synthesis",
                "goal": "Turn analysis into 3-5 actionable findings.",
                "brief": (
                    "After exploration you found:\n"
                    "- Overall ridership UP 12% YoY (excluding routes 7 & 11)\n"
                    "- Routes 7 & 11 lost 34% ridership during reduction, recovered only 60% after restoration\n"
                    "- Route 3 (downtown express) has 22-min avg delay in PM peak — highest of all routes\n"
                    "- Routes 1, 2, 5 are at 90%+ capacity during AM peak\n"
                    "- Weekend ridership growing 3x faster than weekday"
                ),
                "seed_question": (
                    "Synthesise these findings into 3-5 key insights for the councillors. "
                    "Each insight should have: what it means, why it matters, and what it implies for the express line decision."
                ),
                "system_prompt_extra": (
                    "Help the user write insights that go beyond restating data — each should include "
                    "an implication or recommendation. Challenge hedging language. "
                    "Ask: 'What does this actually mean for the express line question?'"
                ),
            },
            {   # session 3
                "title": "Policy Brief Draft",
                "goal": "Write a 1-page brief a non-technical councillor can act on.",
                "brief": (
                    "The council meeting is tomorrow. You need a 1-page brief (≈400 words) "
                    "recommending for or against the express line, backed by your data findings."
                ),
                "seed_question": (
                    "Draft the policy brief. It should include: executive summary (2-3 sentences), "
                    "key findings (bullets), recommendation with rationale, and one risk/caveat."
                ),
                "system_prompt_extra": (
                    "Act as a policy editor. Push for plain language, active voice, and a clear "
                    "recommendation — not 'it depends'. Ensure the brief would make sense to "
                    "someone who hasn't seen the data."
                ),
            },
        ],
    },
    {
        "title": "Plan a SaaS Product Feature",
        "description": (
            "You're a product manager at a B2B SaaS startup. A major enterprise client "
            "is requesting a bulk-export feature. Use AI to scope, prioritise, and "
            "spec the feature — balancing customer needs, engineering constraints, and "
            "business strategy."
        ),
        "category": "Product & Business",
        "difficulty": "Advanced",
        "week": 4,
        "total_sessions": 3,
        "sessions_data": [
            {   # session 1
                "title": "Requirements Discovery",
                "goal": "Separate real requirements from assumed ones.",
                "brief": (
                    "A client's VP of Sales emailed: 'We need to export all our data — "
                    "deals, contacts, activity logs — as CSV or Excel. Our compliance team "
                    "requires this by Q2 or we can't renew.'\n\n"
                    "Your engineering lead says: 'That's at least 6 weeks of work.'"
                ),
                "seed_question": (
                    "Before writing a single line of spec, what questions do you ask the client, "
                    "and what questions do you ask your engineers? "
                    "What assumptions are buried in the request?"
                ),
                "system_prompt_extra": (
                    "Help the user surface hidden assumptions (what does 'all data' mean? "
                    "what's the compliance requirement exactly?). Encourage them to distinguish "
                    "stated wants vs. underlying needs. Don't let them jump to solution space."
                ),
            },
            {   # session 2
                "title": "Scope & Prioritisation",
                "goal": "Define an MVP scope that satisfies the client without blowing the roadmap.",
                "brief": (
                    "After discovery you learned:\n"
                    "- Compliance requires: deal records + timestamps + user attribution, in CSV\n"
                    "- Client's ops team actually just needs monthly exports (not real-time)\n"
                    "- Contacts export is 'nice to have' for Q2\n"
                    "- Engineering says CSV export of deals = 2 weeks; real-time = 6 weeks\n"
                    "- Two other clients have asked for similar features"
                ),
                "seed_question": (
                    "Define the MVP scope for Q2. What's in, what's explicitly out, "
                    "and how do you explain the trade-offs to the client?"
                ),
                "system_prompt_extra": (
                    "Guide the user to write a crisp scope statement with explicit exclusions. "
                    "Ask how they'd handle scope creep from the client. "
                    "Push them to think about the other two clients as a signal."
                ),
            },
            {   # session 3
                "title": "Feature Spec",
                "goal": "Write a spec tight enough for engineering to estimate and build from.",
                "brief": (
                    "MVP is approved: CSV export of deal records (with timestamps and user attribution), "
                    "triggered manually from the admin panel, delivered via email link within 30 minutes.\n\n"
                    "Your engineers need a spec to start sprint planning."
                ),
                "seed_question": (
                    "Write the feature spec. Include: user story, functional requirements, "
                    "edge cases (what if export takes >30 min? what if the file is >1 GB?), "
                    "and acceptance criteria."
                ),
                "system_prompt_extra": (
                    "Act as a senior engineer reviewing the spec. Push for completeness on edge cases, "
                    "clear acceptance criteria (testable, not vague), and any security considerations "
                    "(who can trigger exports? are exports access-controlled?)."
                ),
            },
        ],
    },
    {
        "title": "The Case Analyst",
        "description": (
            "A Boston restaurant chain is deciding between two growth strategies. "
            "You'll use AI to run a structured business analysis — but you must drive "
            "the framework and catch every assumption the AI slips in that isn't "
            "backed by the case facts. Three sessions: open analysis, framework-driven "
            "analysis, then devil's advocate and assumption audit."
        ),
        "category": "Product & Business",
        "difficulty": "Intermediate",
        "week": None,
        "total_sessions": 3,
        "sessions_data": [
            {
                "title": "Open Analysis",
                "goal": "See how AI responds to an open-ended strategy question — and what it gets wrong.",
                "brief": (
                    "--- THE CASE ---\n\n"
                    "Harbour Table is a mid-size Boston restaurant group with 4 locations "
                    "(South End, Cambridge, Seaport, Back Bay). Founded in 2016, it runs "
                    "modern American cuisine at the $35–55 entrée price point. FY2024 revenue: "
                    "$14.2M across all locations. Net margin: 8.1% — above the 3–5% industry "
                    "average, driven by tight labor scheduling and a loyal lunch crowd.\n\n"
                    "The ownership group is debating two options for their FY2026 capital budget ($2.1M):\n\n"
                    "OPTION A — CITY EXPANSION: Open a fifth location in Providence, RI. "
                    "Providence has a growing food scene and lower commercial rents (~40% below Boston). "
                    "However, Harbour Table has no brand recognition there, the ownership team "
                    "is already stretched managing 4 locations, and their head chef has flagged "
                    "that quality control across a 5th site would require hiring a new culinary director "
                    "(estimated cost: $140k/year).\n\n"
                    "OPTION B — DELIVERY INFRASTRUCTURE: Invest in a centralized ghost kitchen "
                    "in Roxbury to consolidate delivery operations currently split across all 4 locations. "
                    "Delivery currently accounts for 18% of revenue but has a 12% lower margin than "
                    "dine-in due to third-party platform fees (28–30%). A ghost kitchen would let them "
                    "negotiate direct delivery contracts and potentially launch 2 virtual brands. "
                    "Upfront cost: ~$1.8M in build-out. Break-even projection: 26 months (internal estimate).\n\n"
                    "The ownership group meets in 3 weeks to vote.\n"
                    "--- END CASE ---"
                ),
                "seed_question": (
                    "Read the case above. What should Harbour Table do — expand to Providence "
                    "or invest in the ghost kitchen? Give me your recommendation."
                ),
                "system_prompt_extra": (
                    "The student is practicing business case analysis. In this first session, "
                    "give a confident recommendation when asked — don't volunteer a structured framework yet. "
                    "After they engage with your answer, ask: 'What assumptions did I make "
                    "that aren't directly supported by the case?' "
                    "This surfaces the key learning: AI jumps to conclusions without flagging unsupported assumptions."
                ),
            },
            {
                "title": "Framework-Driven Analysis",
                "goal": "Use a named framework to ground every claim in case facts, not assumptions.",
                "brief": (
                    "In Session 1, you got a recommendation — but it likely included assumptions "
                    "not in the case (e.g., Providence market size, delivery growth rates, "
                    "management capacity).\n\n"
                    "Now apply structure. Prompt the AI to run a proper framework analysis where "
                    "every point is tied to a specific fact from the case. Any claim that requires "
                    "information NOT in the case must be flagged as [ASSUMPTION].\n\n"
                    "The case text is the same as Session 1 — refer back to it.\n\n"
                    "Try SWOT, a decision matrix, or a risk/reward framework. Pick one and drive it yourself."
                ),
                "seed_question": (
                    "Apply a SWOT analysis to Harbour Table's two options. "
                    "For every point, cite the specific sentence or number from the case that supports it. "
                    "If a point requires information not in the case, mark it clearly as [ASSUMPTION]."
                ),
                "system_prompt_extra": (
                    "The student is running a structured case analysis. "
                    "Follow their chosen framework strictly. For each point, distinguish "
                    "what is in the case from what you are inferring. "
                    "If the student doesn't ask you to flag assumptions, prompt them: "
                    "'Should I mark which points are assumptions vs. direct case facts?' "
                    "Help them see the difference between grounded analysis and extrapolation."
                ),
            },
            {
                "title": "Devil's Advocate & Assumption Audit",
                "goal": "Stress-test your analysis by arguing the opposite and auditing hidden assumptions.",
                "brief": (
                    "You've done a structured analysis. Now stress-test it.\n\n"
                    "Two moves:\n"
                    "1. Ask the AI to argue the opposite recommendation with equal conviction. "
                    "A good analysis should survive the counter-argument.\n\n"
                    "2. Ask the AI to list the top 3 assumptions in your Session 2 analysis "
                    "and rate how well each is supported by case facts "
                    "(1 = pure assumption, 5 = directly stated in case).\n\n"
                    "By the end, you should know which parts of your recommendation rest on "
                    "solid evidence and which depend on assumptions that could easily be wrong."
                ),
                "seed_question": (
                    "Argue the opposite of the recommendation from Session 2. "
                    "Make the strongest possible case for the other option using only facts from the case. "
                    "Then list the 3 biggest assumptions in the original analysis and rate each "
                    "on how well the case actually supports it (1–5 scale)."
                ),
                "system_prompt_extra": (
                    "The student is stress-testing their analysis. "
                    "Argue the opposite position with conviction — don't hedge. "
                    "When listing assumptions, be honest: flag things the analysis relied on "
                    "that weren't in the case (market growth rates, competitive dynamics, execution risk). "
                    "Push them to distinguish 'the case says X' from 'I assumed X.' "
                    "End by asking: which assumptions, if wrong, would flip the recommendation?"
                ),
            },
        ],
    },
    {
        "title": "The Data Interpreter",
        "description": (
            "You're given a small quarterly sales dataset for a Boston retail company — "
            "with one suspicious number planted in it. Three sessions: accept the AI's raw "
            "analysis, then drive it with specific analytical questions, then challenge "
            "a suspicious data point with sensitivity analysis. Learn when to trust "
            "AI-generated insights and when to push back."
        ),
        "category": "Data & Analysis",
        "difficulty": "Intermediate",
        "week": None,
        "total_sessions": 3,
        "sessions_data": [
            {
                "title": "What Do You See?",
                "goal": "Understand what happens when you give AI data without a specific question.",
                "brief": (
                    "--- THE DATASET ---\n\n"
                    "Quarterly net sales ($000s) — Beacon Retail Co., 4 product lines, 5 quarters:\n\n"
                    "Quarter    | Line A | Line B | Line C | Line D\n"
                    "-----------|--------|--------|--------|-------\n"
                    "Q1 2024    |  420   |  310   |  580   |  195\n"
                    "Q2 2024    |  445   |  295   |  551   |  198\n"
                    "Q3 2024    |  461   |  280   |   87   |  201\n"
                    "Q4 2024    |  478   |  398   |  503   |  199\n"
                    "Q1 2025    |  495   |  305   |  468   |  202\n\n"
                    "Line A: Everyday basics (clothing, socks, essentials)\n"
                    "Line B: Seasonal / gifting products\n"
                    "Line C: Premium home goods\n"
                    "Line D: Accessories\n\n"
                    "Context: 12-store regional chain across New England. "
                    "No major product changes during this period.\n"
                    "--- END DATASET ---"
                ),
                "seed_question": (
                    "Here's our quarterly sales data for the past 5 quarters across 4 product lines. "
                    "What trends do you see? What should we pay attention to?"
                ),
                "system_prompt_extra": (
                    "The student is learning to analyze data with AI. "
                    "Give a reasonable narrative analysis: Line A growing steadily, "
                    "Line B seasonal (high Q4), Line C declining, Line D flat. "
                    "Do NOT flag the Q3 2024 Line C number ($87k) as suspicious unless the student asks. "
                    "Be confident. This sets up the learning in Session 2 where structured questions "
                    "reveal what the open-ended analysis glossed over."
                ),
            },
            {
                "title": "Specific Questions, Specific Answers",
                "goal": "Use precise analytical prompts to get verifiable, structured output.",
                "brief": (
                    "In Session 1, the AI gave you a narrative. Narratives are hard to verify "
                    "and easy to accept uncritically.\n\n"
                    "Now ask specific analytical questions that produce numbers you can check:\n"
                    "- Quarter-over-quarter growth rates for each line\n"
                    "- Which line has the highest variance across the 5 quarters?\n"
                    "- Are there any outliers? (A value more than 2 standard deviations "
                    "from that line's mean)\n"
                    "- Present everything as a table, not a paragraph\n\n"
                    "Structured output (tables, numbers) is much easier to verify than "
                    "narrative summaries. If the AI produces a number, you can check it yourself.\n\n"
                    "The data is the same table from Session 1."
                ),
                "seed_question": (
                    "For each product line, calculate the quarter-over-quarter growth rate "
                    "for all 4 transitions (Q1→Q2, Q2→Q3, Q3→Q4, Q4→Q1 2025). "
                    "Present as a table. Then identify any outlier values "
                    "(more than 2 standard deviations from that line's average). "
                    "Which line has the highest variance?"
                ),
                "system_prompt_extra": (
                    "The student is asking for specific calculations. Do the math carefully. "
                    "Line C Q2→Q3: (87-551)/551 = -84.2% — flag this prominently. "
                    "Line C has by far the highest variance because of the Q3 outlier. "
                    "Present everything in tables as requested. "
                    "After presenting, ask: 'That Q3 Line C number is -84.2%. "
                    "Does that look like a real business event to you, or could there be another explanation?'"
                ),
            },
            {
                "title": "Challenge the Numbers",
                "goal": "Apply sensitivity analysis — what if that suspicious number is wrong?",
                "brief": (
                    "Line C (Premium Home Goods) dropped from $551k in Q2 2024 to $87k in Q3 2024 "
                    "— an 84% decline in one quarter — then recovered to $503k in Q4 2024.\n\n"
                    "That Q3 number is suspicious. For a regional chain with no major product changes, "
                    "an 84% single-quarter drop followed by a full recovery is unusual.\n\n"
                    "Possibilities:\n"
                    "- Real event: supply chain disruption, store closure, lost wholesale account\n"
                    "- Data issue: typo ($87k instead of $487k?), reporting error, missing stores\n\n"
                    "Your job: use the AI to run a sensitivity analysis. "
                    "What changes if that Q3 number is a data error? "
                    "What additional data would you need to be confident either way?\n\n"
                    "Core skill: don't just accept what the data says — "
                    "question whether the data itself is right."
                ),
                "seed_question": (
                    "The Q3 2024 Line C number ($87k) dropped 84% from Q2 and then fully recovered in Q4. "
                    "Walk me through two scenarios: "
                    "(1) the drop is real — what would explain it and what should management do? "
                    "(2) the drop is a data entry error — how would that change the trend analysis? "
                    "Then tell me: what 3 pieces of additional data would confirm which scenario is true?"
                ),
                "system_prompt_extra": (
                    "The student is running a sensitivity analysis on a suspicious data point. "
                    "For scenario 1 (real): supply chain disruption or store closure are plausible, "
                    "but the full Q4 recovery argues against most of these explanations. "
                    "For scenario 2 (error): a typo ($87k vs $487k) would make Line C "
                    "a mild decline rather than a crisis — completely different story for management. "
                    "Emphasize: the entire 'Line C is in trouble' narrative rests on this one number. "
                    "Ask: 'What would you tell your manager differently depending on which scenario is true?'"
                ),
            },
        ],
    },
    {
        "title": "The Presentation Outliner",
        "description": (
            "You have a 10-minute class presentation coming up. AI can help you structure it — "
            "but its default output is generic and forgettable. Three sessions: see what a vague "
            "prompt produces, then constrain it with audience and time specifics, then role-play "
            "the audience to find weak spots before you present."
        ),
        "category": "Creative & Strategy",
        "difficulty": "Beginner",
        "week": None,
        "total_sessions": 3,
        "sessions_data": [
            {
                "title": "The Generic Outline",
                "goal": "See what AI produces with a vague prompt — and why it's not good enough.",
                "brief": (
                    "You have a 10-minute presentation next week. "
                    "Topic: **How AI is changing the job market for college graduates.**\n\n"
                    "This is relevant to every major at Northeastern — CS students worried about "
                    "coding jobs, business students thinking about consulting, health science "
                    "students thinking about diagnostics.\n\n"
                    "In this session: ask the AI for an outline with as little detail as possible. "
                    "Just the topic and the time limit. When you get it back, ask yourself:\n"
                    "- Could this outline work for anyone, for any class, at any school?\n"
                    "- Does it have a clear argument or just a list of facts?\n"
                    "- Would a professor remember it after seeing 20 other presentations?\n\n"
                    "The goal isn't a good outline yet — it's to see what 'generic' looks like "
                    "so you know what to fix."
                ),
                "seed_question": (
                    "Create an outline for a 10-minute presentation on how AI is changing "
                    "the job market for college graduates."
                ),
                "system_prompt_extra": (
                    "The student is learning how prompt quality affects output quality. "
                    "Respond to their prompt as given. If they give minimal context, "
                    "give them a generic template-style outline (Introduction, 3 main points, Conclusion). "
                    "Make it reasonably competent but clearly generic — the kind that could apply to anyone. "
                    "After presenting it, ask: 'How specific is this to you, your class, or your argument? "
                    "What would make this stand out from the other 20 presentations your professor sees?'"
                ),
            },
            {
                "title": "Constrained and Specific",
                "goal": "Use audience, time allocation, and a clear argument to force a better outline.",
                "brief": (
                    "That first outline works for anyone — which means it's remarkable to no one.\n\n"
                    "To get a strong outline, give the AI constraints it can't ignore:\n\n"
                    "**Your situation:**\n"
                    "- Audience: 22 classmates who've used ChatGPT but haven't thought critically "
                    "about AI's economic effects\n"
                    "- Time: exactly 10 minutes — 1.5 min intro, 7 min body (3 points), 1.5 min conclusion\n"
                    "- Your argument: 'AI won't eliminate jobs — it will split the job market into "
                    "two tiers: people who use AI well, and people who get replaced by those people'\n"
                    "- Opening: start with a surprising statistic, not 'good morning everyone'\n"
                    "- Your major: use your actual major — this is your presentation\n\n"
                    "Build a prompt using all of these constraints. "
                    "The more specific you are, the less the AI can default to generic structure."
                ),
                "seed_question": (
                    "Create a 10-minute presentation outline on AI and the job market with these constraints: "
                    "audience is 22 undergrads who've used ChatGPT but haven't studied its economic impacts, "
                    "time breakdown is 1.5 min intro / 7 min body (3 points) / 1.5 min conclusion, "
                    "my main argument is that AI splits the job market into two tiers — "
                    "skilled AI users vs. those replaced by them. "
                    "Open with a surprising statistic. Every slide needs a reason to exist."
                ),
                "system_prompt_extra": (
                    "The student is practicing constrained prompting. "
                    "Respect every constraint they give you: time allocations, the specific argument, "
                    "the audience level. Build every section around their thesis — "
                    "not a balanced 'here are pros and cons' structure. "
                    "The outline should feel written for this student, not pulled from a template. "
                    "After presenting it, ask: 'Which slide would your classmates most likely "
                    "check their phone during? And which transition feels weakest?'"
                ),
            },
            {
                "title": "Audience Stress Test",
                "goal": "Role-play the audience to find weak spots before you're standing in front of them.",
                "brief": (
                    "You have a solid outline from Session 2. Now pressure-test it "
                    "before you're standing in front of 22 people.\n\n"
                    "Ask the AI to role-play two audience members:\n\n"
                    "1. **A distracted classmate** — sat through 4 presentations already today, "
                    "tired, giving yours 60% attention. What would make them tune out? "
                    "What would snap them back?\n\n"
                    "2. **Your professor** — heard this topic framed 10 different ways. "
                    "What would make them think 'finally, someone with an actual argument' "
                    "vs. 'another surface-level AI overview'?\n\n"
                    "Use the feedback to revise at least one section. "
                    "Walk out of this session knowing exactly which slide is your weakest "
                    "and what you're going to do about it."
                ),
                "seed_question": (
                    "You are a tired undergrad who has sat through 4 presentations today. "
                    "Look at my outline and tell me: at what point would you check your phone, and why? "
                    "What one change would keep you engaged? "
                    "Then switch roles: you are my professor, who has heard this topic 15 times. "
                    "What would make this presentation stand out vs. feel like every other AI talk?"
                ),
                "system_prompt_extra": (
                    "The student is role-playing audience feedback. Take both roles seriously — "
                    "don't just say 'this is great.' "
                    "As the tired student: identify the most forgettable section "
                    "(often a generic 'background on AI' or 'history' slide). "
                    "As the professor: push for the specific argument — does the outline prove its thesis "
                    "or just present information? Information without argument doesn't earn good grades. "
                    "After the feedback, ask: 'Which piece of feedback surprised you most? "
                    "What specific change are you making before you present?'"
                ),
            },
        ],
    },
]


# ---------------------------------------------------------------------------
# Dependency: get current user from Bearer token
# ---------------------------------------------------------------------------

async def get_current_user(authorization: Optional[str] = Header(None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authorization header required")
    token = authorization.removeprefix("Bearer ").strip()
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user_id


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def _assert_user_manages_classroom(db: AsyncSession, user_id: str, classroom_id: str) -> Classroom:
    room = await db.get(Classroom, classroom_id)
    if not room:
        raise HTTPException(status_code=404, detail="Classroom not found")
    if room.instructor_user_id == user_id:
        return room
    r = await db.execute(
        select(ClassroomMembership).where(
            ClassroomMembership.user_id == user_id,
            ClassroomMembership.classroom_id == classroom_id,
            ClassroomMembership.role.in_(("instructor", "admin")),
        )
    )
    if not r.scalar_one_or_none():
        raise HTTPException(
            status_code=403,
            detail="Only instructors for this section can create or assign challenges",
        )
    return room


def _default_sessions_data(total: int) -> list[dict]:
    sessions = []
    for i in range(1, total + 1):
        sessions.append(
            {
                "title": f"Session {i}",
                "goal": "Practice clear, iterative prompting with the AI coach.",
                "brief": (
                    f"This is session {i} of {total}. State your goal, add context, and refine your "
                    "prompts using the coach's feedback."
                ),
                "seed_question": "What would you like to work on in this session?",
                "system_prompt_extra": (
                    "You are an AI fluency coach. Ask clarifying questions, encourage iteration on "
                    "prompts, and connect answers to stronger human-led use of AI."
                ),
            }
        )
    return sessions


class CreateChallengeBody(BaseModel):
    classroom_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1, max_length=300)
    description: str = Field(..., min_length=1, max_length=16000)
    category: str = Field(default="General", max_length=120)
    difficulty: str = Field(default="Beginner", max_length=64)
    week: Optional[int] = None
    total_sessions: int = Field(default=1, ge=1, le=6)
    # Timed-session settings (optional; null = untimed / no minimum).
    time_limit_minutes: Optional[int] = Field(None, ge=1, le=120)
    min_turns: Optional[int] = Field(None, ge=1, le=50)
    is_active: Optional[bool] = None  # None → publish immediately (default); False → save as draft


class UpdateChallengeBody(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    description: Optional[str] = Field(None, min_length=1, max_length=16000)
    category: Optional[str] = Field(None, max_length=120)
    difficulty: Optional[str] = Field(None, max_length=64)
    week: Optional[int] = None
    # null clears the setting (untimed). "Field set vs unset" is checked via
    # model_fields_set so an explicit null can distinguish from "not provided".
    time_limit_minutes: Optional[int] = Field(None, ge=1, le=120)
    min_turns: Optional[int] = Field(None, ge=1, le=50)
    is_active: Optional[bool] = None


async def _student_classroom_ids(db: AsyncSession, user_id: str) -> set[str]:
    r = await db.execute(
        select(ClassroomMembership.classroom_id).where(
            ClassroomMembership.user_id == user_id,
            ClassroomMembership.role == "student",
        )
    )
    return {row[0] for row in r.all()}


async def _test_enrollment_classroom_ids(db: AsyncSession, user_id: str) -> set[str]:
    r = await db.execute(
        select(InstructorTestEnrollment.classroom_id).where(InstructorTestEnrollment.user_id == user_id)
    )
    return {row[0] for row in r.all()}


async def _challenge_access_sets(
    db: AsyncSession, user_id: str
) -> tuple[set[str], set[str], bool]:
    """(allowed_challenge_ids, instructor_preview_ids, is_platform_admin)."""
    u = await db.get(User, user_id)
    is_admin = bool(u and u.is_platform_admin)
    if is_admin:
        r = await db.execute(select(Challenge.id))
        return {row[0] for row in r.all()}, set(), True

    stu = await _student_classroom_ids(db, user_id)
    te = await _test_enrollment_classroom_ids(db, user_id)
    all_cids = stu | te
    if not all_cids:
        return set(), set(), False

    r = await db.execute(
        select(ClassroomChallenge.challenge_id, ClassroomChallenge.classroom_id).where(
            ClassroomChallenge.classroom_id.in_(all_cids)
        )
    )
    rows = r.all()
    by_challenge: dict[str, set[str]] = {}
    for ch_id, cid in rows:
        by_challenge.setdefault(ch_id, set()).add(cid)

    allowed = set(by_challenge.keys())
    preview: set[str] = set()
    for ch_id, cids in by_challenge.items():
        has_student_path = bool(cids & stu)
        has_test_only_path = any(cid in te and cid not in stu for cid in cids)
        if has_test_only_path and not has_student_path:
            preview.add(ch_id)

    return allowed, preview, False


async def _manages_linked_classroom(db: AsyncSession, user_id: str, challenge_id: str) -> bool:
    r = await db.execute(
        select(ClassroomChallenge.classroom_id).where(ClassroomChallenge.challenge_id == challenge_id)
    )
    for (cid,) in r.all():
        room = await db.get(Classroom, cid)
        if not room:
            continue
        if room.instructor_user_id == user_id:
            return True
        m = await db.execute(
            select(ClassroomMembership).where(
                ClassroomMembership.user_id == user_id,
                ClassroomMembership.classroom_id == cid,
                ClassroomMembership.role.in_(("instructor", "admin")),
            )
        )
        if m.scalar_one_or_none():
            return True
    return False


async def _can_play_challenge(
    db: AsyncSession, user_id: str, challenge_id: str, ch: Challenge, is_admin: bool
) -> bool:
    if is_admin:
        return bool(ch.is_active)
    allowed, _, _ = await _challenge_access_sets(db, user_id)
    if challenge_id not in allowed:
        return False
    return bool(ch.is_active)


# ---------------------------------------------------------------------------
# Seed helper — called from main.py lifespan
# ---------------------------------------------------------------------------

async def seed_challenges():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Challenge))
        existing = result.scalars().all()
        existing_titles = {c.title for c in existing}

        added = 0
        for data in SEED_CHALLENGES:
            if data["title"] not in existing_titles:
                db.add(Challenge(
                    title=data["title"],
                    description=data["description"],
                    category=data["category"],
                    difficulty=data["difficulty"],
                    week=data.get("week"),
                    total_sessions=data["total_sessions"],
                    sessions_data=data["sessions_data"],
                ))
                added += 1

        if added:
            await db.commit()
            log.info(f"Seeded {added} new challenge(s)")
        else:
            log.info("All challenges already seeded")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_challenges(
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    allowed_ids, preview_ids, is_admin = await _challenge_access_sets(db, user_id)
    if not allowed_ids:
        return []

    if is_admin:
        result = await db.execute(select(Challenge).where(Challenge.id.in_(allowed_ids)))
    else:
        result = await db.execute(
            select(Challenge).where(Challenge.is_active.is_(True), Challenge.id.in_(allowed_ids))
        )
    challenges = result.scalars().all()

    # Fetch user's session progress for each challenge
    sessions_result = await db.execute(
        select(UserChallengeSession).where(UserChallengeSession.user_id == user_id)
    )
    user_sessions = sessions_result.scalars().all()
    sessions_by_challenge: dict[str, list] = {}
    for s in user_sessions:
        sessions_by_challenge.setdefault(s.challenge_id, []).append(s)

    out = []
    for ch in challenges:
        user_ch_sessions = sessions_by_challenge.get(ch.id, [])
        completed = sum(1 for s in user_ch_sessions if s.status == "completed")
        best_pei = max((s.best_pei for s in user_ch_sessions if s.best_pei is not None), default=None)
        out.append({
            "id": ch.id,
            "title": ch.title,
            "description": ch.description,
            "category": ch.category,
            "difficulty": ch.difficulty,
            "week": ch.week,
            "total_sessions": ch.total_sessions,
            "time_limit_minutes": ch.time_limit_minutes,
            "min_turns": ch.min_turns,
            "sessions_completed": completed,
            "best_pei": best_pei,
            "is_active": bool(ch.is_active),
            "instructor_preview": bool(not is_admin and ch.id in preview_ids),
        })

    return out


@router.get("/me/husky-score")
async def my_husky_score(
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Husky Score = average of per-session avg PEI across all the user's ended challenge sessions."""
    stmt = (
        select(
            func.avg(UserChallengeSession.session_avg_pei),
            func.count(UserChallengeSession.id),
        )
        .where(
            UserChallengeSession.user_id == user_id,
            UserChallengeSession.session_avg_pei.is_not(None),
        )
    )
    row = (await db.execute(stmt)).one_or_none()
    if not row:
        return {"husky_score": None, "turns_counted": 0, "sessions_counted": 0}
    avg_pei, session_count = row
    return {
        "husky_score": round(float(avg_pei), 1) if avg_pei is not None else None,
        "turns_counted": int(session_count or 0),
        "sessions_counted": int(session_count or 0),
    }


@router.post("", status_code=201)
async def create_challenge(
    body: CreateChallengeBody,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Instructor only: create a challenge and assign it to a section you manage."""
    await _assert_user_manages_classroom(db, user_id, body.classroom_id)
    max_so = await db.scalar(
        select(func.coalesce(func.max(ClassroomChallenge.sort_order), -1)).where(
            ClassroomChallenge.classroom_id == body.classroom_id
        )
    )
    next_sort = int(max_so if max_so is not None else -1) + 1
    sessions_data = _default_sessions_data(body.total_sessions)
    ch = Challenge(
        title=body.title.strip(),
        description=body.description.strip(),
        category=body.category.strip() or "General",
        difficulty=body.difficulty.strip() or "Beginner",
        week=body.week,
        total_sessions=body.total_sessions,
        sessions_data=sessions_data,
        time_limit_minutes=body.time_limit_minutes,
        min_turns=body.min_turns,
        is_active=body.is_active if body.is_active is not None else True,
        status="draft" if body.is_active is False else "published",
        created_by_user_id=user_id,
    )
    db.add(ch)
    await db.flush()
    db.add(
        ClassroomChallenge(
            classroom_id=body.classroom_id,
            challenge_id=ch.id,
            sort_order=next_sort,
        )
    )
    await db.commit()
    await db.refresh(ch)
    log.info("challenge created id=%s classroom=%s by user=%s", ch.id, body.classroom_id, user_id[:8])
    return {
        "id": ch.id,
        "title": ch.title,
        "classroom_id": body.classroom_id,
        "total_sessions": ch.total_sessions,
    }


@router.patch("/{challenge_id}")
async def update_challenge(
    challenge_id: str,
    body: UpdateChallengeBody,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not body.model_fields_set:
        raise HTTPException(status_code=400, detail="Provide at least one field to update")

    ch = await db.get(Challenge, challenge_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Challenge not found")

    u = await db.get(User, user_id)
    is_admin = bool(u and u.is_platform_admin)
    if not is_admin and not await _manages_linked_classroom(db, user_id, challenge_id):
        raise HTTPException(status_code=403, detail="Only an instructor for a linked section can update this challenge")

    if body.title is not None:
        ch.title = body.title.strip()
    if body.description is not None:
        ch.description = body.description.strip()
    if body.category is not None:
        ch.category = body.category.strip() or "General"
    if body.difficulty is not None:
        ch.difficulty = body.difficulty.strip() or "Beginner"
    if body.week is not None:
        ch.week = body.week
    # Timer fields: applied when explicitly present (an explicit null clears them).
    if "time_limit_minutes" in body.model_fields_set:
        ch.time_limit_minutes = body.time_limit_minutes
    if "min_turns" in body.model_fields_set:
        ch.min_turns = body.min_turns
    if body.is_active is not None:
        ch.is_active = body.is_active
    await db.commit()
    await db.refresh(ch)
    return {
        "id": ch.id,
        "title": ch.title,
        "is_active": bool(ch.is_active),
        "week": ch.week,
        "time_limit_minutes": ch.time_limit_minutes,
        "min_turns": ch.min_turns,
    }


@router.get("/{challenge_id}")
async def get_challenge(
    challenge_id: str,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ch = await db.get(Challenge, challenge_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Challenge not found")

    allowed, _, is_admin = await _challenge_access_sets(db, user_id)
    manages = await _manages_linked_classroom(db, user_id, challenge_id)
    if is_admin:
        visible = True
    elif manages:
        visible = True
    elif ch.id in allowed and ch.is_active:
        visible = True
    else:
        visible = False
    if not visible:
        raise HTTPException(status_code=404, detail="Challenge not found")

    sessions_result = await db.execute(
        select(UserChallengeSession).where(
            UserChallengeSession.user_id == user_id,
            UserChallengeSession.challenge_id == challenge_id,
        )
    )
    user_sessions = sessions_result.scalars().all()
    sessions_map = {s.session_number: s for s in user_sessions}

    sessions_out = []
    for i, sd in enumerate(ch.sessions_data, start=1):
        us = sessions_map.get(i)
        sessions_out.append({
            "session_number": i,
            "title": sd["title"],
            "goal": sd["goal"],
            "brief": sd["brief"],
            "seed_question": sd["seed_question"],
            "status": us.status if us else "not_started",
            "best_pei": us.best_pei if us else None,
            "conversation_id": us.conversation_id if us else None,
            "started_at": us.started_at.isoformat() if us and us.started_at else None,
            "completed_at": us.completed_at.isoformat() if us and us.completed_at else None,
            "end_reason": us.end_reason if us else None,
        })

    return {
        "id": ch.id,
        "title": ch.title,
        "description": ch.description,
        "category": ch.category,
        "difficulty": ch.difficulty,
        "week": ch.week,
        "total_sessions": ch.total_sessions,
        "time_limit_minutes": ch.time_limit_minutes,
        "min_turns": ch.min_turns,
        "sessions": sessions_out,
    }


@router.post("/{challenge_id}/sessions/{session_number}/start")
async def start_session(
    challenge_id: str,
    session_number: int,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ch = await db.get(Challenge, challenge_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Challenge not found")

    _, _, is_admin = await _challenge_access_sets(db, user_id)
    if not await _can_play_challenge(db, user_id, challenge_id, ch, is_admin):
        raise HTTPException(status_code=404, detail="Challenge not found")

    if session_number < 1 or session_number > ch.total_sessions:
        raise HTTPException(status_code=400, detail="Invalid session number")

    # Check previous session is completed (except session 1)
    if session_number > 1:
        prev_result = await db.execute(
            select(UserChallengeSession).where(
                UserChallengeSession.user_id == user_id,
                UserChallengeSession.challenge_id == challenge_id,
                UserChallengeSession.session_number == session_number - 1,
            )
        )
        prev = prev_result.scalar_one_or_none()
        if not prev or prev.status != "completed":
            raise HTTPException(
                status_code=400,
                detail=f"Complete session {session_number - 1} before starting session {session_number}"
            )

    # Find or create UserChallengeSession
    existing_result = await db.execute(
        select(UserChallengeSession).where(
            UserChallengeSession.user_id == user_id,
            UserChallengeSession.challenge_id == challenge_id,
            UserChallengeSession.session_number == session_number,
        )
    )
    session_record = existing_result.scalar_one_or_none()

    if not session_record:
        session_record = UserChallengeSession(
            user_id=user_id,
            challenge_id=challenge_id,
            session_number=session_number,
            status="in_progress",
            started_at=datetime.utcnow(),
            # Snapshot the challenge's timer settings now, so later instructor
            # edits don't change the rules mid-attempt. The countdown is anchored
            # to started_at + time_limit_minutes.
            time_limit_minutes=ch.time_limit_minutes,
            min_turns=ch.min_turns,
        )
        db.add(session_record)
        await db.commit()
        await db.refresh(session_record)
    elif session_record.status == "not_started":
        session_record.status = "in_progress"
        session_record.started_at = datetime.utcnow()
        session_record.time_limit_minutes = ch.time_limit_minutes
        session_record.min_turns = ch.min_turns
        await db.commit()

    sd = ch.sessions_data[session_number - 1]
    return {
        "session_id": session_record.id,
        "challenge_id": challenge_id,
        "session_number": session_number,
        "status": session_record.status,
        "title": sd["title"],
        "goal": sd["goal"],
        "brief": sd["brief"],
        "seed_question": sd["seed_question"],
        "conversation_id": session_record.conversation_id,
        "time_limit_minutes": session_record.time_limit_minutes,
        "min_turns": session_record.min_turns,
        "started_at": session_record.started_at.isoformat() if session_record.started_at else None,
    }


@router.post("/{challenge_id}/sessions/{session_number}/complete")
async def complete_session(
    challenge_id: str,
    session_number: int,
    user_id: str = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ch = await db.get(Challenge, challenge_id)
    if not ch:
        raise HTTPException(status_code=404, detail="Challenge not found")
    _, _, is_admin = await _challenge_access_sets(db, user_id)
    if not await _can_play_challenge(db, user_id, challenge_id, ch, is_admin):
        raise HTTPException(status_code=404, detail="Challenge not found")

    result = await db.execute(
        select(UserChallengeSession).where(
            UserChallengeSession.user_id == user_id,
            UserChallengeSession.challenge_id == challenge_id,
            UserChallengeSession.session_number == session_number,
        )
    )
    session_record = result.scalar_one_or_none()
    if not session_record:
        raise HTTPException(status_code=404, detail="Session not found — start it first")

    session_record.status = "completed"
    session_record.completed_at = datetime.utcnow()
    # User-initiated completion from the challenge detail page.
    if session_record.end_reason is None:
        session_record.end_reason = "manual"
    await db.commit()
    return {"status": "completed"}
