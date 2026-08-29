import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.errors import ConfigurationError

MAX_QUERIES_PER_PRESET = 50


class PresetModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class NichePreset(PresetModel):
    id: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    title: str = Field(min_length=1, max_length=100)
    queries: tuple[str, ...] = Field(min_length=1, max_length=MAX_QUERIES_PER_PRESET)

    @field_validator("queries")
    @classmethod
    def queries_are_clean_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(" ".join(item.split()) for item in value)
        if any(not item for item in cleaned):
            raise ValueError("queries must not be blank")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("queries must be unique")
        return cleaned


class NichePresets(PresetModel):
    preset: tuple[NichePreset, ...] = Field(default=())

    @field_validator("preset")
    @classmethod
    def ids_are_unique(cls, value: tuple[NichePreset, ...]) -> tuple[NichePreset, ...]:
        identifiers = [item.id for item in value]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("preset ids must be unique")
        return value

    def get(self, preset_id: str) -> NichePreset | None:
        return next((item for item in self.preset if item.id == preset_id), None)

    @classmethod
    def load(cls, path: Path) -> "NichePresets":
        try:
            with path.open("rb") as presets_file:
                payload = tomllib.load(presets_file)
            return cls.model_validate(payload)
        except (OSError, tomllib.TOMLDecodeError, ValidationError) as error:
            raise ConfigurationError("Некорректный файл наборов ниш") from error


@lru_cache(maxsize=8)
def load_presets(path: Path) -> NichePresets:
    return NichePresets.load(path)
