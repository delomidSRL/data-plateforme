import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

# Mirrors the old no-code contract's own flag-name rule (Module 18 §6/§8): lowercase, digits,
# underscore, never starting with a digit — a literal tag matched verbatim by hand-written
# 05 routing, never a SQL identifier, so it can't contain ':' either (cast_issue's own tag
# delimiter, 'FIELD:absent'/'FIELD:invalid').
_FLAG_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

DqFlagCategoryLiteral = Literal["informative", "elimination"]


class DqFlagRegistryEntryBase(BaseModel):
    flag_name: str
    category: DqFlagCategoryLiteral = "elimination"
    source_rule: str | None = None
    issue_type: str | None = None

    @field_validator("flag_name")
    @classmethod
    def _validate_flag_name(cls, v: str) -> str:
        if not _FLAG_NAME_RE.match(v):
            raise ValueError("Nom de flag invalide (minuscules, chiffres, underscore, ne commence pas par un chiffre).")
        return v


class DqFlagRegistryEntryCreate(DqFlagRegistryEntryBase):
    pass


class DqFlagRegistryEntryUpdate(DqFlagRegistryEntryBase):
    pass


class DqFlagRegistryEntryOut(DqFlagRegistryEntryBase):
    id: int
    created_at: datetime
    updated_at: datetime
    updated_by: int | None = None

    model_config = ConfigDict(from_attributes=True)
