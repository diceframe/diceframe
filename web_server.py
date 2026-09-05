from pathlib import Path

import asyncio
import logging
import os
import sys

from aiohttp import web

sys.path.insert(0, str(Path(__file__).parent))
from src.runtime_env import load_project_env
from src.runtime_asyncio import install_runtime_exception_handler
from src.runtime_logging import RETENTION_DAYS, configure_runtime_logging

load_project_env(Path(__file__).resolve().with_name(".env"))

from src.ai_providers import is_llm_config_ready
from src.common_factory import TRPGSubsystems, create_trpg_subsystems
from src.migrations.config import (
    DEFAULT_NARRATIVE_MAX_TOKENS,
    migrate_generation_defaults as _migrate_generation_defaults,
)
from src.tts import SpeechService
from src.asr import AsrService
from src.imagegen import ImageGenerationService
from src.webui.api import WebAPI
from src.webui.composition import (
    RuntimeComposition,
    RuntimeFactories,
    RuntimePaths as CompositionPaths,
)
from src.webui.application import ApplicationDependencies, create_app
from src.webui.runtime_config import (
    ConfigStore,
    RuntimePaths,
)
from src.webui.host_credentials import HostCredentials
from src.webui.bootstrap import (
    BootstrapDependencies,
    BootstrapPaths,
    WebUIBootstrap,
)
from src.webui.access_control import WebAccessControl
from src.webui.config_controller import (
    ConfigController,
    ConfigControllerDependencies,
)

logger = logging.getLogger("trpg")
logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")


ROOT = Path(__file__).parent
RUNTIME_PATHS = RuntimePaths.from_root(ROOT, os.environ)
CONFIG_STORE = ConfigStore(RUNTIME_PATHS, os.environ, logger=logger)
DATA_DIR = RUNTIME_PATHS.data_dir
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = RUNTIME_PATHS.config_file
SECRETS_FILE = RUNTIME_PATHS.secrets_file
ACCESS_TOKEN_FILE = RUNTIME_PATHS.access_token_file


def _quarantine_invalid_json(path: Path) -> Path | None:
    return CONFIG_STORE.quarantine_invalid_json(path)


def _load_json_object(path: Path, label: str) -> dict:
    return CONFIG_STORE.load_json_object(path, label)


RUNTIME_CONFIG = CONFIG_STORE.load()
STATE = RUNTIME_CONFIG.state
HOST = RUNTIME_CONFIG.host
PORT = RUNTIME_CONFIG.port
TRANSPORT = RUNTIME_CONFIG.transport
WEB_CORS_ENV_VALUE = RUNTIME_CONFIG.cors_env_value
WEB_CORS_CONFIG_VALUE = RUNTIME_CONFIG.cors_config_value
WEB_CORS_ORIGINS = RUNTIME_CONFIG.cors_origins

PROMPTS_DIR = RUNTIME_PATHS.prompts_dir
BUILTIN_RULES_DIR = RUNTIME_PATHS.builtin_rules_dir
BUILTIN_WORLDS_DIR = RUNTIME_PATHS.builtin_worlds_dir
BUILTIN_ADVENTURES_DIR = RUNTIME_PATHS.builtin_adventures_dir
RULES_DIR = RUNTIME_PATHS.rules_dir
WORLDS_DIR = RUNTIME_PATHS.worlds_dir
ADVENTURES_DIR = RUNTIME_PATHS.adventures_dir
STATIC_V2_DIR = RUNTIME_PATHS.static_v2_dir

def _atomic_write_json(path: Path, data: dict) -> None:
    CONFIG_STORE.atomic_write_json(path, data)


def _mask_secret(value: str) -> dict:
    return CONFIG_STORE.mask_secret(value)


def _public_config() -> dict:
    return CONFIG_STORE.public_view(RUNTIME_CONFIG)


def save_config():
    CONFIG_STORE.save(STATE)


def _legacy_plugin_bot_token() -> str:
    return _host_credentials().legacy_plugin_bot_token()


def _ensure_bot_token() -> str:
    return _host_credentials().ensure_bot_token()


def _write_access_token_file(password: str) -> None:
    _host_credentials().write_access_token_file(password)


def _delete_access_token_file() -> None:
    _host_credentials().delete_access_token_file()


def _read_access_token_file() -> str:
    return _host_credentials().read_access_token_file()


def _generate_initial_access_password() -> None:
    _host_credentials().generate_initial_access_password()


def _host_credentials() -> HostCredentials:
    return HostCredentials(
        state=STATE,
        data_dir=DATA_DIR,
        access_token_file=ACCESS_TOKEN_FILE,
        environ=os.environ,
        save_config=save_config,
        logger=logger,
    )


def _build_subsystems(
    reuse: TRPGSubsystems | None = None,
    config: dict | None = None,
) -> TRPGSubsystems:
    return _runtime_composition().build_subsystems(reuse=reuse, config=config)


def _config_with_resolved_api_refs(config: dict) -> dict:
    return RuntimeComposition.config_with_resolved_api_refs(config)


def _make_api(subsystems: TRPGSubsystems, plugin_host=None, config: dict | None = None, hub_client=None) -> WebAPI:
    return _runtime_composition().make_api(
        subsystems,
        plugin_host=plugin_host,
        config=config,
        hub_client=hub_client,
    )


