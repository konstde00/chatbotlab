import secrets

from django.core.exceptions import FieldDoesNotExist
from django.db import models

# Every category omni-moderation-latest scores, in OpenAI's own spelling. Adding
# a category here is not enough on its own — it also needs the matching
# `moderation_<name>` field on Bot, or it stays unreachable at 1.0.
MODERATION_CATEGORIES = (
    "harassment",
    "harassment/threatening",
    "hate",
    "hate/threatening",
    "illicit",
    "illicit/violent",
    "self-harm",
    "self-harm/instructions",
    "self-harm/intent",
    "sexual",
    "sexual/minors",
    "violence",
    "violence/graphic",
)


def moderation_field_name(category):
    """Map an OpenAI category onto its Bot threshold field.

    "harassment/threatening" -> "moderation_harassment_threatening"
    "self-harm"              -> "moderation_self_harm"
    """
    return "moderation_" + category.replace("/", "_").replace("-", "_")


class Persona(models.Model):
    name = models.CharField(max_length=255, unique=True)
    instructions = models.TextField(help_text="Instructions for this persona")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ["name"]


class Conversation(models.Model):
    conversation_id = models.CharField(max_length=255, unique=True)  # Conversation ID
    bot_name = models.CharField(max_length=255, default="DefaultBot")  # Bot Name
    bot_config = models.TextField(null=True, blank=True)  # Bot Config
    participant_id = models.CharField(max_length=255)
    initial_utterance = models.CharField(max_length=255, null=True, blank=True)
    study_name = models.CharField(max_length=255, null=True, blank=True)
    user_group = models.CharField(max_length=255, null=True, blank=True)
    survey_id = models.CharField(max_length=255, null=True, blank=True)  # Survey ID
    survey_meta_data = models.TextField(
        null=True,
        blank=True,
    )  # Survey metadata (can be long)
    started_time = models.DateTimeField(auto_now_add=True)  # Start time

    # Track which persona was randomly selected for this conversation
    selected_persona = models.ForeignKey(
        Persona,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
        help_text="The persona randomly selected for this conversation",
    )
    survey_context = models.JSONField(
        null=True,
        blank=True,
        help_text="Snapshot of the participant's survey answers as used by THIS conversation, copied from SurveyResponse at init. A copy rather than a link, so the record stays reproducible if the survey tool later overwrites the answers. Null means no answers were found for this participant.",
    )

    def __str__(self):
        return f"Conversation {self.conversation_id} started at {self.started_time}"


class Utterance(models.Model):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        # Unique identifier per conversation
        related_name="utterances",
        null=True,
        blank=True,
    )
    speaker_id = models.CharField(max_length=255)  # 'participant' or 'bot'
    bot_name = models.CharField(max_length=255, null=True, blank=True)
    participant_id = models.CharField(max_length=255, null=True, blank=True)
    created_time = models.DateTimeField(auto_now_add=True)  # Timestamp
    text = models.TextField()

    # new fields added for voice chat
    audio_file = models.FileField(
        upload_to="utterance_audio/",
        null=True,
        blank=True,
    )  # path to saved audio
    # to distinguish voice vs text utterances
    is_voice = models.BooleanField(default=False)

    # Store the instruction prompt that was passed to the LLM for this utterance
    instruction_prompt = models.TextField(
        null=True,
        blank=True,
        help_text="The instruction prompt (bot prompt + persona) that was passed to the LLM for this utterance",
    )

    # Store the chat history that was passed to the LLM for this utterance
    chat_history_used = models.TextField(
        null=True,
        blank=True,
        help_text="The chat history (formatted as JSON) that was actually passed to the LLM for this utterance",
    )
    compiled_prompt = models.JSONField(
        null=True,
        blank=True,
        help_text="Everything sent to the LLM for this utterance: base prompt, persona, survey context, history, query and the rendered system prompt",
    )

    # Set on BOTH rows of a blocked exchange — the user message that tripped
    # moderation and the canned warning sent in reply — so a single
    # `moderation_category__isnull=False` filter returns the complete event.
    # Null on every utterance that was not moderated.
    moderation_category = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="Moderation category that blocked this exchange (e.g. 'harassment'); null if the message was not blocked",
    )

    # Only on the user row of a blocked exchange — the scores describe the
    # message that was actually scored. Kept in full, including the categories
    # that passed, so thresholds can be re-tuned against real traffic later.
    moderation_scores = models.JSONField(
        null=True,
        blank=True,
        help_text="Full moderation category→score map for the blocked message; null if the message was not blocked",
    )

    def __str__(self):
        return f"{self.speaker_id}: {self.text[:50]}"


