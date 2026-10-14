"""
Tests for survey context reaching the model.

Two units are covered:
  - context_for_prompt() (chatbot.services.survey) decides WHETHER a
    conversation's survey context applies, including the follow-up toggle.
  - generate_system_prompt() (chatbot.services.prompt) assembles the
    <instructions>, <persona> and <survey_context> blocks of the standard.

Because every Utterance stores the prompt it was generated from, the last test
here checks the audit trail end to end.

Coverage:
  - The full prompt matches the standard exactly
  - A block with nothing to say is omitted, never left empty
  - Admin text passes word for word; attribute values are escaped
  - Bot.survey_context_in_followup gates the follow-up path only
  - Utterance.instruction_prompt records the survey context
"""

from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async

from chatbot.models import Persona, Utterance
from chatbot.services.moderation import ModerationResult
from chatbot.services.runchat import generate_system_prompt, run_chat_round
from chatbot.services.survey import context_for_prompt

PREAMBLE = "The participant answered these questions before talking to you:"

ANSWERS = [
    {"question": "How stressed have you felt this week?", "answer": "Very stressed"},
]


# ── context_for_prompt: whether the context applies at all ────────────────────


@pytest.mark.django_db
def test_chat_always_uses_the_conversation_context(make_bot, make_conversation):
    bot = make_bot(survey_context_enabled=True, survey_context_in_followup=False)
    conversation = make_conversation(bot=bot, survey_context=ANSWERS)

    assert context_for_prompt(bot, conversation) == ANSWERS


@pytest.mark.django_db
def test_followup_uses_the_context_when_enabled(make_bot, make_conversation):
    bot = make_bot(survey_context_enabled=True, survey_context_in_followup=True)
    conversation = make_conversation(bot=bot, survey_context=ANSWERS)

    assert context_for_prompt(bot, conversation, is_followup=True) == ANSWERS


@pytest.mark.django_db
def test_followup_drops_the_context_when_disabled(make_bot, make_conversation):
    """The toggle affects follow-ups only — regular chat is unaffected."""
    bot = make_bot(survey_context_enabled=True, survey_context_in_followup=False)
    conversation = make_conversation(bot=bot, survey_context=ANSWERS)

    assert context_for_prompt(bot, conversation, is_followup=True) is None
    assert context_for_prompt(bot, conversation, is_followup=False) == ANSWERS


# ── generate_system_prompt: the system prompt standard ────────────────────────
# The format is specified in docs/llm-integration/system-prompt-format.rst.


@pytest.mark.django_db
def test_full_prompt_follows_the_standard(make_bot):
    """All three blocks, in the fixed order, pinned exactly."""
    bot = make_bot(prompt="Base prompt.", survey_context_preamble=PREAMBLE)
    persona = Persona.objects.create(name="Chatty", instructions="Be warm.")

    assert generate_system_prompt(bot, persona, ANSWERS) == (
        "<instructions>\n"
        "Base prompt.\n"
        "</instructions>\n"
        "\n"
        '<persona name="Chatty">\n'
        "Be warm.\n"
        "</persona>\n"
        "\n"
        "<survey_context>\n"
        f"<preamble>{PREAMBLE}</preamble>\n"
        "<answers>\n"
        "[\n"
        "  {\n"
        '    "question": "How stressed have you felt this week?",\n'
        '    "answer": "Very stressed"\n'
        "  }\n"
        "]\n"
        "</answers>\n"
        "</survey_context>"
    )


@pytest.mark.django_db
def test_a_disabled_bot_gets_instructions_only(make_bot, make_conversation):
    """No survey block at all — not an empty one — for a bot that opted out."""
    bot = make_bot(prompt="You are a test assistant.", survey_context_enabled=False)
    conversation = make_conversation(bot=bot, survey_context=ANSWERS)

    prompt = generate_system_prompt(bot, None, context_for_prompt(bot, conversation))
    assert prompt == "<instructions>\nYou are a test assistant.\n</instructions>"


@pytest.mark.django_db
def test_no_answers_adds_no_survey_block_even_with_a_preamble(make_bot):
    """A participant whose survey data never arrived gets instructions only."""
    bot = make_bot(prompt="Base prompt.", survey_context_preamble=PREAMBLE)
    assert generate_system_prompt(bot, None, None) == (
        "<instructions>\nBase prompt.\n</instructions>"
    )


@pytest.mark.django_db
def test_survey_block_follows_instructions_without_a_persona(make_bot):
    bot = make_bot(prompt="Base prompt.", survey_context_preamble=PREAMBLE)
    prompt = generate_system_prompt(bot, None, ANSWERS)

    assert prompt.startswith("<instructions>\nBase prompt.\n</instructions>\n\n")
    assert "<persona" not in prompt
    assert prompt.endswith("</survey_context>")


@pytest.mark.django_db
def test_persona_name_cannot_break_out_of_its_attribute(make_bot):
    bot = make_bot(prompt="Base prompt.")
    persona = Persona.objects.create(name='Odd "name" <x>', instructions="Hi.")

    assert '<persona name="Odd &quot;name&quot; &lt;x&gt;">' in (
        generate_system_prompt(bot, persona)
    )


@pytest.mark.django_db
def test_instructions_are_passed_word_for_word(make_bot):
    """Admin-written text is trusted: no escaping, so researchers' own tags survive."""
    text = "Line one.\n\n<rules>Be brief & kind.</rules>"
    bot = make_bot(prompt=text)

    assert generate_system_prompt(bot, None) == (
        f"<instructions>\n{text}\n</instructions>"
    )


# ── End to end: what gets recorded against each message ───────────────────────


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_instruction_prompt_records_the_survey_context(
    make_bot, make_conversation, mock_llm
):
    """
    The audit trail: the prompt stored against each message must contain the
    survey context, so a conversation can be reproduced after the fact.
    """
    bot = await sync_to_async(make_bot)(
        prompt="Base prompt.",
        survey_context_enabled=True,
        survey_context_preamble=PREAMBLE,
    )
    conversation = await sync_to_async(make_conversation)(
        bot=bot, survey_context=ANSWERS
    )

    with patch(
        "chatbot.services.runchat.moderate_message", return_value=ModerationResult()
    ):
        await run_chat_round(
            bot_name=bot.name,
            conversation_id=conversation.conversation_id,
            participant_id=conversation.participant_id,
            message="Hello",
        )

    utterances = await sync_to_async(list)(
        Utterance.objects.filter(conversation=conversation, speaker_id="assistant")
    )
    assert utterances, "expected an assistant utterance to be recorded"
    assert '"answer": "Very stressed"' in utterances[-1].instruction_prompt
