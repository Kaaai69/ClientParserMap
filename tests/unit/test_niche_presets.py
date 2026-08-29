from pathlib import Path

import pytest

from app.core.errors import ConfigurationError
from app.presets import NichePresets

SHIPPED = Path("app/presets/niche_presets.toml")


def test_shipped_presets_load_and_are_usable() -> None:
    presets = NichePresets.load(SHIPPED)

    assert presets.preset
    for preset in presets.preset:
        assert preset.queries
        assert len(set(preset.queries)) == len(preset.queries)
    assert presets.get("small_business") is not None
    assert presets.get("нет такого") is None


def test_duplicate_queries_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "presets.toml"
    path.write_text(
        '[[preset]]\nid = "a"\ntitle = "A"\nqueries = ["кафе", "кафе"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError):
        NichePresets.load(path)


def test_duplicate_preset_ids_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "presets.toml"
    path.write_text(
        '[[preset]]\nid = "a"\ntitle = "A"\nqueries = ["кафе"]\n'
        '[[preset]]\nid = "a"\ntitle = "B"\nqueries = ["бар"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError):
        NichePresets.load(path)


def test_a_missing_file_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        NichePresets.load(tmp_path / "absent.toml")
