import os
import json
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import types
from evaluator_v3 import evaluate_conversation_v3 as evaluate_conversation
from session_analysis import analyze_session
from sqlalchemy import select, update, func

from database import init_db, AsyncSessionLocal, Conversation, Message, EvalResult, Challenge, UserChallengeSession, User
from auth import router as auth_router, decode_token, pwd_context
from challenges import router as challenges_router, seed_challenges, get_current_user, get_db
from classrooms import router as classrooms_router, seed_demo_classroom, seed_pilot_classroom
from admin import router as admin_router

_backend_dir = Path(__file__).resolve().parent
load_dotenv(_backend_dir / ".env")
load_dotenv()

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chat-evaluator")


async def _sync_platform_admin_emails() -> None:
    """Grant is_platform_admin to users whose emails appear in PLATFORM_ADMIN_EMAILS (comma-separated)."""
    raw = os.getenv("PLATFORM_ADMIN_EMAILS", "").strip()
    if not raw:
        return
    emails = {e.strip().lower() for e in raw.split(",") if e.strip()}
    if not emails:
        return
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(User).where(User.email.in_(emails)))
        for u in r.scalars().all():
            if not bool(u.is_platform_admin):
                u.is_platform_admin = True
        await db.commit()
    log.info("Synced platform admin flag for %d email(s)", len(emails))


async def seed_dev_platform_admin() -> None:
    """
    Non-production: ensure a platform admin exists for local QA.
    Sign in with email admin@husky.local or bare login id \"admin\" + SEED_DEV_ADMIN_PASSWORD (default 1234).
    Disabled when ENVIRONMENT=production, or SEED_DEV_ADMIN=0/false/no.
    """
    if os.getenv("ENVIRONMENT", "").strip().lower() in ("production", "prod"):
        return
    if os.getenv("SEED_DEV_ADMIN", "1").strip().lower() in ("0", "false", "no"):
        return
    email = os.getenv("SEED_DEV_ADMIN_EMAIL", "admin@husky.local").strip().lower()
    password = os.getenv("SEED_DEV_ADMIN_PASSWORD", "1234")
    name = (os.getenv("SEED_DEV_ADMIN_NAME", "Platform admin") or "Platform admin").strip()
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(User).where(User.email == email))
        u = r.scalar_one_or_none()
        if u:
            if not bool(u.is_platform_admin):
                u.is_platform_admin = True
                await db.commit()
            return
        db.add(
            User(
                email=email,
                name=name,
                password_hash=pwd_context.hash(password),
                is_platform_admin=True,
            )
        )
        await db.commit()
    log.info("Seeded dev platform admin %r (sign in with bare id admin or this email)", email)


api_key = os.getenv("GOOGLE_API_KEY", "")
if not api_key:
    log.warning("GOOGLE_API_KEY is not set — requests will fail")
client = genai.Client(api_key=api_key)


# --- Post-session analysis: background-task plumbing ---------------------------
# A "pending" analysis older than this is treated as orphaned (e.g. the worker
# process died mid-generation) and gets regenerated rather than wedged forever.
_ANALYSIS_STALE_SECONDS = 300

# Strong references to in-flight background tasks. asyncio only keeps weak refs
# to tasks, so without this the GC can collect one mid-run (e.g. while it's
# awaiting the LLM) and the analysis silently never completes.
_analysis_tasks: set = set()


def _pending_blob() -> dict:
    """The 'analysis is generating' marker, timestamped so we can detect a stall."""
    return {"status": "pending", "pending_at": datetime.utcnow().isoformat()}


def _pending_is_stale(blob: dict | None) -> bool:
    pa = (blob or {}).get("pending_at")
    if not pa:
        return True  # legacy pending rows (no timestamp) -> regenerate
    try:
        started = datetime.fromisoformat(pa)
    except ValueError:
        return True
    return (datetime.utcnow() - started).total_seconds() > _ANALYSIS_STALE_SECONDS


