"""
Tests for compile_prompt(): everything the model sees, gathered into one typed
value before it is handed to Kani.

Kani is real here, not mocked — the point is to check what actually reaches
the engine. A fake engine records the messages it is asked to predict on.

Coverage:
  - The compiled value carries every input and serializes to JSON
  - The engine receives system prompt + history + query, query exactly once
  - chat_history_used is exactly the history Kani was constructed with
  - Both real rounds (chat, follow-up) send the new message once
  - Each bot message stores the full compiled prompt (Utterance.compiled_prompt)
"""

import json
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from django.core.cache import cache
from kani import ChatMessage, ChatRole, Kani
from kani.engines.base import BaseEngine, Completion

from chatbot.models import Persona, Utterance
from chatbot.services.followup import run_followup_chat_round
from chatbot.services.moderation import ModerationResult
from chatbot.services.prompt import compile_prompt
from chatbot.services.runchat import run_chat_round

PREAMBLE = "The participant answered these questions before talking to you:"
ANSWERS = [{"question": "Mood?", "answer": "Calm"}]
HISTORY = [
    {"role": "user", "content": "Hi"},
    {"role": "assistant", "content": "Hello!"},
]


class RecordingEngine(BaseEngine):
    """Replies "ok" and keeps the messages of every predict() call."""

    max_context_size = 100_000

    def __init__(self):
        self.calls = []

    def prompt_len(self, messages, functions=None, **kwargs):
        return 0

    async def predict(self, messages, functions=None, **hyperparams):
        self.calls.append(list(messages))
        return Completion(ChatMessage.assistant("ok"))


@pytest.fixture
def compiled(make_bot):
    bot = make_bot(prompt="Base.", survey_context_preamble=PREAMBLE)
    persona = Persona.objects.create(name="Warm", instructions="Be kind.")
    return compile_prompt(bot, persona, ANSWERS, HISTORY, "How are you?")


@pytest.mark.django_db
def test_compiled_prompt_round_trips_through_json(compiled):
    data = json.loads(compiled.to_json())

    assert data["base_prompt"] == "Base."
    assert data["persona"] == {"name": "Warm", "instructions": "Be kind."}
    assert data["survey_context"] == ANSWERS
    assert data["history"] == HISTORY
    assert data["query"] == "How are you?"
    assert data["system_prompt"].startswith("Base.")
    assert "Be kind." in data["system_prompt"]
    assert "A: Calm" in data["system_prompt"]


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_engine_sees_the_query_exactly_once(compiled):
    engine = RecordingEngine()
    kani = Kani(
        engine,
        system_prompt=compiled.system_prompt,
        chat_history=compiled.kani_history(),
    )
    async for _ in kani.full_round(query=compiled.query):
        pass

    sent = [(m.role, m.text) for m in engine.calls[0]]
    assert sent == [
        (ChatRole.SYSTEM, compiled.system_prompt),
        (ChatRole.USER, "Hi"),
        (ChatRole.ASSISTANT, "Hello!"),
        (ChatRole.USER, "How are you?"),
    ]


@pytest.mark.django_db
def test_history_json_matches_what_kani_was_given(compiled):
    given = [{"role": m.role.value, "content": m.text} for m in compiled.kani_history()]

    assert json.loads(compiled.history_json()) == given


# ── Call sites: the real chat and follow-up rounds ────────────────────────────


async def _run_round(round_fn, path, conversation, **kwargs):
    engine = RecordingEngine()
    with (
        patch(f"{path}.get_or_create_engine_from_model", return_value=engine),
        patch("chatbot.services.runchat._MOCK_LLM", False),
        patch(
            "chatbot.services.runchat.moderate_message",
            return_value=ModerationResult(),
        ),
    ):
        await round_fn(
            conversation_id=conversation.conversation_id,
            participant_id=conversation.participant_id,
            **kwargs,
        )
    return engine


async def _seed(make_bot, make_conversation):
    bot = await sync_to_async(make_bot)()
    conversation = await sync_to_async(make_conversation)(bot=bot)
    for m in HISTORY:
        await sync_to_async(Utterance.objects.create)(
            conversation=conversation, speaker_id=m["role"], text=m["content"]
        )
    await cache.adelete(f"conversation_cache_{conversation.conversation_id}")
    return bot, conversation


async def _last_history_used(conversation):
    u = await sync_to_async(
        Utterance.objects.filter(conversation=conversation, speaker_id="assistant")
        .order_by("-created_time")
        .first
    )()
    return json.loads(u.chat_history_used)


async def _last_compiled_prompt(conversation):
    u = await sync_to_async(
        Utterance.objects.filter(conversation=conversation, speaker_id="assistant")
        .order_by("-created_time")
        .first
    )()
    return u.compiled_prompt, u.instruction_prompt


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_chat_round_sends_message_once_and_records_it(
    make_bot, make_conversation
):
    bot, conversation = await _seed(make_bot, make_conversation)

    engine = await _run_round(
        run_chat_round,
        "chatbot.services.runchat",
        conversation,
        bot_name=bot.name,
        message="How are you?",
    )

    sent = [(m.role.value, m.text) for m in engine.calls[0][1:]]
    assert sent == [
        *((m["role"], m["content"]) for m in HISTORY),
        ("user", "How are you?"),
    ]
    assert await _last_history_used(conversation) == HISTORY

    compiled, instruction_prompt = await _last_compiled_prompt(conversation)
    assert compiled["history"] == HISTORY
    assert compiled["query"] == "How are you?"
    assert compiled["system_prompt"] == instruction_prompt


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_followup_round_sends_instruction_once_and_records_it(
    make_bot, make_conversation
):
    bot, conversation = await _seed(make_bot, make_conversation)

    engine = await _run_round(
        run_followup_chat_round,
        "chatbot.services.followup",
        conversation,
        bot_name=bot.name,
        followup_instruction="[FOLLOW-UP REQUEST] nudge",
    )

    sent = [(m.role.value, m.text) for m in engine.calls[0][1:]]
    assert sent == [
        *((m["role"], m["content"]) for m in HISTORY),
        ("user", "[FOLLOW-UP REQUEST] nudge"),
    ]
    assert await _last_history_used(conversation) == HISTORY

    compiled, instruction_prompt = await _last_compiled_prompt(conversation)
    assert compiled["history"] == HISTORY
    assert compiled["query"] == "[FOLLOW-UP REQUEST] nudge"
    assert compiled["system_prompt"] == instruction_prompt
