import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# dbt/Jinja identifier: letters, digits, underscore, not starting with a digit — mirrors what
# a {% macro name(...) %} block requires to be callable as {{ name(...) }}.
_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class DbtMacroParameter(BaseModel):
    name: str
    default: str | None = None


class DbtMacroBase(BaseModel):
    name: str
    description: str | None = None
    parameters: list[DbtMacroParameter] = Field(default_factory=list)
    sql_body: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError("Le nom doit être un identifiant valide (lettres, chiffres, underscore, pas de chiffre en tête).")
        return v


class DbtMacroCreate(DbtMacroBase):
    pass


class DbtMacroUpdate(DbtMacroBase):
    pass


class DbtMacroOut(DbtMacroBase):
    id: int
    created_at: datetime
    updated_at: datetime
    updated_by: int | None = None

    model_config = ConfigDict(from_attributes=True)
