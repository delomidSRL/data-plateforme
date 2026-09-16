import copy
import secrets

from cryptography.fernet import Fernet

from app.core.security import decrypt_secret, encrypt_secret

SECRET_FIELDS = {
    "postgres": ["password"],
    "minio": ["secret_key"],
    "superset": ["admin_password", "secret_key", "metadata_db_password"],
    "airflow": ["admin_password", "fernet_key", "jwt_secret", "webserver_secret_key"],
    "jupyter": ["token"],
}

MASK = "••••••••"


def apply_defaults(services: dict) -> dict:
    services = copy.deepcopy(services)
    superset = services.get("superset")
    if superset and superset.get("enabled"):
        if not superset.get("secret_key"):
            superset["secret_key"] = secrets.token_urlsafe(32)
        if not superset.get("metadata_db_password"):
            superset["metadata_db_password"] = secrets.token_urlsafe(16)
    airflow = services.get("airflow")
    if airflow and airflow.get("enabled"):
        if not airflow.get("fernet_key"):
            airflow["fernet_key"] = Fernet.generate_key().decode()
        if not airflow.get("jwt_secret"):
            airflow["jwt_secret"] = secrets.token_urlsafe(32)
        if not airflow.get("webserver_secret_key"):
            airflow["webserver_secret_key"] = secrets.token_urlsafe(32)
    jupyter = services.get("jupyter")
    if jupyter and jupyter.get("enabled"):
        if not jupyter.get("token"):
            jupyter["token"] = secrets.token_urlsafe(24)
    return services


def merge_with_existing(existing_plain: dict, incoming_plain: dict) -> dict:
    """Blank secret fields in `incoming_plain` mean 'keep the current value' (from `existing_plain`)."""
    merged = copy.deepcopy(incoming_plain)
    for service_name, fields in SECRET_FIELDS.items():
        incoming_block = merged.get(service_name)
        existing_block = (existing_plain or {}).get(service_name) or {}
        if not incoming_block:
            continue
        for field in fields:
            incoming_value = incoming_block.get(field)
            is_unset_or_mask = not incoming_value or incoming_value == MASK
            if is_unset_or_mask and existing_block.get(field):
                incoming_block[field] = existing_block[field]
    return merged


def encrypt_services(services: dict) -> dict:
    services = copy.deepcopy(services)
    for service_name, fields in SECRET_FIELDS.items():
        block = services.get(service_name)
        if not block:
            continue
        for field in fields:
            value = block.get(field)
            if value:
                block[field] = encrypt_secret(value)
    return services


def decrypt_services(services: dict) -> dict:
    services = copy.deepcopy(services)
    for service_name, fields in SECRET_FIELDS.items():
        block = services.get(service_name)
        if not block:
            continue
        for field in fields:
            value = block.get(field)
            if value:
                block[field] = decrypt_secret(value)
    return services


def mask_services(services: dict) -> dict:
    services = copy.deepcopy(services)
    for service_name, fields in SECRET_FIELDS.items():
        block = services.get(service_name)
        if not block:
            continue
        for field in fields:
            if block.get(field):
                block[field] = MASK
    return services
