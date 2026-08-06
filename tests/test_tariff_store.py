from pathlib import Path

from app.schemas.tariff import TariffConfig, TariffPeriod
from app.services.tariff.store import load_tariff_config, save_tariff_config


async def test_load_missing_file_returns_empty_config(tmp_path: Path) -> None:
    config = await load_tariff_config(str(tmp_path / "nope.json"))
    assert config.periods == []


async def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    path = str(tmp_path / "tariffs.json")
    original = TariffConfig(
        umbral_cs_kwh=130.0,
        periods=[
            TariffPeriod(
                month="2026-01", cu_cop_kwh=859.19, excedente_cop_kwh=114.34
            )
        ],
    )

    await save_tariff_config(path, original)
    loaded = await load_tariff_config(path)

    assert loaded == original


async def test_save_creates_parent_directory(tmp_path: Path) -> None:
    path = str(tmp_path / "nested" / "dir" / "tariffs.json")
    config = TariffConfig(umbral_cs_kwh=100.0)

    await save_tariff_config(path, config)

    assert Path(path).exists()
    loaded = await load_tariff_config(path)
    assert loaded.umbral_cs_kwh == 100.0
