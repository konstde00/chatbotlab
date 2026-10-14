"""
Tests for Bot.survey_context_enabled

Survey context used to be switched on by typing something into
survey_context_preamble, which made one field do two jobs: a preamble left
blank by accident was indistinguishable from a bot deliberately configured not
to use survey answers. In a factorial study with a bot per cell, that silently
turns a treatment participant into a control one.

The checkbox is now the switch and the preamble is only wording.

Coverage:
  - A new bot has survey context off
  - Disabled wins, whatever the preamble says
  - Enabled with a preamble renders the preamble and the answers
  - Enabled with no preamble renders the answers alone
  - The follow-up toggle still applies, and only when enabled
"""

import pytest

from chatbot.services.runchat import generate_system_prompt
from chatbot.services.survey import context_for_prompt, render_survey_context

PREAMBLE = "The participant answered these questions before talking to you:"

ANSWERS = [
    {"question": "What's been on your mind lately?", "answer": "My goldfish Wallace"},
]


# ── The switch ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_survey_context_is_off_for_a_new_bot(make_bot):
    """Opting in is deliberate; nothing changes for a bot nobody configured."""
    assert make_bot().survey_context_enabled is False


@pytest.mark.django_db
def test_disabled_bot_gets_no_context_even_with_a_preamble(make_bot, make_conversation):
    """
    A preamble left behind from an earlier design, or pasted in by mistake,
    must not switch the feature on by itself.
    """
    bot = make_bot(survey_context_enabled=False, survey_context_preamble=PREAMBLE)
    conversation = make_conversation(bot=bot, survey_context=ANSWERS)

    assert context_for_prompt(bot, conversation) is None


@pytest.mark.django_db
def test_enabled_bot_gets_the_context(make_bot, make_conversation):
    bot = make_bot(survey_context_enabled=True, survey_context_preamble=PREAMBLE)
    conversation = make_conversation(bot=bot, survey_context=ANSWERS)

    assert context_for_prompt(bot, conversation) == ANSWERS


@pytest.mark.django_db
def test_enabled_with_no_preamble_still_gets_the_context(make_bot, make_conversation):
    """An empty preamble means no heading, not "switched off"."""
    bot = make_bot(survey_context_enabled=True, survey_context_preamble="")
    conversation = make_conversation(bot=bot, survey_context=ANSWERS)

    assert context_for_prompt(bot, conversation) == ANSWERS


# ── The preamble is wording only ──────────────────────────────────────────────


def test_preamble_is_rendered_above_the_answers():
    rendered = render_survey_context(PREAMBLE, ANSWERS)
    assert rendered.index(f"<preamble>{PREAMBLE}</preamble>") < rendered.index(
        "<answers>"
    )


@pytest.mark.parametrize("preamble", ["", "   ", None])
def test_no_preamble_renders_the_answers_without_a_heading(preamble):
    """
    No invented wording: a study controls its prompts precisely, so an absent
    preamble omits the heading rather than substituting one of our own.
    """
    rendered = render_survey_context(preamble, ANSWERS)
    assert "<preamble>" not in rendered
    assert '"answer": "My goldfish Wallace"' in rendered


@pytest.mark.parametrize("answers", [[], None, "not a list"])
def test_no_answers_renders_nothing_whatever_the_preamble(answers):
    assert render_survey_context(PREAMBLE, answers) == ""


# ── In the assembled prompt ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_prompt_is_unchanged_when_disabled(make_bot, make_conversation):
    bot = make_bot(
        prompt="Base prompt.",
        survey_context_enabled=False,
        survey_context_preamble=PREAMBLE,
    )
    conversation = make_conversation(bot=bot, survey_context=ANSWERS)

    prompt = generate_system_prompt(bot, None, context_for_prompt(bot, conversation))
    assert prompt == "<instructions>\nBase prompt.\n</instructions>"


@pytest.mark.django_db
def test_prompt_carries_the_answers_when_enabled(make_bot, make_conversation):
    bot = make_bot(
        prompt="Base prompt.",
        survey_context_enabled=True,
        survey_context_preamble=PREAMBLE,
    )
    conversation = make_conversation(bot=bot, survey_context=ANSWERS)

    prompt = generate_system_prompt(bot, None, context_for_prompt(bot, conversation))
    assert "Base prompt." in prompt
    assert PREAMBLE in prompt
    assert '"answer": "My goldfish Wallace"' in prompt


# ── Follow-ups ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_followup_toggle_applies_only_when_enabled(make_bot, make_conversation):
    bot = make_bot(survey_context_enabled=True, survey_context_in_followup=False)
    conversation = make_conversation(bot=bot, survey_context=ANSWERS)

    assert context_for_prompt(bot, conversation, is_followup=True) is None
    assert context_for_prompt(bot, conversation, is_followup=False) == ANSWERS


@pytest.mark.django_db
def test_followup_toggle_cannot_switch_a_disabled_bot_on(make_bot, make_conversation):
    bot = make_bot(survey_context_enabled=False, survey_context_in_followup=True)
    conversation = make_conversation(bot=bot, survey_context=ANSWERS)

    assert context_for_prompt(bot, conversation, is_followup=True) is None