class ModelProvider(models.Model):
    """Model provider (e.g., OpenAI, Anthropic)"""

    name = models.CharField(max_length=255, unique=True)
    display_name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.display_name

    class Meta:
        ordering = ["name"]

    @classmethod
    def get_or_create_default_providers(cls):
        """Create default providers if they don't exist"""
        providers_data = {
            "OpenAI": {
                "display_name": "OpenAI",
                "description": "OpenAI's language models including GPT series",
            },
            "Anthropic": {
                "display_name": "Anthropic",
                "description": "Anthropic's Claude language models",
            },
            "Bedrock": {
                "display_name": "Amazon Bedrock",
                "description": "Amazon Bedrock foundation models including Llama3",
            },
        }

        providers = {}
        for name, data in providers_data.items():
            provider, _ = cls.objects.get_or_create(
                name=name,
                defaults=data,
            )
            providers[name] = provider
        return providers


class Model(models.Model):
    """AI model with capabilities and provider relationship"""

    provider = models.ForeignKey(
        ModelProvider,
        on_delete=models.CASCADE,
        related_name="models",
    )
    model_id = models.CharField(
        max_length=255,
        help_text="The actual model ID used by the provider",
    )
    display_name = models.CharField(max_length=255, help_text="Human-readable name")
    description = models.TextField(blank=True)
    capabilities = models.JSONField(
        default=list,
        help_text="List of capabilities like ['Chat', 'Reasoning', 'Code']",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.provider.display_name} - {self.display_name}"

    class Meta:
        unique_together = ["provider", "model_id"]
        ordering = ["provider__name", "display_name"]

    @classmethod
    def get_or_create_default_models(cls):
        """Create default models if they don't exist"""
        # First ensure providers exist
        providers = ModelProvider.get_or_create_default_providers()

        models_data = {
            "OpenAI": [
                {
                    "model_id": "gpt-5",
                    "display_name": "GPT-5",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "gpt-5-mini",
                    "display_name": "GPT-5 Mini",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "gpt-5-nano",
                    "display_name": "GPT-5 Nano",
                    "capabilities": ["Chat", "Basic Reasoning", "Code"],
                },
                {
                    "model_id": "gpt-4o",
                    "display_name": "GPT-4o",
                    "capabilities": ["Chat", "Vision", "Audio", "Reasoning"],
                },
                {
                    "model_id": "gpt-4o-mini",
                    "display_name": "GPT-4o Mini",
                    "capabilities": ["Chat", "Vision", "Audio", "Reasoning"],
                },
                {
                    "model_id": "gpt-4.1",
                    "display_name": "GPT-4.1",
                    "capabilities": ["Chat", "Reasoning", "Analysis"],
                },
                {
                    "model_id": "gpt-4.1-mini",
                    "display_name": "GPT-4.1 Mini",
                    "capabilities": ["Chat", "Reasoning", "Analysis"],
                },
                {
                    "model_id": "gpt-4.1-nano",
                    "display_name": "GPT-4.1 Nano",
                    "capabilities": ["Chat", "Basic Reasoning"],
                },
                {
                    "model_id": "gpt-4-turbo",
                    "display_name": "GPT-4 Turbo",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "gpt-4-turbo-preview",
                    "display_name": "GPT-4 Turbo Preview",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "gpt-3.5-turbo",
                    "display_name": "GPT-3.5 Turbo",
                    "capabilities": ["Chat", "Code", "Analysis"],
                },
            ],
            "Anthropic": [
                {
                    "model_id": "claude-opus-4-1-20250805",
                    "display_name": "Claude Opus 4.1",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "claude-opus-4-20250514",
                    "display_name": "Claude Opus 4",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "claude-sonnet-4-20250514",
                    "display_name": "Claude Sonnet 4",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "claude-3-5-haiku-20241022",
                    "display_name": "Claude 3.5 Haiku",
                    "capabilities": ["Chat", "Basic Reasoning", "Code"],
                },
                {
                    "model_id": "claude-3-7-sonnet-20250219",
                    "display_name": "Claude 3.7 Sonnet",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "claude-3-haiku-20240307",
                    "display_name": "Claude 3 Haiku",
                    "capabilities": ["Chat", "Basic Reasoning", "Code"],
                },
            ],
            "Bedrock": [
                {
                    "model_id": "meta.llama3-8b-instruct-v1:0",
                    "display_name": "Meta Llama 3 8B Instruct",
                    "capabilities": ["Chat", "Basic Reasoning", "Code"],
                },
                {
                    "model_id": "meta.llama3-70b-instruct-v1:0",
                    "display_name": "Meta Llama 3 70B Instruct",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "anthropic.claude-3-haiku-20240307-v1:0",
                    "display_name": "Anthropic Claude 3 Haiku",
                    "capabilities": ["Chat", "Basic Reasoning"],
                },
                {
                    "model_id": "anthropic.claude-3-sonnet-20240229-v1:0",
                    "display_name": "Anthropic Claude 3 Sonnet",
                    "capabilities": ["Chat", "Reasoning", "Code"],
                },
                {
                    "model_id": "anthropic.claude-3-5-sonnet-20240620-v1:0",
                    "display_name": "Anthropic Claude 3.5 Sonnet",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "us.anthropic.claude-sonnet-4-20250514-v1:0",
                    "display_name": "Anthropic Claude Sonnet 4",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "us.anthropic.claude-opus-4-1-20250805-v1:0",
                    "display_name": "Anthropic Claude Opus 4.1",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                    "display_name": "Anthropic Claude Sonnet 4.5",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                    "display_name": "Anthropic Claude Haiku 4.5",
                    "capabilities": ["Chat", "Basic Reasoning", "Code"],
                },
                {
                    "model_id": "us.anthropic.claude-opus-4-5-20251101-v1:0",
                    "display_name": "Anthropic Claude Opus 4.5",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "us.anthropic.claude-opus-4-6-v1",
                    "display_name": "Anthropic Claude Opus 4.6",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "us.anthropic.claude-sonnet-4-6",
                    "display_name": "Anthropic Claude Sonnet 4.6",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "us.anthropic.claude-opus-4-7",
                    "display_name": "Anthropic Claude Opus 4.7",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
                {
                    "model_id": "us.anthropic.claude-opus-4-8",
                    "display_name": "Anthropic Claude Opus 4.8",
                    "capabilities": ["Chat", "Reasoning", "Code", "Analysis"],
                },
            ],
        }

        created_models = []
        for provider_name, models_list in models_data.items():
            provider = providers[provider_name]
            for model_data in models_list:
                model, created = cls.objects.get_or_create(
                    provider=provider,
                    model_id=model_data["model_id"],
                    defaults={
                        "display_name": model_data["display_name"],
                        "capabilities": model_data["capabilities"],
                    },
                )
                if created:
                    created_models.append(model)

        return created_models


class ModerationSettings(models.Model):
    enabled = models.BooleanField(
        default=True,
        help_text="When disabled, all content moderation is bypassed globally",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Global Moderation Setting"
        verbose_name_plural = "Global Moderation Settings"

    def save(self, *args, **kwargs):
        # Enforce singleton pattern
        if not self.pk and ModerationSettings.objects.exists():
            raise ValueError("Only one ModerationSettings record is allowed")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Moderation {'Enabled' if self.enabled else 'Disabled'}"


class Bot(models.Model):
    # Make name the unique identifier
    name = models.CharField(max_length=255, unique=True, default="DefaultBotName")
    prompt = models.TextField()  # Bot's prompt
    # Use foreign key to Model instead of separate model_type and model_id
    # Keep old fields for migration compatibility
    model_type = models.CharField(
        max_length=255,
        default="OpenAI",
        null=True,
        blank=True,
    )
    model_id = models.CharField(
        max_length=255,
        default="gpt-4o-mini",
        null=True,
        blank=True,
    )
    ai_model = models.ForeignKey(
        Model,
        on_delete=models.CASCADE,
        related_name="bots",
        null=False,
        blank=False,
    )
    initial_utterance = models.TextField(blank=True, null=True)

    # New Column:
    AVATAR_CHOICES = [
        ("none", "None"),
        ("default", "Default"),
        ("user", "User Provided"),
    ]
    avatar_type = models.CharField(
        max_length=20,
        choices=AVATAR_CHOICES,
        default="none",
    )

    # Avatar generation prompt
    avatar_prompt = models.TextField(
        blank=True,
        null=True,
        help_text="Prompt used for generating bot avatars. If empty, will use default prompt.",
    )

    # Message chunking control (bot-specific)
    chunk_messages = models.BooleanField(
        default=True,
        help_text="If true, split responses into human-like chunks; if false, send as one blob",
    )

    # Humanlike delay control (bot-specific)
    humanlike_delay = models.BooleanField(
        default=True,
        help_text="If true, apply human-like typing delays; if false, show messages instantly",
    )

    # NEW: Humanlike delay configuration (bot-specific) - all in seconds
    # Reading parameters
    reading_words_per_minute = models.FloatField(
        default=250.0,
        help_text="Words per minute reading speed for calculating reading delay",
    )
    reading_jitter_min = models.FloatField(
        default=0.1,
        help_text="Minimum reading jitter in seconds",
    )
    reading_jitter_max = models.FloatField(
        default=0.3,
        help_text="Maximum reading jitter in seconds",
    )
    reading_thinking_min = models.FloatField(
        default=0.2,
        help_text="Minimum reading thinking time in seconds",
    )
    reading_thinking_max = models.FloatField(
        default=0.5,
        help_text="Maximum reading thinking time in seconds",
    )

    # Writing parameters
    writing_words_per_minute = models.FloatField(
        default=200.0,
        help_text="Words per minute writing speed for calculating writing delay",
    )
    writing_jitter_min = models.FloatField(
        default=0.05,
        help_text="Minimum writing jitter in seconds",
    )
    writing_jitter_max = models.FloatField(
        default=0.15,
        help_text="Maximum writing jitter in seconds",
    )
    writing_thinking_min = models.FloatField(
        default=0.1,
        help_text="Minimum writing thinking time in seconds",
    )
    writing_thinking_max = models.FloatField(
        default=0.3,
        help_text="Maximum writing thinking time in seconds",
    )

    # Intra-message delays
    intra_message_delay_min = models.FloatField(
        default=0.1,
        help_text="Minimum delay between message parts in seconds",
    )
    intra_message_delay_max = models.FloatField(
        default=0.3,
        help_text="Maximum delay between message parts in seconds",
    )
    min_reading_delay = models.FloatField(
        default=1.0,
        help_text="Minimum reading delay in seconds",
    )

    # Follow-up on idle settings
    follow_up_on_idle = models.BooleanField(
        default=False,
        help_text="If true, bot will send follow-up messages when user is idle",
    )
    idle_time_minutes = models.IntegerField(
        default=2,
        help_text="Minutes of inactivity before considering user idle",
    )
    follow_up_instruction_prompt = models.TextField(
        blank=True,
        null=True,
        help_text="Instructions for generating follow-up messages when user is idle",
    )
    recurring_followup = models.BooleanField(
        default=False,
        help_text="If true, bot will keep sending follow-up messages while user is idle. If false, bot will only send one follow-up per idle period.",
    )

    # Survey context — answers collected before the conversation (see
    # chatbot/services/survey.py and docs/survey-integration/qualtrics.rst)
    survey_context_enabled = models.BooleanField(
        default=False,
        help_text="Give this bot the participant's pre-conversation survey answers. This checkbox is the switch: a blank preamble below does NOT turn it off. Leave unticked for a control condition.",
    )
    survey_context_preamble = models.TextField(
        blank=True,
        default="",
        help_text="Optional sentence introducing the answers to the model, e.g. 'The participant answered these questions before talking to you:'. Leave blank to pass the answers with no introduction. This does not switch the feature on or off — the checkbox above does.",
    )
    survey_context_in_followup = models.BooleanField(
        default=True,
        help_text="If true, survey context is also included when generating idle follow-up messages. Only has an effect when a survey context preamble is set.",
    )

    # Transcript length control
    max_transcript_length = models.IntegerField(
        default=-1,
        help_text="Maximum number of messages to include in chat history. 0 = no chat history (only current message), 1+ = include that many most recent messages, negative = unlimited history.",
    )

    # Moderation settings - individual fields for each category
    moderation_harassment = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0.50,
        help_text="Harassment threshold (0.0-1.0, lower = stricter)",
    )
    moderation_harassment_threatening = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0.10,
        help_text="Harassment/threatening threshold (0.0-1.0, lower = stricter)",
    )
    moderation_hate = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0.50,
        help_text="Hate threshold (0.0-1.0, lower = stricter)",
    )
    moderation_hate_threatening = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0.10,
        help_text="Hate/threatening threshold (0.0-1.0, lower = stricter)",
    )
    # Added after the fact: omni-moderation has scored these since launch, but
    # without the fields there was no threshold to compare against and both
    # categories were silently unenforceable.
    moderation_illicit = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0.50,
        help_text="Illicit behaviour threshold (0.0-1.0, lower = stricter)",
    )
    moderation_illicit_violent = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0.10,
        help_text="Illicit/violent threshold (0.0-1.0, lower = stricter)",
    )
    moderation_self_harm = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0.20,
        help_text="Self-harm threshold (0.0-1.0, lower = stricter)",
    )
    moderation_self_harm_instructions = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0.50,
        help_text="Self-harm/instructions threshold (0.0-1.0, lower = stricter)",
    )
    moderation_self_harm_intent = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0.70,
        help_text="Self-harm/intent threshold (0.0-1.0, lower = stricter)",
    )
    moderation_sexual = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0.50,
        help_text="Sexual threshold (0.0-1.0, lower = stricter)",
    )
    moderation_sexual_minors = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0.20,
        help_text="Sexual/minors threshold (0.0-1.0, lower = stricter)",
    )
    moderation_violence = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0.70,
        help_text="Violence threshold (0.0-1.0, lower = stricter)",
    )
    moderation_violence_graphic = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0.80,
        help_text="Violence/graphic threshold (0.0-1.0, lower = stricter)",
    )

    # Many-to-many relationship with personas
    personas = models.ManyToManyField(
        Persona,
        blank=True,
        related_name="bots",
        help_text="Select personas that this bot should embody",
    )

    def __str__(self):
        return self.name

    def get_moderation_threshold(self, category):
        """Threshold for one OpenAI moderation category.

        The field name is derived rather than looked up in a hand-maintained
        map. That map was the reason `illicit` and `illicit/violent` went
        unenforced: OpenAI added the categories, nobody added the entries, and
        both silently fell through to the unreachable 1.0 default. Deriving the
        name means declaring the Bot field is enough to make a category work.

        Unknown categories still return 1.0 so an unrecognised one can never
        block on its own; moderate_message logs them so they get noticed.
        """
        value = getattr(self, moderation_field_name(category), None)
        return float(value) if value is not None else 1.0

    @classmethod
    def default_moderation_threshold(cls, category):
        """This category's configured default, for callers with no Bot instance.

        Reads the field's own default so there is exactly one definition of it;
        duplicated copies of the defaults are what let categories drift out of
        sync in the first place.
        """
        try:
            field = cls._meta.get_field(moderation_field_name(category))
        except FieldDoesNotExist:
            return 1.0
        return float(field.default)

    @classmethod
    def get_default_model(cls):
        """Get or create a default model for bots"""
        # Ensure default models exist
        Model.get_or_create_default_models()

        # Return the first available model (preferably GPT-4o Mini)
        try:
            return (
                Model.objects.filter(
                    provider__name="OpenAI",
                    model_id="gpt-4o-mini",
                ).first()
                or Model.objects.first()
            )
        except Exception:
            return None


