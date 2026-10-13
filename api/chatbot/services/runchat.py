import asyncio
import logging
import math
import os
import random

from asgiref.sync import sync_to_async
from django.core.cache import cache
from django.db import close_old_connections
from kani import Kani

from server.engine import get_or_create_engine_from_model

from ..models import Bot, Conversation, Utterance
from .db import db_retry
from .moderation import moderate_message
from .prompt import compile_prompt, generate_system_prompt  # noqa: F401
from .survey import context_for_prompt

logger = logging.getLogger(__name__)

engine_instances = {}


class ConversationNotFound(Exception):
    """
    Raised when a chat round targets a conversation_id that has no row in the DB
    (e.g. the client never called /api/initialize_conversation/, or it was
    deleted). The view turns this into a 400 instead of a generic 500 — a retry
    can't help, the conversation genuinely isn't there.
    """


class BotNotFound(Exception):
    """
    Raised when a chat round names a bot that has no row in the DB — renamed,
    deleted, or simply a typo in the client's bot_name. The view turns this into
    a 404, which is what /api/initialize_conversation/ and the avatar endpoints
    already return for the same condition; a retry can't help, the bot genuinely
    isn't there.

    Without this the ORM's Bot.DoesNotExist fell through to the view's generic
    handler and every mistyped bot name became a 500, which reads as a broken
    deployment rather than a bad request.
    """


async def _recycle_db_connections():
    """
    Release the request's DB connection on the thread-sensitive thread.

    Primary purpose is connection-count control: closing before the long LLM
    call frees the connection so peak concurrent connections stay low under load
    (CONN_MAX_AGE=0, so the next ORM call reopens). It does NOT reliably protect
    against stale connections — CONN_HEALTH_CHECKS is a no-op at CONN_MAX_AGE=0,
    and a connection can still be reaped during the request; stale-connection
    recovery is handled by _db_call()'s reconnect-and-retry instead.

    Skip if in an atomic block — Django TestCase wraps tests in a transaction and
    closing that connection would break the test's rollback mechanism.
    """

    def _recycle():
        from django.db import connection

        if not connection.in_atomic_block:
            close_old_connections()

    await sync_to_async(_recycle, thread_sensitive=True)()


async def _db_call(fn, *args, **kwargs):
    """
    Await a request-path ORM op on the thread-sensitive DB thread, with
    reconnect-and-retry on a stale connection.

    Thin async wrapper over db_retry() (see chatbot/services/db.py for the full
    rationale). The whole try/close/retry runs inside one sync_to_async call so
    close() and the retry execute on the same thread as the failed query.
    """

    def _call():
        return db_retry(fn, *args, **kwargs)

    return await sync_to_async(_call, thread_sensitive=True)()


_MOCK_LLM = os.getenv("MOCK_LLM", "false").lower() == "true"
_MOCK_LLM_P50_MS = int(os.getenv("MOCK_LLM_P50_MS", "900"))


async def save_chat_to_db(
    conversation_id,
    speaker_id,
    text,
    bot_name=None,
    participant_id=None,
    instruction_prompt=None,
    chat_history_used=None,
    compiled_prompt=None,
    moderation_category=None,
    moderation_scores=None,
):
    """
    Save chat messages asynchronously to the Utterance table.
    """
    try:
        conversation = await _db_call(
            Conversation.objects.get, conversation_id=conversation_id
        )

        # Debug logging for Bedrock engine save
        if bot_name and "bedrock" in bot_name.lower():
            logger.info("Saving Bedrock utterance to DB:")
            logger.info(f"  - conversation_id: {conversation_id}")
            logger.info(f"  - speaker_id: {speaker_id}")
            logger.info(
                f"  - instruction_prompt: {len(instruction_prompt) if instruction_prompt else 'None'}"
            )
            logger.info(
                f"  - chat_history_used: {len(chat_history_used) if chat_history_used else 'None'}"
            )

        await _db_call(
            Utterance.objects.create,
            conversation=conversation,
            speaker_id=speaker_id,
            bot_name=bot_name,
            participant_id=participant_id,
            text=text,
            instruction_prompt=instruction_prompt,
            chat_history_used=chat_history_used,
            compiled_prompt=compiled_prompt,
            moderation_category=moderation_category,
            moderation_scores=moderation_scores,
        )

        logger.info(f"Successfully saved utterance for conversation {conversation_id}")

    except Conversation.DoesNotExist:
        logger.warning(f"Conversation with ID {conversation_id} not found.")
    except Exception as e:
        logger.error(f"Failed to save message to Utterance table: {e}")
        import traceback

        traceback.print_exc()


