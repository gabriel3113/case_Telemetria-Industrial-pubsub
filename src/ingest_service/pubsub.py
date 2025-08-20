from google.cloud import pubsub_v1
from google.oauth2 import service_account
import base64, json
from typing import Optional
from .config import settings

_publisher: Optional[pubsub_v1.PublisherClient] = None
_topic_path: Optional[str] = None

def _build_credentials():
    # 1) Caminho para o arquivo (CREDENTIALS_PATH)
    if settings.credentials_path:
        return service_account.Credentials.from_service_account_file(settings.credentials_path)
    # 2) Conteúdo base64 (CREDENTIALS_JSON_B64)
    if settings.credentials_json_b64:
        info = json.loads(base64.b64decode(settings.credentials_json_b64))
        return service_account.Credentials.from_service_account_info(info)
    # 3) Fallback para ADC do ambiente (GOOGLE_APPLICATION_CREDENTIALS, gcloud etc.)
    return None

def _get_publisher() -> pubsub_v1.PublisherClient:
    global _publisher, _topic_path
    if _publisher is None:
        creds = _build_credentials()
        _publisher = pubsub_v1.PublisherClient(credentials=creds) if creds else pubsub_v1.PublisherClient()
        _topic_path = _publisher.topic_path(settings.project_id, settings.topic_id)
    return _publisher

def publish_message(payload: dict, ordering_key: Optional[str] = None) -> None:
    publisher = _get_publisher()
    data = json.dumps(payload).encode("utf-8")
    kwargs = {}
    if ordering_key:
        kwargs["ordering_key"] = ordering_key
    publisher.publish(_topic_path, data=data, **kwargs).result(timeout=30)