def _spawn_analysis(conversation_id: str, user_id: str):
    """Fire-and-forget the analysis generator while holding a strong task ref."""
    task = asyncio.create_task(_generate_session_analysis(conversation_id, user_id))
    _analysis_tasks.add(task)
    task.add_done_callback(_analysis_tasks.discard)


async def _resweep_stuck_analyses():
    """Startup sweep: re-queue any sessions left 'pending' by a previous process
    (a deploy/crash mid-generation would otherwise wedge them permanently)."""
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(UserChallengeSession).where(
                    UserChallengeSession.conversation_id.is_not(None),
                    UserChallengeSession.session_analysis.is_not(None),
                )
            )).scalars().all()
            requeued = 0
            for ucs in rows:
                blob = ucs.session_analysis or {}
                if blob.get("status") == "pending":
                    # Refresh the timestamp so concurrent pollers don't double-fire.
                    ucs.session_analysis = _pending_blob()
                    _spawn_analysis(ucs.conversation_id, ucs.user_id)
                    requeued += 1
            if requeued:
                await db.commit()
                log.info(f"[SESSION-ANALYSIS] re-queued {requeued} stuck pending analyses on startup")
    except Exception as e:
        log.error(f"[SESSION-ANALYSIS] startup sweep failed: {type(e).__name__}: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    env = os.getenv("ENVIRONMENT", "").strip().lower()
    jwt_secret = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
    if env in ("production", "prod"):
        if len(jwt_secret) < 32:
            raise RuntimeError(
                "JWT_SECRET must be at least 32 characters when ENVIRONMENT=production"
            )
    elif not os.getenv("HUSKY_TESTING") and len(jwt_secret) < 32:
        log.warning(
            "JWT_SECRET is shorter than 32 characters — use a long random secret in production "
            "(e.g. openssl rand -hex 32)."
        )
    await init_db()
    await seed_dev_platform_admin()
    await _sync_platform_admin_emails()
    log.info("Database initialized")
    await seed_challenges()
    await seed_demo_classroom()
    await seed_pilot_classroom()
    await _resweep_stuck_analyses()
    yield


app = FastAPI(title="Husky AI API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(challenges_router)
app.include_router(classrooms_router)
app.include_router(admin_router)

BASE_SYSTEM_PROMPT = (
    "You are an expert AI tutor helping students develop their AI prompting and reasoning skills. "
    "Be a thoughtful coach: guide users to think more deeply, ask clarifying questions, "
    "and help them reason through problems step by step. "
    "Provide concrete, specific feedback rather than generic praise."
)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


def _build_gemini_history(conversation_history: list) -> list:
    history = []
    for msg in conversation_history:
        role = "user" if msg["role"] == "user" else "model"
        history.append(
            types.Content(role=role, parts=[types.Part(text=msg["content"])])
        )
    return history


async def _save_turn(conversation_id: str, user_msg: str, assistant_msg: str, eval_data: dict, turn_num: int):
    try:
        async with AsyncSessionLocal() as db:
            db.add(Message(conversation_id=conversation_id, role="user", content=user_msg))
            db.add(Message(conversation_id=conversation_id, role="assistant", content=assistant_msg))
            scores = eval_data.get("scores", {})
            # Snapshot the user's research consent at this instant (per-turn, so it
            # survives mid-session toggles and resumed conversations).
            consent_now = False
            conv = await db.get(Conversation, conversation_id)
            if conv:
                owner = await db.get(User, conv.user_id)
                consent_now = bool(owner.consent_research) if owner else False
            db.add(EvalResult(
                conversation_id=conversation_id,
                turn_number=turn_num,
                pei=scores.get("PEI"),
                psq=scores.get("PSQ"),
                ccm=scores.get("CCM"),
                tsi=scores.get("TSI"),
                clm=scores.get("CLM"),
                ras=scores.get("RAS"),
                classification=eval_data.get("classification"),
                leading_status=eval_data.get("leading_status"),
                full_result=eval_data,
                consent_research=consent_now,
            ))
            res = await db.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(turn_count=turn_num)
            )
            if res.rowcount != 1:
                log.warning(
                    "turn_count update affected %s rows (expected 1) for conversation_id=%s",
                    res.rowcount,
                    conversation_id,
                )

            # Roll the new PEI into the challenge session's best_pei so the Husky Score and
            # challenge progress reflect the latest evaluation.
            new_pei = scores.get("PEI")
            if new_pei is not None:
                from sqlalchemy import select as sa_select
                ucs_q = await db.execute(
                    sa_select(UserChallengeSession).where(
                        UserChallengeSession.conversation_id == conversation_id
                    )
                )
                ucs = ucs_q.scalar_one_or_none()
                if ucs:
                    try:
                        pei_val = float(new_pei)
                    except (TypeError, ValueError):
                        pei_val = None
                    if pei_val is not None:
                        if ucs.best_pei is None or pei_val > ucs.best_pei:
                            ucs.best_pei = pei_val
                        if ucs.started_at is None:
                            ucs.started_at = datetime.utcnow()
                        if ucs.status == "not_started":
                            ucs.status = "in_progress"

            await db.commit()
    except Exception as e:
        log.error(f"DB save failed for turn {turn_num}: {e}")


async def _close_conversation(conversation_id: str):
    try:
        async with AsyncSessionLocal() as db:
            conv = await db.get(Conversation, conversation_id)
            if conv:
                conv.ended_at = datetime.utcnow()
                await db.commit()
    except Exception as e:
        log.error(f"Failed to close conversation: {e}")


async def _finalize_session(conversation_id: str) -> float | None:
    """End a conversation and finalize its linked challenge session (avg PEI +
    completed). Used for timer auto-end. Safe to call repeatedly. Returns the
    session average PEI rounded to 1 dp, or None."""
    try:
        async with AsyncSessionLocal() as db:
            conv = await db.get(Conversation, conversation_id)
            if not conv:
                return None
            avg_pei = (await db.execute(
                select(func.avg(EvalResult.pei)).where(
                    EvalResult.conversation_id == conversation_id,
                    EvalResult.pei.is_not(None),
                )
            )).scalar()
            if conv.ended_at is None:
                conv.ended_at = datetime.utcnow()
            ucs = (await db.execute(
                select(UserChallengeSession).where(
                    UserChallengeSession.conversation_id == conversation_id
                )
            )).scalar_one_or_none()
            schedule_analysis = False
            if ucs:
                if avg_pei is not None:
                    ucs.session_avg_pei = round(float(avg_pei), 2)
                ucs.status = "completed"
                ucs.completed_at = datetime.utcnow()
                # This path is only reached via timer/deadline finalization.
                if ucs.end_reason is None:
                    ucs.end_reason = "timer_expired"
                # Same background post-session analysis as the manual /end path.
                if (ucs.session_analysis or {}).get("status") not in ("ready", "pending"):
                    ucs.session_analysis = _pending_blob()
                    schedule_analysis = True
            await db.commit()
            if schedule_analysis:
                _spawn_analysis(conversation_id, conv.user_id)
            return round(float(avg_pei), 1) if avg_pei is not None else None
    except Exception as e:
        log.error(f"Failed to finalize session: {e}")
        return None


async def _build_system_prompt(challenge_id: str | None, session_num: int | None) -> tuple[str, dict | None]:
    """Return (system_prompt, session_data_dict) for the given challenge/session."""
    if not challenge_id:
        return BASE_SYSTEM_PROMPT, None

    try:
        async with AsyncSessionLocal() as db:
            ch = await db.get(Challenge, challenge_id)
            if not ch:
                return BASE_SYSTEM_PROMPT, None
            idx = (session_num or 1) - 1
            if idx < 0 or idx >= len(ch.sessions_data):
                idx = 0
            sd = ch.sessions_data[idx]
            extra = sd.get("system_prompt_extra", "")
            prompt = (
                f"You are an expert AI tutor coaching a student through the following challenge:\n\n"
                f"CHALLENGE: {ch.title}\n"
                f"SESSION {idx + 1}: {sd['title']}\n"
                f"GOAL: {sd['goal']}\n\n"
                f"CONTEXT FOR THIS SESSION:\n{sd['brief']}\n\n"
                f"YOUR COACHING ROLE:\n{extra}\n\n"
                f"Always stay in the context of this specific challenge and session. "
                f"Guide the student to think through the problem rather than just giving answers. "
                f"Ask probing questions. Celebrate good reasoning explicitly."
            )
            return prompt, sd
    except Exception as e:
        log.error(f"Failed to build challenge system prompt: {e}")
        return BASE_SYSTEM_PROMPT, None


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(None),
    challenge_id: str = Query(None),
    session_num: int = Query(None),
):
    if not token:
        await websocket.close(code=4001, reason="Authentication required")
        return
    user_id = decode_token(token)
    if not user_id:
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    await websocket.accept()

    system_prompt, session_data = await _build_system_prompt(challenge_id, session_num)
    chat_config = types.GenerateContentConfig(system_instruction=system_prompt)

    conversation_id = None
    conversation_history: list[dict] = []
    resumed = False
    session_is_completed = False
    # Timed-session snapshot (from the UserChallengeSession). None = untimed.
    session_time_limit = None
    session_min_turns = None
    session_started_at = None
    try:
        async with AsyncSessionLocal() as db:
            # Resume an existing challenge-session conversation when possible so chat
            # history is preserved across reconnects / page refreshes.
            if challenge_id and session_num:
                from sqlalchemy import select as sa_select
                result = await db.execute(
                    sa_select(UserChallengeSession).where(
                        UserChallengeSession.user_id == user_id,
                        UserChallengeSession.challenge_id == challenge_id,
                        UserChallengeSession.session_number == session_num,
                    )
                )
                ucs = result.scalar_one_or_none()
                if ucs:
                    session_time_limit = ucs.time_limit_minutes
                    session_min_turns = ucs.min_turns
                    session_started_at = ucs.started_at
                if ucs and ucs.conversation_id:
                    existing = await db.get(Conversation, ucs.conversation_id)
                    if existing and existing.user_id == user_id:
                        conversation_id = existing.id
                        resumed = True
                        session_is_completed = ucs.status == "completed"
                        # Only reopen the conversation if it was not explicitly ended
                        if existing.ended_at is not None and not session_is_completed:
                            existing.ended_at = None
                            await db.commit()
                        # Hydrate server-side history so the model has full context
                        mr = await db.execute(
                            sa_select(Message)
                            .where(Message.conversation_id == conversation_id)
                            .order_by(Message.created_at)
                        )
                        for m in mr.scalars().all():
                            conversation_history.append({"role": m.role, "content": m.content})

            if not conversation_id:
                conv = Conversation(user_id=user_id)
                db.add(conv)
                await db.commit()
                await db.refresh(conv)
                conversation_id = conv.id

                # Link conversation to challenge session if applicable (first connect)
                if challenge_id and session_num:
                    from sqlalchemy import select as sa_select
                    result = await db.execute(
                        sa_select(UserChallengeSession).where(
                            UserChallengeSession.user_id == user_id,
                            UserChallengeSession.challenge_id == challenge_id,
                            UserChallengeSession.session_number == session_num,
                        )
                    )
                    ucs = result.scalar_one_or_none()
                    if ucs:
                        session_time_limit = ucs.time_limit_minutes
                        session_min_turns = ucs.min_turns
                        session_started_at = ucs.started_at
                    if ucs and not ucs.conversation_id:
                        ucs.conversation_id = conversation_id
                        await db.commit()
    except Exception as e:
        log.error(f"Failed to create conversation record: {e}")

    # Timed-session deadline (None = untimed). Anchored to the server-side start time.
    session_deadline = (
        session_started_at + timedelta(minutes=session_time_limit)
        if (session_started_at and session_time_limit) else None
    )
    # Lazy finalize: a session left open past its deadline is ended on next connect.
    if conversation_id and session_deadline and not session_is_completed and datetime.utcnow() >= session_deadline:
        await _finalize_session(conversation_id)
        session_is_completed = True

    # Server-computed remaining time so the client never has to parse timestamps
    # or worry about clock skew. Recomputed fresh on every (re)connect, so refresh
    # resumes the same countdown.
    remaining_seconds = (
        max(0, int((session_deadline - datetime.utcnow()).total_seconds()))
        if session_deadline else None
    )

    # Always send conversation_id so the client can call the end-session REST endpoint
    if conversation_id:
        await websocket.send_text(json.dumps({
            "type": "session_init",
            "conversation_id": conversation_id,
            "time_limit_minutes": session_time_limit,
            "min_turns": session_min_turns,
            "remaining_seconds": remaining_seconds,
            "turn_count": len(conversation_history) // 2,
        }))

    # Send session context to client immediately if challenge mode
    if session_data:
        await websocket.send_text(json.dumps({
            "type": "challenge_context",
            "data": {
                "title": session_data.get("title"),
                "goal": session_data.get("goal"),
                "brief": session_data.get("brief"),
                "seed_question": session_data.get("seed_question"),
            }
        }))

    # Replay any prior messages from a resumed conversation so the client UI rehydrates
    if resumed and conversation_history:
        await websocket.send_text(json.dumps({
            "type": "history",
            "messages": conversation_history,
            "turn_count": len(conversation_history) // 2,
        }))

    # Tell the client the session is locked if it was explicitly ended
    if session_is_completed:
        await websocket.send_text(json.dumps({"type": "session_ended"}))

    client_host = websocket.client.host if websocket.client else "unknown"
    mode = f"challenge={challenge_id}/session={session_num}" if challenge_id else "free"
    log.info(
        f"[WS] User {user_id[:8]}... connected ({mode}) (conv: {conversation_id}) "
        f"resumed={resumed} prior_turns={len(conversation_history) // 2}"
    )

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)

            if data.get("type") != "message":
                log.debug(f"[WS] Ignoring non-message packet: type={data.get('type')}")
                continue

            user_content = data.get("content", "").strip()
            if not user_content:
                log.warning("[WS] Received empty message content, skipping")
                continue

            # Server-side timer enforcement: once past the deadline, refuse new
            # messages and finalize the session (defends against client tampering).
            if session_deadline and datetime.utcnow() >= session_deadline:
                await _finalize_session(conversation_id)
                await websocket.send_text(json.dumps({"type": "session_ended"}))
                log.info(f"[WS] Message rejected, session past deadline (conv: {conversation_id})")
                continue

            turn = len(conversation_history) // 2 + 1
            preview = user_content[:120]
            ellipsis = "..." if len(user_content) > 120 else ""
            log.info(f"[TURN {turn}] User ({len(user_content)} chars): {preview!r}{ellipsis}")

            gemini_history = _build_gemini_history(conversation_history)
            contents = gemini_history + [
                types.Content(role="user", parts=[types.Part(text=user_content)])
            ]

            conversation_history.append({"role": "user", "content": user_content})

            await websocket.send_text(json.dumps({"type": "typing"}))
            log.debug(f"[TURN {turn}] Sent 'typing' signal to client")

            # -- Chat streaming --
            full_response = ""
            chunk_count = 0
            last_chunk = None
            log.info(f"[TURN {turn}] Streaming chat -> gemini-2.5-pro (history depth: {len(gemini_history)})")

            try:
                async for chunk in await client.aio.models.generate_content_stream(
                    model="gemini-2.5-pro",
                    contents=contents,
                    config=chat_config,
                ):
                    text = chunk.text
                    if text:
                        full_response += text
                        chunk_count += 1
                        await websocket.send_text(json.dumps({
                            "type": "stream",
                            "content": text
                        }))
                    last_chunk = chunk

                usage = last_chunk.usage_metadata if last_chunk else None
                if usage:
                    log.info(
                        f"[TURN {turn}] Chat done -- "
                        f"chunks={chunk_count}, "
                        f"in={usage.prompt_token_count} tok, "
                        f"out={usage.candidates_token_count} tok, "
                        f"response={len(full_response)} chars"
                    )
                else:
                    log.info(f"[TURN {turn}] Chat done -- chunks={chunk_count}, response={len(full_response)} chars")

            except Exception as e:
                err_str = str(e)
                if "API_KEY_INVALID" in err_str or "API key not valid" in err_str:
                    log.error(f"[TURN {turn}] AUTH FAILED -- check GOOGLE_API_KEY")
                    msg = "Authentication failed -- is GOOGLE_API_KEY set correctly?"
                elif "quota" in err_str.lower() or "rate" in err_str.lower() or "429" in err_str:
                    log.warning(f"[TURN {turn}] Rate limited: {e}")
                    msg = "Rate limited. Please wait a moment and try again."
                elif "billing" in err_str.lower() or "credit" in err_str.lower():
                    log.error(f"[TURN {turn}] BILLING ISSUE: {e}")
                    msg = "Billing issue -- check your Google Cloud / AI Studio account."
                else:
                    log.error(f"[TURN {turn}] Chat stream error: {type(e).__name__}: {e}", exc_info=True)
                    msg = f"Chat error: {type(e).__name__}: {e}"
                await websocket.send_text(json.dumps({"type": "error", "message": msg}))
                conversation_history.pop()
                continue

            conversation_history.append({"role": "assistant", "content": full_response})

            await websocket.send_text(json.dumps({
                "type": "done",
                "full_response": full_response
            }))
            log.debug(f"[TURN {turn}] Sent 'done' to client")

            # -- Evaluation --
            await websocket.send_text(json.dumps({"type": "eval_start"}))
            log.info(f"[TURN {turn}] Starting eval (total history: {len(conversation_history)} msgs)")

            eval_result = None
            try:
                eval_result = await evaluate_conversation(conversation_history)
                scores = eval_result.get("scores", {})
                log.info(
                    f"[TURN {turn}] Eval -> "
                    f"PEI={scores.get('PEI', 0):.1f}  "
                    f"PSQ={scores.get('PSQ', 0):.1f}  "
                    f"CCM={scores.get('CCM', 0):.1f}  "
                    f"TSI={scores.get('TSI', 0):.1f}  "
                    f"CLM={scores.get('CLM', 0):.1f}  "
                    f"RAS={scores.get('RAS', 0):.1f}  "
                    f"| {eval_result.get('classification')} / {eval_result.get('leading_status')}"
                )
                log.debug(f"[TURN {turn}] Suggestions: {eval_result.get('suggestions', [])}")
                log.debug(f"[TURN {turn}] Red flags:   {eval_result.get('red_flags', [])}")
                # Persist before notifying the client so a fast disconnect cannot cancel the save.
                if conversation_id:
                    await _save_turn(conversation_id, user_content, full_response, eval_result, turn)
                await websocket.send_text(json.dumps({"type": "eval", "data": eval_result}))
            except Exception as e:
                log.error(f"[TURN {turn}] Eval error: {type(e).__name__}: {e}", exc_info=True)
                await websocket.send_text(json.dumps({
                    "type": "eval_error",
                    "message": str(e)
                }))

    except WebSocketDisconnect:
        log.info(f"[WS] User {user_id[:8]}... disconnected after {len(conversation_history) // 2} turns")
        if conversation_id:
            await _close_conversation(conversation_id)
    except Exception as e:
        log.error(f"[WS] Unexpected error: {type(e).__name__}: {e}", exc_info=True)
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass
        if conversation_id:
            await _close_conversation(conversation_id)


