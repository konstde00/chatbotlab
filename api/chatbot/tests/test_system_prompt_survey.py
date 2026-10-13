"""
Tests for survey context reaching the model.

Two units are covered:
  - context_for_prompt() (chatbot.services.survey) decides WHETHER a
    conversation's survey context applies, including the follow-up toggle.
  - generate_system_prompt() (chatbot.services.runchat) appends the rendered
    block after the bot's base prompt and its persona instructions.

Because every Utterance stores the prompt it was generated from, the last test
here checks the audit trail end to end.

Coverage:
  - Off by default: a bot with no preamble gets an unchanged prompt
  - Base prompt and persona instructions survive alongside the survey block
  - Questions and answers reach the prompt
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


# ── generate_system_prompt: how the context is rendered in ────────────────────


@pytest.mark.django_db
def test_a_disabled_bot_leaves_the_prompt_unchanged(make_bot, make_conversation):
    """A bot that never opted in behaves exactly as it did before this feature."""
    bot = make_bot(prompt="You are a test assistant.", survey_context_enabled=False)
    conversation = make_conversation(bot=bot, survey_context=ANSWERS)

    prompt = generate_system_prompt(bot, None, context_for_prompt(bot, conversation))
    assert prompt == "You are a test assistant."


@pytest.mark.django_db
def test_survey_context_is_appended_to_the_base_prompt(make_bot):
    bot = make_bot(prompt="You are a test assistant.", survey_context_preamble=PREAMBLE)
    prompt = generate_system_prompt(bot, None, ANSWERS)

    assert prompt.startswith("You are a test assistant.")
    assert PREAMBLE in prompt
    assert "Q: How stressed have you felt this week?" in prompt
    assert "A: Very stressed" in prompt


@pytest.mark.django_db
def test_survey_context_coexists_with_persona_instructions(make_bot):
    bot = make_bot(prompt="Base prompt.", survey_context_preamble=PREAMBLE)
    persona = Persona.objects.create(name="Chatty", instructions="Be warm.")
    prompt = generate_system_prompt(bot, persona, ANSWERS)

    assert "Base prompt." in prompt
    assert "Be warm." in prompt
    assert "A: Very stressed" in prompt


@pytest.mark.django_db
def test_no_answers_appends_nothing_even_with_a_preamble(make_bot):
    """A participant whose survey data never arrived gets the plain prompt."""
    bot = make_bot(prompt="Base prompt.", survey_context_preamble=PREAMBLE)
    assert generate_system_prompt(bot, None, None) == "Base prompt."


@pytest.mark.django_db
def test_existing_callers_are_unaffected(make_bot):
    """The third argument is optional, so pre-existing call sites still work."""
    bot = make_bot(prompt="Base prompt.", survey_context_preamble=PREAMBLE)
    assert generate_system_prompt(bot, None) == "Base prompt."


# ── {{RECALLED_SITUATION}}: answers placed inside the prompt ──────────────────

PLACEHOLDER_PROMPT = (
    "The participant previously wrote:\n\n"
    "<participant_context>\n{{RECALLED_SITUATION}}\n</participant_context>\n\n"
    "Use this information when it is relevant."
)


@pytest.mark.django_db
def test_placeholder_is_replaced_by_the_answers_in_place(make_bot):
    """A prompt with the placeholder keeps its wording; answers land inside it."""
    bot = make_bot(prompt=PLACEHOLDER_PROMPT)

    assert generate_system_prompt(bot, None, ANSWERS) == (
        "The participant previously wrote:\n\n"
        "<participant_context>\n"
        "Q: How stressed have you felt this week?\nA: Very stressed\n"
        "</participant_context>\n\n"
        "Use this information when it is relevant."
    )


@pytest.mark.django_db
def test_placeholder_takes_the_preamble_with_it(make_bot):
    """No answers appended twice: the whole block goes where the placeholder is."""
    bot = make_bot(
        prompt="Before {{RECALLED_SITUATION}} after.", survey_context_preamble="Intro:"
    )

    assert generate_system_prompt(bot, None, ANSWERS) == (
        "Before Intro:\n\nQ: How stressed have you felt this week?\nA: Very stressed after."
    )


@pytest.mark.django_db
def test_placeholder_is_removed_when_there_are_no_answers(make_bot):
    """The literal token must never reach the model, e.g. for a control bot."""
    bot = make_bot(prompt="Before {{RECALLED_SITUATION}} after.")

    assert generate_system_prompt(bot, None, None) == "Before  after."


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
    assert "A: Very stressed" in utterances[-1].instruction_prompt
