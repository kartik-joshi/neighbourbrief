"""Cohere client with a fake HTTP layer: request shapes, citation parsing, retries, errors, batching."""
import httpx
import pytest

from app.ai import cohere, llm


class Resp:
    def __init__(self, status, data, headers=None):
        self.status_code, self._data, self.headers = status, data, headers or {}

    def json(self):
        return self._data


@pytest.fixture()
def key(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "test-key")
    monkeypatch.setenv("DEMO_OFFLINE", "false")


def test_chat_with_documents_and_citations(key, monkeypatch):
    sent = {}

    def post(url, json, headers, timeout):
        sent.update(url=url, body=json, headers=headers)
        return Resp(200, {"id": "r1", "message": {"role": "assistant", "content": [{"type": "text", "text": "There were 6 assaults."}],
                                                   "citations": [{"start": 10, "end": 21, "text": "6 assaults.", "type": "TEXT_CONTENT",
                                                                  "sources": [{"type": "document", "id": "crime-055", "document": {"id": "crime-055"}}]}]},
                          "usage": {"billed_units": {"input_tokens": 100, "output_tokens": 8}}})
    monkeypatch.setattr(httpx, "post", post)
    r = cohere.chat([{"role": "user", "content": "How many?"}], documents=[{"id": "crime-055", "data": {"title": "t", "text": "x"}}])
    assert sent["url"] == "https://api.cohere.com/v2/chat" and sent["headers"]["Authorization"] == "Bearer test-key"
    assert sent["body"]["model"] == "command-a-03-2025" and sent["body"]["documents"][0]["id"] == "crime-055"
    assert r["text"] == "There were 6 assaults." and r["citations"] == [{"start": 10, "end": 21, "text": "6 assaults.", "doc_ids": ["crime-055"]}]


def test_retry_on_429_then_errors(key, monkeypatch):
    calls = []

    def post(url, json, headers, timeout):
        calls.append(1)
        return Resp(429, {}, {"Retry-After": "0"}) if len(calls) == 1 else Resp(200, {"results": [{"index": 1, "relevance_score": 0.9}]})
    monkeypatch.setattr(httpx, "post", post)
    assert cohere.rerank("q", ["a", "b"], top_n=1) == [(1, 0.9)] and len(calls) == 2
    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp(401, {"message": "invalid api token"}))
    with pytest.raises(cohere.CohereUnavailable, match="401: invalid api token"):
        cohere.rerank("q", ["a"], top_n=1)

    def down(*a, **k):
        raise httpx.ConnectError("no network")
    monkeypatch.setattr(httpx, "post", down)
    with pytest.raises(cohere.CohereUnavailable, match="unreachable"):
        cohere.embed(["a"], "search_query")


def test_embed_batches_of_96(key, monkeypatch):
    sizes = []

    def post(url, json, headers, timeout):
        sizes.append(len(json["texts"]))
        assert json["input_type"] == "search_document" and json["embedding_types"] == ["float"]
        return Resp(200, {"embeddings": {"float": [[0.1, 0.2]] * len(json["texts"])}})
    monkeypatch.setattr(httpx, "post", post)
    assert len(cohere.embed(["t"] * 200, "search_document")) == 200 and sizes == [96, 96, 8]


def test_not_used_without_key_or_offline(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "")
    assert not cohere.configured()
    with pytest.raises(cohere.CohereUnavailable, match="COHERE_API_KEY"):
        cohere.chat([{"role": "user", "content": "x"}])
    monkeypatch.setenv("COHERE_API_KEY", "k")
    monkeypatch.setenv("DEMO_OFFLINE", "true")
    assert not cohere.configured() and "offline" in cohere.status()["detail"]


def test_cohere_as_llm_provider(key, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "cohere")
    seen = {}

    def chat(messages, json_mode=False, **kw):
        seen.update(messages=messages, json_mode=json_mode)
        return {"text": '{"headline": "x"}', "citations": []}
    monkeypatch.setattr(cohere, "chat", chat)
    assert llm.complete("sys", "user", json=True) == '{"headline": "x"}'
    assert seen["messages"][0] == {"role": "system", "content": "sys"} and seen["json_mode"] is True
    assert llm.provider_label() == "Cohere (command-a-03-2025)"

    def fail(*a, **k):
        raise cohere.CohereUnavailable("down")
    monkeypatch.setattr(cohere, "chat", fail)
    with pytest.raises(llm.LLMUnavailable):
        llm.complete("s", "u")


def test_monthly_budget_stops_calls_and_counts_retries(key, monkeypatch):
    monkeypatch.setenv("COHERE_MONTHLY_CALLS", "3")
    sent = []

    def post(url, json, headers, timeout):
        sent.append(url)
        return Resp(429, {}, {"Retry-After": "0"}) if len(sent) == 1 else Resp(200, {"message": {"content": []}})
    monkeypatch.setattr(httpx, "post", post)
    cohere.chat([{"role": "user", "content": "q"}])  # a 429 and its retry: 2 calls
    assert cohere.usage()["calls"] == 2
    cohere.chat([{"role": "user", "content": "q"}])  # 3 of 3
    with pytest.raises(cohere.CohereUnavailable, match="budget"):
        cohere.chat([{"role": "user", "content": "q"}])
    assert len(sent) == 3 and cohere.usage()["calls"] == 3
    assert "3/3 calls" in cohere.status()["detail"] and not cohere.status()["ok"]


def test_budget_resets_each_month(key, monkeypatch):
    cohere.USAGE_FILE.write_text('{"month": "2000-01", "calls": 999}', encoding="utf-8")
    assert cohere.usage()["calls"] == 0