async def _generate_session_analysis(conversation_id: str, user_id: str):
    """
    Background task: build the post-session analysis for a completed session and
    store it on UserChallengeSession.session_analysis. Opens its own DB session
    (the request's session is already closed by the time this runs). Idempotent:
    skips if a "ready" analysis already exists; records {"status": "failed"} so
    the UI can stop polling and the next /end call can retry.
    """
    try:
        async with AsyncSessionLocal() as db:
            ucs_q = await db.execute(
                select(UserChallengeSession).where(
                    UserChallengeSession.conversation_id == conversation_id,
                    UserChallengeSession.user_id == user_id,
                )
            )
            ucs = ucs_q.scalar_one_or_none()
            if ucs is None:
                return
            if (ucs.session_analysis or {}).get("status") == "ready":
                return

            msgs = (await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at, Message.id)
            )).scalars().all()
            transcript = [{"role": m.role, "content": m.content} for m in msgs]

            # Order positionally by creation time (turn_number is unreliable across
            # resumed sessions); enumerate to give each turn a stable display index.
            evals = (await db.execute(
                select(EvalResult)
                .where(EvalResult.conversation_id == conversation_id)
                .order_by(EvalResult.created_at, EvalResult.id)
            )).scalars().all()
            per_turn = []
            for i, e in enumerate(evals, start=1):
                fr = e.full_result or {}
                per_turn.append({
                    "turn": i,
                    "pei": e.pei,
                    "scores": {"PSQ": e.psq, "CCM": e.ccm, "TSI": e.tsi, "CLM": e.clm, "RAS": e.ras},
                    "classification": e.classification,
                    "turn_summary": fr.get("turn_summary") or "",
                    # The concrete per-turn feedback the evaluator already produced.
                    # The session analyst consolidates these into session takeaways
                    # instead of inventing generic advice from scratch.
                    "suggestions": fr.get("suggestions") or [],
                    "red_flags": fr.get("red_flags") or [],
                })

            challenge_ctx = None
            ch = await db.get(Challenge, ucs.challenge_id)
            if ch is not None:
                challenge_ctx = {"title": ch.title, "objective": ch.description}

            analysis = await analyze_session(transcript, per_turn, challenge_ctx)

            # Re-fetch inside this session to attach the result to a live row.
            ucs.session_analysis = analysis
            await db.commit()
            log.info(f"[SESSION-ANALYSIS] stored for conversation {conversation_id[:8]}...")
    except Exception as e:
        log.error(f"[SESSION-ANALYSIS] failed for {conversation_id[:8]}...: {type(e).__name__}: {e}", exc_info=True)
        try:
            async with AsyncSessionLocal() as db2:
                ucs_q = await db2.execute(
                    select(UserChallengeSession).where(
                        UserChallengeSession.conversation_id == conversation_id,
                        UserChallengeSession.user_id == user_id,
                    )
                )
                ucs = ucs_q.scalar_one_or_none()
                if ucs is not None and (ucs.session_analysis or {}).get("status") != "ready":
                    ucs.session_analysis = {"status": "failed"}
                    await db2.commit()
        except Exception:
            pass


