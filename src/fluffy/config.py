"""Load home configuration. Environment values are read, never logged."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


WARMTH_DEFAULTS = {"warm": "#FFD6A0", "neutral": "#FFF1D0", "cool": "#DDEBFF"}


@dataclass(frozen=True)
class LightConfig:
    id: str
    label: str


@dataclass(frozen=True)
class BlindConfig:
    id: str
    label: str


@dataclass(frozen=True)
class RoomConfig:
    id: str
    label: str
    speaker: str
    display: str
    lights: tuple[LightConfig, ...]
    blinds: tuple[BlindConfig, ...]


@dataclass(frozen=True)
class MediaConfig:
    id: str
    provider: str
    title: str
    uri: str | None = None
    asset: str | None = None


@dataclass(frozen=True)
class HomeConfig:
    rooms: tuple[RoomConfig, ...]
    media: tuple[MediaConfig, ...]
    warmth_colors: dict[str, str]
    clap_enabled: bool = True
    clap_cooldown_s: int = 15
    no_restart_if_same_track: bool = True
    automatic_comfort: bool = True
    plan_ttl_s: int = 30
    clap_fresh_s: int = 5
    room_cue_fresh_s: int = 10
    safety_questions: int = 2
    safety_window_s: int = 12
    checkin_1: str = "Are you okay? Say 'I'm okay' or 'help me.'"
    checkin_2: str = (
        "Are you okay? Please respond. If I don't hear from you, "
        "I'll message your trusted contact."
    )
    alert_template: str = (
        "Fluffy demo: a person may have fallen in the living room and did not "
        "respond to two check-ins. Please check on them."
    )
    audio_failure_note: str = "The audio check-in could not be played."
    max_utterance_chars: int = 240
    generation_timeout_s: int = 8
    ack_grace_s: int = 3
    speech_total_cap_s: int = 25
    suppression_tail_ms: int = 300
    local_timeout_s: float = 5
    spotify_timeout_s: float = 10
    photon_timeout_s: float = 8

    def room(self, room_id: str) -> RoomConfig | None:
        return next((room for room in self.rooms if room.id == room_id), None)

    def light(self, room_id: str, light_id: str) -> LightConfig | None:
        room = self.room(room_id)
        if room is None:
            return None
        return next((light for light in room.lights if light.id == light_id), None)

    def media_item(self, media_id: str) -> MediaConfig | None:
        return next((item for item in self.media if item.id == media_id), None)

    @property
    def room_ids(self) -> list[str]:
        return [room.id for room in self.rooms]

    @property
    def light_ids(self) -> list[str]:
        return [light.id for room in self.rooms for light in room.lights]

    @property
    def media_ids(self) -> list[str]:
        return [item.id for item in self.media]


DEFAULT_PERCEPTRON_MODEL = "perceptron-mk1.5"


@dataclass
class IntegrationsConfig:
    perceptron_api_key: str = ""
    perceptron_model: str = ""
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model_id: str = ""
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://127.0.0.1:8100/api/integrations/spotify/callback"
    photon_api_key: str = ""
    photon_project_id: str = ""
    photon_sender_id: str = ""
    caregiver_destination: str = ""
    photon_from: str = ""
    caregiver_handle: str = ""

    def __repr__(self) -> str:
        return (
            "IntegrationsConfig("
            f"perceptron_configured={self.perceptron_configured}, "
            f"elevenlabs_configured={self.elevenlabs_configured}, "
            f"spotify_configured={self.spotify_configured}, "
            f"photon_configured={self.photon_configured})"
        )

    @property
    def perceptron_configured(self) -> bool:
        return bool(self.perceptron_api_key)

    @property
    def elevenlabs_configured(self) -> bool:
        return bool(self.elevenlabs_api_key and self.elevenlabs_voice_id and self.elevenlabs_model_id)

    @property
    def photon_configured(self) -> bool:
        return bool(
            self.photon_api_key
            and self.photon_project_id
            and self.photon_sender_id
            and self.caregiver_destination
        )

    @property
    def spotify_configured(self) -> bool:
        return bool(self.spotify_client_id and self.spotify_client_secret)

    def inference_status(self) -> dict[str, str]:
        if self.perceptron_configured:
            return {
                "provider": "perceptron",
                "readiness": "configured",
                "detail": "Perceptron key is set. A live decision has not been verified.",
            }
        return {
            "provider": "perceptron",
            "readiness": "unavailable",
            "detail": "PERCEPTRON_API_KEY is not set. Inference will not turn text into device actions.",
        }


def _env(source: dict[str, str], name: str) -> str:
    return source.get(name, "").strip()


def load_integrations(source: dict[str, str] | None = None) -> IntegrationsConfig:
    if source is None:
        load_local_env()
        env = dict(os.environ)
    else:
        env = source
    model_id = _env(env, "ELEVENLABS_MODEL_ID") or _env(env, "ELEVENLABS_MODEL")
    return IntegrationsConfig(
        perceptron_api_key=_env(env, "PERCEPTRON_API_KEY"),
        perceptron_model=_env(env, "PERCEPTRON_MODEL"),
        elevenlabs_api_key=_env(env, "ELEVENLABS_API_KEY"),
        elevenlabs_voice_id=_env(env, "ELEVENLABS_VOICE_ID"),
        elevenlabs_model_id=model_id,
        spotify_client_id=_env(env, "SPOTIFY_CLIENT_ID"),
        spotify_client_secret=_env(env, "SPOTIFY_CLIENT_SECRET"),
        spotify_redirect_uri=_env(env, "SPOTIFY_REDIRECT_URI")
        or "http://127.0.0.1:8100/api/integrations/spotify/callback",
        photon_api_key=_env(env, "PHOTON_API_KEY"),
        photon_project_id=_env(env, "PHOTON_PROJECT_ID"),
        photon_sender_id=_env(env, "PHOTON_SENDER_ID"),
        caregiver_destination=_env(env, "CAREGIVER_DESTINATION"),
        photon_from=_env(env, "PHOTON_FROM"),
        caregiver_handle=_env(env, "PHOTON_CAREGIVER_HANDLE"),
    )


def perceptron_model_name(source: dict[str, str] | None = None) -> str:
    return load_integrations(source).perceptron_model or DEFAULT_PERCEPTRON_MODEL


def load_home_config(path: Path, source: dict[str, str] | None = None) -> HomeConfig:
    if source is None:
        load_local_env()
        env = dict(os.environ)
    else:
        env = source
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    rooms: list[RoomConfig] = []
    for room in raw.get("rooms", []):
        lights = tuple(
            LightConfig(id=item["id"], label=item.get("label", item["id"]))
            for item in room.get("lights", [])
        )
        blinds = tuple(
            BlindConfig(id=item["id"], label=item.get("label", item["id"]))
            for item in room.get("blinds", [])
        )
        rooms.append(
            RoomConfig(
                id=room["id"],
                label=room.get("label", room["id"]),
                speaker=room["speaker"],
                display=room["display"],
                lights=lights,
                blinds=blinds,
            )
        )
    media: list[MediaConfig] = []
    for item in raw.get("media", []):
        uri = item.get("uri")
        uri_env = item.get("uri_env")
        if uri_env:
            uri = _env(env, uri_env) or uri or "spotify:track:REPLACE_WITH_TRACK_ID"
        media.append(
            MediaConfig(
                id=item["id"],
                provider=item["provider"],
                title=item.get("title", item["id"]),
                uri=uri,
                asset=item.get("asset"),
            )
        )
    demo = raw.get("demo", {})
    safety = raw.get("safety", {})
    speech = raw.get("speech", {})
    timeouts = raw.get("timeouts", {})
    warmth = {**WARMTH_DEFAULTS, **raw.get("warmth", {})}
    if not rooms:
        raise ValueError("home config needs at least one room")
    return HomeConfig(
        rooms=tuple(rooms),
        media=tuple(media),
        warmth_colors=warmth,
        clap_enabled=bool(demo.get("clap_enabled", True)),
        clap_cooldown_s=int(demo.get("clap_cooldown_s", 15)),
        no_restart_if_same_track=bool(demo.get("no_restart_if_same_track", True)),
        automatic_comfort=bool(demo.get("automatic_comfort", True)),
        plan_ttl_s=int(demo.get("plan_ttl_s", 30)),
        clap_fresh_s=int(demo.get("clap_fresh_s", 5)),
        room_cue_fresh_s=int(demo.get("room_cue_fresh_s", 10)),
        safety_questions=int(safety.get("question_count", 2)),
        safety_window_s=int(safety.get("response_window_s", 12)),
        checkin_1=safety.get("checkin_1", HomeConfig.checkin_1),
        checkin_2=safety.get("checkin_2", HomeConfig.checkin_2),
        alert_template=safety.get("alert_template", HomeConfig.alert_template),
        audio_failure_note=safety.get("audio_failure_note", HomeConfig.audio_failure_note),
        max_utterance_chars=int(speech.get("max_utterance_chars", 240)),
        generation_timeout_s=int(speech.get("generation_timeout_s", 8)),
        ack_grace_s=int(speech.get("ack_grace_s", 3)),
        speech_total_cap_s=int(speech.get("total_cap_s", 25)),
        suppression_tail_ms=int(speech.get("suppression_tail_ms", 300)),
        local_timeout_s=float(timeouts.get("local_state_s", 5)),
        spotify_timeout_s=float(timeouts.get("spotify_s", 10)),
        photon_timeout_s=float(timeouts.get("photon_accept_s", 8)),
    )


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ensure_env_file(root: Path | None = None) -> Path:
    """Create a placeholder .env from the example. Never overwrite an existing file."""

    base = root or repo_root()
    path = base / ".env"
    example = base / ".env.example"
    if not path.exists() and example.is_file():
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def load_local_env(root: Path | None = None) -> None:
    """Load the repository-root .env. Values already in the process environment win."""

    from dotenv import load_dotenv

    base = root or repo_root()
    ensure_env_file(base)
    load_dotenv(base / ".env", override=False)


@dataclass
class RuntimeSettings:
    root: Path
    home: HomeConfig
    integrations: IntegrationsConfig
    internal_token: str
    harness_host: str = "127.0.0.1"
    harness_port: int = 8000
    home_host: str = "127.0.0.1"
    home_port: int = 8100
    data_dir: Path = field(default_factory=lambda: Path("data"))
    session_id: str = "ses_demo"
    autotick_safety: bool = True

    def __repr__(self) -> str:
        return (
            f"RuntimeSettings(session_id={self.session_id!r}, harness={self.harness_url!r}, "
            f"home={self.home_url!r}, {self.integrations!r})"
        )

    @property
    def harness_url(self) -> str:
        return f"http://{self.harness_host}:{self.harness_port}"

    @property
    def home_url(self) -> str:
        return f"http://{self.home_host}:{self.home_port}"

    @property
    def ledger_path(self) -> Path:
        return self.data_dir / "ledger.sqlite"

    @property
    def spotify_token_path(self) -> Path:
        return self.data_dir / "spotify_oauth.json"


def load_settings(root: Path | None = None, environ: dict[str, str] | None = None) -> RuntimeSettings:
    base = root or repo_root()
    if environ is None:
        load_local_env(base)
        env = dict(os.environ)
    else:
        env = environ
    config_path = Path(env.get("FLUFFY_CONFIG", base / "config" / "home.toml"))
    data_dir = Path(env.get("FLUFFY_DATA", base / "data"))
    token = _env(env, "FLUFFY_INTERNAL_TOKEN") or "dev-internal-token"
    return RuntimeSettings(
        root=base,
        home=load_home_config(config_path, env),
        integrations=load_integrations(env),
        internal_token=token,
        harness_host=env.get("FLUFFY_HARNESS_HOST", "127.0.0.1"),
        harness_port=int(env.get("FLUFFY_HARNESS_PORT", "8000")),
        home_host=env.get("FLUFFY_HOME_HOST", "127.0.0.1"),
        home_port=int(env.get("FLUFFY_HOME_PORT", "8100")),
        data_dir=data_dir,
        session_id=env.get("FLUFFY_SESSION_ID", "ses_demo"),
        autotick_safety=env.get("FLUFFY_AUTOTICK", "1") != "0",
    )
