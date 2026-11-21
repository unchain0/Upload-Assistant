from src.prompt_sound import play_prompt_sound


def test_prompt_sound_uses_the_cli_bell(capsys) -> None:
    play_prompt_sound()

    assert capsys.readouterr().out == "\x07"
