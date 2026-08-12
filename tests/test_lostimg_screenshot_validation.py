import pytest

from src.takescreens import is_valid_lostimg_image_size


@pytest.mark.parametrize(
    ("image_size", "valid"),
    [
        (75_000, False),
        (75_001, True),
        (20_000_000, True),
        (20_000_001, False),
    ],
)
def test_lostimg_size_boundaries(image_size: int, valid: bool) -> None:
    assert is_valid_lostimg_image_size(image_size) is valid  # noqa: S101