def _activate_api_runtime(subsystems: TRPGSubsystems, api: WebAPI) -> None:
    RuntimeComposition.activate_api_runtime(subsystems, api)


def _runtime_composition() -> RuntimeComposition:
    """Build the composition boundary from current compatibility globals."""
    return RuntimeComposition(
        paths=CompositionPaths(
            data_dir=DATA_DIR,
            prompts_dir=PROMPTS_DIR,
            rules_dir=RULES_DIR,
            worlds_dir=WORLDS_DIR,
            adventures_dir=ADVENTURES_DIR,
        ),
        state=STATE,
        save_config=save_config,
        factories=RuntimeFactories(
            create_subsystems=create_trpg_subsystems,
            create_web_api=WebAPI,
            create_speech=SpeechService,
            create_asr=AsrService,
            create_imagegen=ImageGenerationService,
        ),
    )


BOOTSTRAP = WebUIBootstrap(
    BootstrapDependencies(
        paths=BootstrapPaths(
            root=ROOT,
            data_dir=DATA_DIR,
            builtin_rules_dir=BUILTIN_RULES_DIR,
            builtin_worlds_dir=BUILTIN_WORLDS_DIR,
            builtin_adventures_dir=BUILTIN_ADVENTURES_DIR,
            rules_dir=RULES_DIR,
            worlds_dir=WORLDS_DIR,
            adventures_dir=ADVENTURES_DIR,
        ),
        state=STATE,
        environ=os.environ,
        transport=TRANSPORT,
        generation_defaults_migrated=RUNTIME_CONFIG.generation_defaults_migrated,
        credentials=_host_credentials,
        save_config=save_config,
        build_subsystems=_build_subsystems,
        make_api=_make_api,
        activate_api_runtime=_activate_api_runtime,
    ),
    logger=logger,
)

ACCESS_CONTROL = WebAccessControl(STATE)
auth_middleware = ACCESS_CONTROL.middleware

CONFIG_CONTROLLER = ConfigController(
    ConfigControllerDependencies(
        state=STATE,
        environ=os.environ,
        cors_env_value=WEB_CORS_ENV_VALUE,
        public_config=lambda: _public_config(),
        save_config=lambda: save_config(),
        ensure_bot_token=lambda: _ensure_bot_token(),
        delete_access_token_file=lambda: _delete_access_token_file(),
        build_subsystems=lambda *args, **kwargs: _build_subsystems(*args, **kwargs),
        make_api=lambda *args, **kwargs: _make_api(*args, **kwargs),
        activate_api_runtime=lambda *args, **kwargs: _activate_api_runtime(
            *args, **kwargs
        ),
    ),
    logger=logger,
)
api_config_get = CONFIG_CONTROLLER.get
api_config_post = CONFIG_CONTROLLER.post
_apply_config_update = CONFIG_CONTROLLER.apply_update
api_bot_token_post = CONFIG_CONTROLLER.bot_token_post
_is_safe_external_url = CONFIG_CONTROLLER.is_safe_external_url
api_test_connection = CONFIG_CONTROLLER.test_connection
api_config_provider_models_post = CONFIG_CONTROLLER.provider_models_post
_proxy_from_test_body = CONFIG_CONTROLLER.proxy_from_test_body
api_test_embedding = CONFIG_CONTROLLER.test_embedding
api_test_proxy = CONFIG_CONTROLLER.test_proxy


def _application_dependencies() -> ApplicationDependencies:
    return ApplicationDependencies(
        data_dir=DATA_DIR,
        static_v2_dir=STATIC_V2_DIR,
        cors_origins=WEB_CORS_ORIGINS,
        transport=TRANSPORT,
        config_state=STATE,
        save_config=save_config,
        on_startup=BOOTSTRAP.on_startup,
        on_cleanup=BOOTSTRAP.on_cleanup,
        auth_middleware=auth_middleware,
        config_get=api_config_get,
        config_post=api_config_post,
        bot_token_post=api_bot_token_post,
        provider_models_post=api_config_provider_models_post,
        test_connection=api_test_connection,
        test_embedding=api_test_embedding,
        test_proxy=api_test_proxy,
    )


app = create_app(_application_dependencies())

if __name__ == "__main__":
    runtime_log_path = configure_runtime_logging(DATA_DIR)
    logger.info("运行日志写入 %s（保留 %s 天）", runtime_log_path, RETENTION_DAYS)
    if TRANSPORT.degraded_error:
        logger.critical("%s", TRANSPORT.degraded_error)
    print(f"DiceFrame WebUI: {TRANSPORT.endpoint.url('127.0.0.1')}  (host={HOST})")
    if not is_llm_config_ready(STATE):
        print("请在 WebUI 的 AI 服务商与模型配置中设置主模型。")
    runtime_loop = asyncio.new_event_loop()
    install_runtime_exception_handler(runtime_loop)
    web.run_app(
        app,
        host=HOST,
        port=PORT,
        ssl_context=TRANSPORT.ssl_context,
        loop=runtime_loop,
    )
    if app["runtime_control"]["restart_requested"]:
        logger.info("DiceFrame 清理完成，正在重新启动")
        os.execv(sys.executable, [sys.executable, *sys.argv])