@app.post("/conversations/{conversation_id}/end")
async def end_conversation(
    conversation_id: str,
    user_id: str = Depends(get_current_user),
    db=Depends(get_db),
):
    """Mark a conversation as ended, calculate session avg PEI, and store it on the linked challenge session."""
    conv = await db.get(Conversation, conversation_id)
    if not conv or conv.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    ucs_result = await db.execute(
        select(UserChallengeSession).where(
            UserChallengeSession.conversation_id == conversation_id,
            UserChallengeSession.user_id == user_id,
        )
    )
    ucs = ucs_result.scalar_one_or_none()

    # Did the timer already lapse? Decided server-side so the recorded end_reason
    # can't be spoofed by the client, and reused by the min-turns gate below.
    past_deadline = bool(
        ucs
        and ucs.time_limit_minutes
        and ucs.started_at
        and datetime.utcnow() >= ucs.started_at + timedelta(minutes=ucs.time_limit_minutes)
    )

    # Min-turns gate: block an early manual end until the minimum turns are met,
    # unless the timer has already expired (the timer is a hard cap that wins).
    if ucs and ucs.min_turns:
        turns_done = conv.turn_count or 0
        if turns_done < ucs.min_turns and not past_deadline:
            raise HTTPException(
                status_code=400,
                detail=f"Send at least {ucs.min_turns} turns before ending (you have {turns_done}).",
            )

    result = await db.execute(
        select(func.avg(EvalResult.pei), func.count(EvalResult.id)).where(
            EvalResult.conversation_id == conversation_id,
            EvalResult.pei.is_not(None),
        )
    )
    row = result.one_or_none()
    avg_pei = row[0] if row else None
    turn_count = row[1] if row else 0

    conv.ended_at = datetime.utcnow()

    schedule_analysis = False
    if ucs:
        if avg_pei is not None:
            ucs.session_avg_pei = round(float(avg_pei), 2)
        ucs.status = "completed"
        ucs.completed_at = datetime.utcnow()
        # Record how it ended, server-decided. Don't overwrite a reason already
        # set by a prior finalize (e.g. timer auto-end that beat this call).
        if ucs.end_reason is None:
            ucs.end_reason = "timer_expired" if past_deadline else "manual"
        # Kick off the post-session analysis in the background unless one is
        # already done or in flight. Mark it "pending" now so the UI can poll.
        prior_status = (ucs.session_analysis or {}).get("status")
        if prior_status not in ("ready", "pending"):
            ucs.session_analysis = _pending_blob()
            schedule_analysis = True

    await db.commit()

    if schedule_analysis:
        _spawn_analysis(conversation_id, user_id)

    return {
        "session_avg_pei": round(float(avg_pei), 1) if avg_pei is not None else None,
        "turns": int(turn_count or 0),
        "analysis_status": (ucs.session_analysis or {}).get("status") if ucs else None,
        "end_reason": ucs.end_reason if ucs else None,
    }


