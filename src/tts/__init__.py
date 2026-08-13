"""Text-to-speech provider abstraction used by DiceFrame's WebUI and plugins."""

from .contracts import SpeechAudio, SpeechRequest, VoiceProfile
from .service import SpeechService, SpeechServiceError
from .profile_store import VoiceProfileStore

__all__ = [
    "SpeechAudio",
    "SpeechRequest",
    "SpeechService",
    "SpeechServiceError",
    "VoiceProfileStore",
    "VoiceProfile",
]
