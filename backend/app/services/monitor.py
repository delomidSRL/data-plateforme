import json
from dataclasses import dataclass


@dataclass
class ContainerStatus:
    name: str
    service: str
    image: str
    state: str
    status: str
    health: str | None
    ports: str | None


def parse_compose_ps(raw_output: str) -> list[ContainerStatus]:
    raw_output = raw_output.strip()
    if not raw_output:
        return []

    records: list[dict]
    try:
        parsed = json.loads(raw_output)
        records = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        records = []
        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    containers = []
    for r in records:
        containers.append(
            ContainerStatus(
                name=r.get("Name", ""),
                service=r.get("Service", ""),
                image=r.get("Image", ""),
                state=r.get("State", "unknown"),
                status=r.get("Status", ""),
                health=r.get("Health") or None,
                ports=r.get("Publishers") and ", ".join(
                    f"{p.get('PublishedPort')}->{p.get('TargetPort')}" for p in r["Publishers"] if p.get("PublishedPort")
                ) or r.get("Ports"),
            )
        )
    return containers