@app.get("/conversations/{conversation_id}/analysis")
async def get_session_analysis(
    conversation_id: str,
    user_id: str = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return the stored post-session analysis for a session (the frontend polls
    this after /end until status is 'ready' or 'failed')."""
    conv = await db.get(Conversation, conversation_id)
    if not conv or conv.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    ucs_q = await db.execute(
        select(UserChallengeSession).where(
            UserChallengeSession.conversation_id == conversation_id,
            UserChallengeSession.user_id == user_id,
        )
    )
    ucs = ucs_q.scalar_one_or_none()
    if ucs is None or not ucs.session_analysis:
        return {"status": "none"}

    # Self-heal: if generation has been "pending" too long (worker died, deploy
    # mid-flight), re-queue it. Refresh the timestamp first so rapid polling
    # doesn't fire the task repeatedly within the staleness window.
    if ucs.session_analysis.get("status") == "pending" and _pending_is_stale(ucs.session_analysis):
        ucs.session_analysis = _pending_blob()
        await db.commit()
        _spawn_analysis(conversation_id, user_id)
        log.info(f"[SESSION-ANALYSIS] re-queued stale pending for {conversation_id[:8]}...")

    return ucs.session_analysis


@app.post("/conversations/{conversation_id}/analysis/retry")
async def retry_session_analysis(
    conversation_id: str,
    user_id: str = Depends(get_current_user),
    db=Depends(get_db),
):
    """Manually re-trigger generation (powers the 'Try again' button on a failed
    analysis). No-op if one is already ready or freshly generating."""
    conv = await db.get(Conversation, conversation_id)
    if not conv or conv.user_id != user_id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    ucs_q = await db.execute(
        select(UserChallengeSession).where(
            UserChallengeSession.conversation_id == conversation_id,
            UserChallengeSession.user_id == user_id,
        )
    )
    ucs = ucs_q.scalar_one_or_none()
    if ucs is None:
        raise HTTPException(status_code=404, detail="Session not found")

    blob = ucs.session_analysis or {}
    status = blob.get("status")
    # Re-run unless it's already done or actively generating (a stale pending is fair game).
    if status == "ready" or (status == "pending" and not _pending_is_stale(blob)):
        return {"status": status}

    ucs.session_analysis = _pending_blob()
    await db.commit()
    _spawn_analysis(conversation_id, user_id)
    return {"status": "pending"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
