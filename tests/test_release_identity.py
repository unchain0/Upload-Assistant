from src.domain_models.release import Meta
from src.domain_models.release_identity import ReleaseYearIdentity


def test_release_year_identity_prefers_manual_then_metadata_then_search() -> (
    None
):
    assert (
        ReleaseYearIdentity.from_release(
            Meta(manual_year=2025, year=2024, search_year=2023)
        ).canonical
        == 2025
    )
    assert (
        ReleaseYearIdentity.from_release(
            Meta(year=2024, search_year=2023)
        ).canonical
        == 2024
    )
    assert (
        ReleaseYearIdentity.from_release(Meta(search_year=2023)).canonical
        == 2023
    )


def test_release_year_identity_keeps_imdb_year_informational() -> None:
    identity = ReleaseYearIdentity.from_release(
        Meta(year=2024, search_year=2023, imdb_info={"year": 2023})
    )
    assert identity.canonical == 2024
    assert identity.imdb == 2023
    assert identity.canonical_text == "2024"


def test_release_year_identity_rejects_invalid_year_values_and_missing_imdb_mapping() -> (
    None
):
    identity = ReleaseYearIdentity.from_release(
        Meta(year="not-a-year", search_year=1700, imdb_info=[])
    )  # type: ignore[arg-type]
    assert identity.canonical is None
    assert identity.imdb is None
    assert identity.canonical_text == ""


def test_original_language_cannot_be_erased_by_empty_metadata_fallback() -> (
    None
):
    meta = Meta(original_language="en")

    meta.original_language = None
    assert meta.original_language == "en"

    meta.update({"original_language": ""})
    assert meta.original_language == "en"

    meta.original_language = "fa"
    assert meta.original_language == "fa"


def test_original_language_initializes_empty_when_no_source_exists() -> None:
    assert Meta().original_language is None