class Keystroke(models.Model):
    # Respnose_ID but not FK to conversation because keystroke logging can happen without conversation registration
    conversation_id = models.CharField(max_length=255)
    total_time_on_page = models.FloatField()
    total_time_away_from_page = models.FloatField()
    keystroke_count = models.IntegerField()
    timestamp = models.DateTimeField(auto_now_add=False)

    def __str__(self):
        return (
            f"Keystroke log for conversation {self.conversation_id} at {self.timestamp}"
        )


def generate_survey_token():
    """A fresh ingest token. 64 hex characters from the OS random source."""
    return secrets.token_hex(32)


class SurveyIngestToken(models.Model):
    """
    A shared secret the survey tool sends to /api/survey_response/.

    Issued from the admin panel so a researcher can start a study without an
    AWS console or a redeploy. The token is stored in readable form because
    whoever configures the survey has to copy it into the survey tool — the
    protection is that only admin users can see this table.

    The SURVEY_INGEST_TOKEN environment variable keeps working alongside these,
    for deployments that prefer to manage the secret as infrastructure.
    """

    token = models.CharField(
        max_length=64,
        unique=True,
        default=generate_survey_token,
        editable=False,
        help_text="Generated automatically. Copy this into your survey tool as the X-Survey-Token header.",
    )
    note = models.CharField(
        max_length=255,
        help_text="What this token is for, e.g. 'Stress study, wave 1'. Only for your own reference.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Untick to revoke this token without deleting the record. A revoked token is rejected immediately.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Survey ingest token"
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.note} ({'active' if self.is_active else 'revoked'})"