async def run_chat_round(bot_name, conversation_id, participant_id, message):
    """
    Handles one full round of chat interaction: user -> bot response.
    Runs moderation on incoming message before processing.
    Returns (response_text, bot) so callers have the bot object without a second DB fetch.
    """
    # Prevent followup requests from being processed as regular user messages
    if message.startswith("[FOLLOW-UP REQUEST]"):
        logger.warning(
            f"Followup request detected in regular chat round, ignoring: {message[:50]}...",
        )
        return (
            "I'm sorry, but I can't process followup requests through the regular chat. Please use the appropriate followup mechanism.",
            None,
        )

    # Validate the connection before the first read. Under the async stack a
    # connection can persist across an idle gap and be reaped by RDS; reusing it
    # here was the source of intermittent (2006, 'Server has gone away') 500s on
    # the first query of a request after an idle period. Recycle so a stale socket
    # is dropped and this read opens a fresh connection.
    await _recycle_db_connections()

    # Fetch bot object with personas and ai_model prefetched
    try:
        bot = await _db_call(
            Bot.objects.prefetch_related("personas", "ai_model__provider").get,
            name=bot_name,
        )
    except Bot.DoesNotExist:
        # Distinct from a stale connection (which _db_call retries): the row
        # isn't there, so surface a clean 404 rather than a generic 500.
        raise BotNotFound(bot_name) from None

    # Moderate incoming message
    # Run in thread to avoid blocking
    # thread_sensitive=False: moderate_message uses time.sleep (mock) or an HTTP
    # call (real). Neither touches the DB, so don't tie up the single DB-thread.
    moderation = await sync_to_async(moderate_message, thread_sensitive=False)(
        message, bot
    )
    if moderation.category:
        # Prepare a generic warning — do NOT expose the category to the user
        warning_text = "Your message could not be processed. Please keep conversations respectful and constructive."
        # Save both user message and moderation response. Both rows carry the
        # category so the exchange is identifiable in the DB without matching
        # the warning text; the category itself never reaches the client. The
        # scores go on the user row only — that is the message they describe.
        await save_chat_to_db(
            conversation_id=conversation_id,
            speaker_id="user",
            text=message,
            bot_name=None,
            participant_id=participant_id,
            moderation_category=moderation.category,
            moderation_scores=moderation.scores,
        )
        await save_chat_to_db(
            conversation_id=conversation_id,
            speaker_id="assistant",
            text=warning_text,
            bot_name=bot.name,
            participant_id=None,
            instruction_prompt=bot.prompt,  # Use bot prompt for moderation responses
            moderation_category=moderation.category,
        )
        return warning_text, bot

    # Retrieve history from cache
    cache_key = f"conversation_cache_{conversation_id}"
    conversation_history = await cache.aget(cache_key, [])

    # If cache is empty, try to load from database
    if not conversation_history:
        try:
            conversation = await _db_call(
                Conversation.objects.get, conversation_id=conversation_id
            )
            utterances = await _db_call(
                list,
                Utterance.objects.filter(conversation=conversation).order_by(
                    "created_time",
                ),
            )

            # Build conversation history from database
            for utterance in utterances:
                role = "user" if utterance.speaker_id == "user" else "assistant"
                conversation_history.append({"role": role, "content": utterance.text})

            # Populate cache
            await cache.aset(cache_key, conversation_history, timeout=3600)
            logger.info(
                f"Loaded {len(conversation_history)} messages from database for conversation {conversation_id}",
            )
        except Exception as e:
            logger.warning(f"Failed to load conversation history from database: {e}")
            conversation_history = []

    # Apply transcript length limit to history only (before adding new message)
    if bot.max_transcript_length > 0:
        # Keep only the latest messages from history up to the limit
        conversation_history = conversation_history[-bot.max_transcript_length :]
        logger.info(
            f"Limited history to {len(conversation_history)} messages (max: {bot.max_transcript_length})",
        )
    elif bot.max_transcript_length == 0:
        # 0 means no chat history - clear history
        conversation_history = []
        logger.info("No chat history - using only current message")
    else:
        logger.info(
            f"No transcript limit applied, using all {len(conversation_history)} messages from history",
        )

    # Get the selected persona for this conversation
    try:
        conversation = await _db_call(
            Conversation.objects.select_related("selected_persona").get,
            conversation_id=conversation_id,
        )
    except Conversation.DoesNotExist:
        # Distinct from a stale connection: the row isn't there, so surface a
        # clean 400 rather than letting it fall through to a generic 500.
        raise ConversationNotFound(conversation_id) from None
    selected_persona = conversation.selected_persona

    prompt = compile_prompt(
        bot,
        selected_persona,
        context_for_prompt(bot, conversation),
        conversation_history,
        message,
    )
    system_prompt = prompt.system_prompt

    # Log the generated prompt for debugging
    logger.info(f"Bot '{bot.name}' system prompt:")
    logger.info(f"   Base prompt: {bot.prompt[:100] if bot.prompt else 'None'}...")
    logger.info(
        f"   Selected persona: {selected_persona.name if selected_persona and hasattr(selected_persona, 'name') else 'None'}",
    )
    logger.info(f"   Final prompt length: {len(system_prompt)} characters")

    # The history Kani is constructed with; the new message goes in as `query`.
    chat_history_json = prompt.history_json()

    # Release the DB connection before the long LLM/mock call.
    # Django 5.2 + asgiref ThreadSensitiveContext gives each request its own
    # dedicated thread, which holds a MySQL connection for the full request
    # lifetime. At 200 RPS with ~1.1s requests the system opens ~220 concurrent
    # connections, exceeding RDS db.t3.small's ~166 max. Closing here (all DB
    # reads are done; writes happen after) keeps peak connections under ~10.
    await _recycle_db_connections()

    if _MOCK_LLM:
        # Simulate realistic LLM latency without calling the API.
        # Redis, DB reads/writes, and all other infrastructure are still exercised.
        _delay = random.lognormvariate(math.log(_MOCK_LLM_P50_MS), 0.4) / 1000
        await asyncio.sleep(_delay)
        response_text = "This is a mock response for load testing."
    else:
        engine = get_or_create_engine_from_model(bot.ai_model, engine_instances)
        kani = Kani(
            engine, system_prompt=system_prompt, chat_history=prompt.kani_history()
        )
        response_text = ""

        async for msg in kani.full_round(query=prompt.query):
            if hasattr(msg, "text") and isinstance(msg.text, str):
                response_text += msg.text + " "

        response_text = response_text.strip()

        # Debug logging for Bedrock engine
        if bot.ai_model.provider.name == "Bedrock":
            logger.info(f"Bedrock engine response: '{response_text}'")
            logger.info(f"System prompt length: {len(system_prompt)}")
            logger.info(f"Chat history length: {len(chat_history_json)}")

    # Append the exchange for the next round
    conversation_history.append({"role": "user", "content": message})
    conversation_history.append({"role": "assistant", "content": response_text})
    await cache.aset(cache_key, conversation_history, timeout=3600)

    # Recycle connections again before the writes: the connection released above
    # was closed pre-LLM, but a connection opened lazily during the LLM round (or
    # reused after it) can be reaped by RDS while idle, so validate before writing.
    await _recycle_db_connections()

    # Save to DB (but not followup requests)
    if not message.startswith("[FOLLOW-UP REQUEST]"):
        await save_chat_to_db(
            conversation_id=conversation_id,
            speaker_id="user",
            text=message,
            bot_name=None,
            participant_id=participant_id,
        )

    await save_chat_to_db(
        conversation_id=conversation_id,
        speaker_id="assistant",
        text=response_text,
        bot_name=bot.name,
        participant_id=None,
        instruction_prompt=system_prompt,
        chat_history_used=chat_history_json,
        compiled_prompt=prompt.to_dict(),
    )

    return response_text, bot
