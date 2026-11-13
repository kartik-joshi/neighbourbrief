"""`.env` parsing: the forms people actually type (inline comments, blanks, yes/no, spaces)."""
from dotenv import dotenv_values

from app import config


def test_dotenv_forms(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "TRANSLATOR=auto   # auto | azure | llm\n"
        "DEMO_OFFLINE=Yes\n"
        "ALLOW_LAN=\n"
        "REQUIRE_REVIEW=1 # two-person check\n"
        "ROLE_REVIEWER=Sergeant\n"
        "PUBLISHER_NAME=\"53 Division CPLC\"\n"
        "LLM_TIMEOUT= 90 \n"
        "ALLOW_CLIENTS=172.16.0.0/12,192.168.65.0/24\n", encoding="utf-8")
    for k, v in dotenv_values(env).items():
        monkeypatch.setenv(k, v or "")
    s = config.get_settings()
    assert s.translator == "auto"
    assert s.demo_offline is True and s.allow_lan is False and s.require_review is True
    assert s.role_reviewer == "Sergeant" and s.role_author == "Author"          # default when missing
    assert s.publisher_name == "53 Division CPLC"
    assert s.llm_timeout == 90.0
    assert s.allow_clients == "172.16.0.0/12,192.168.65.0/24"


def test_values_without_dotenv_stripping(monkeypatch):
    monkeypatch.setenv("TRANSLATOR", "azure   # comment kept by an old dotenv")
    monkeypatch.setenv("DEMO_OFFLINE", "off")
    monkeypatch.setenv("ROLE_AUTHOR", "   ")
    s = config.get_settings()
    assert s.translator == "azure" and s.demo_offline is False and s.role_author == "Author"