class SurveyResponse(models.Model):
    """
    Answers a participant gave in the survey tool before reaching the chatbot.

    Like Keystroke, this deliberately holds no ForeignKey: the survey tool
    POSTs these answers while the participant is still mid-survey, so the
    Conversation does not exist yet. initialize_conversation looks the row up
    by (survey_id, participant_id) and snapshots it onto the conversation.
    """

    survey_id = models.CharField(max_length=255)
    participant_id = models.CharField(max_length=255)
    answers = models.JSONField(
        default=list,
        help_text="List of {'question': ..., 'answer': ...} in the order asked. Neither the number of questions nor the length of an answer is capped.",
    )
    received_at = models.DateTimeField(auto_now=True)

    class Meta:
        # One row per participant per survey: the survey tool may retry, and a
        # participant may restart the survey. Both overwrite rather than
        # accumulate ambiguous duplicates.
        unique_together = ("survey_id", "participant_id")

    def __str__(self):
        return (
            f"Survey response for participant {self.participant_id} "
            f"in survey {self.survey_id} ({len(self.answers or [])} answers)"
        )


class Avatar(models.Model):
    # New Column:
    CONDITION_CHOICES = [
        ("control", "control"),
        ("similar", "similar"),
        ("dissimilar", "dissimilar"),
    ]
    bot = models.ForeignKey(
        Bot,
        on_delete=models.CASCADE,
        related_name="avatars",
        null=True,
        blank=True,
    )
    bot_conversation = models.CharField(max_length=255, null=True, blank=True)
    condition = models.CharField(
        max_length=20,
        choices=CONDITION_CHOICES,
        default="similar",
    )
    participant_avatar = models.TextField(null=True, blank=True)
    chatbot_avatar = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"Avatar for Conversation {self.bot.name} {self.bot.avatar_type} {self.condition} {self.participant_avatar} {self.chatbot_avatar}"
