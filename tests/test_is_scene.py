from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.domain_models.release import Meta
from src.integrations.media.scene_detection import SceneManager


@pytest.mark.asyncio
async def test_scene_lookup_runs_when_meta_scene_defaults_to_false(
    tmp_path,
) -> None:
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "resultsCount": 1,
            "results": [
                {
                    "release": "Cellar.Keeper-TENOKE",
                    "hasNFO": "no",
                    "imdbId": "",
                }
            ],
        },
    )
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.get.return_value = response
    meta = Meta(
        base_dir=str(tmp_path),
        uuid="cellar",
        category="GAME",
        path=str(tmp_path / "Cellar.Keeper-TENOKE"),
        isdir=True,
    )

    with patch(
        "src.integrations.media.scene_detection.httpx.AsyncClient",
        return_value=client,
    ):
        video, scene, imdb = await SceneManager({"DEFAULT": {}}).is_scene(
            str(meta.path), meta
        )

    assert scene is True
    assert meta.scene_name == "Cellar.Keeper-TENOKE"
    assert video == "Cellar.Keeper-TENOKE.mkv"
    assert imdb is None
    client.get.assert_awaited_once()
