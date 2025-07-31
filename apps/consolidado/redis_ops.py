import json
from typing import Dict, Any, Optional
from configuration import Config
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
import redis

patch_all()
config = Config()

_redis_client = None

def get_redis_client() -> Optional[redis.Redis]:
    global _redis_client

    if _redis_client is None and config.REDIS_ENDPOINT:
        try:
            _redis_client = redis.Redis(
                host=config.REDIS_ENDPOINT,
                port=config.REDIS_PORT,
                decode_responses=True,
                socket_connect_timeout=10,
                socket_timeout=10,
                retry_on_timeout=True,
                health_check_interval=30,
                ssl=True,
                ssl_cert_reqs=None,
                ssl_check_hostname=False
            )
            # Testar conexão
            _redis_client.ping()
            config.logger.info("Conexão Redis estabelecida com sucesso")
        except Exception as e:
            config.logger.warning(f"Erro ao conectar no Redis: {str(e)}")
            _redis_client = None

    return _redis_client


@xray_recorder.capture('get_from_cache')
def get_from_cache(key: str) -> Optional[Dict[str, Any]]:
    """
    Recupera dados do cache Redis
    """
    redis_client = get_redis_client()
    if not redis_client:
        return None

    try:
        cached_data = redis_client.get(key)
        if cached_data:
            return json.loads(cached_data)
    except Exception as e:
        config.logger.warning(f"Erro ao recuperar do cache: {str(e)}")

    return None


@xray_recorder.capture('set_cache')
def set_cache(key: str, data: Dict[str, Any], ttl: int = 3600) -> None:
    """
    Armazena dados no cache Redis
    """
    redis_client = get_redis_client()
    if not redis_client:
        return

    try:
        redis_client.setex(key, ttl, json.dumps(data, default=str))
    except Exception as e:
        config.logger.warning(f"Erro ao armazenar no cache: {str(e)}")


@xray_recorder.capture('invalidate_cache')
def invalidate_cache(pattern: str) -> None:
    """
    Invalida cache baseado em pattern
    """
    redis_client = get_redis_client()
    if not redis_client:
        return

    try:
        keys = redis_client.keys(pattern)
        if keys:
            redis_client.delete(*keys)
            config.logger.info(f"Cache invalidado: {len(keys)} chaves removidas")
    except Exception as e:
        config.logger.warning(f"Erro ao invalidar cache: {str(e)}")
