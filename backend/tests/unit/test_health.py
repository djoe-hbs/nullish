import httpx
import pytest
import respx

from app.config import Settings
from app.llm.embed import EmbedClient, EmbeddingDimensionError
from app.llm.health import check_chat, check_embed
from app.llm.rerank import RerankClient


def make_settings(**kw) -> Settings:
    base = dict(
        chat_base_url="http://gpu:8001/v1",
        gpu_api_key="k",
        embed_url="http://embed",
        rerank_url="http://rerank",
        embed_dim=4,
    )
    base.update(kw)
    return Settings(_env_file=None, **base)


@respx.mock
async def test_chat_offline_is_reported_not_raised():
    respx.get("http://gpu:8001/v1/models").mock(side_effect=httpx.ConnectError("down"))
    st = await check_chat(make_settings())
    assert st.ok is False
    assert st.detail.startswith("offline")


def models_payload(alias: str, root: str) -> dict:
    return {"object": "list", "data": [{"id": alias, "root": root}]}


@respx.mock
async def test_chat_online_reports_real_model_behind_alias():
    route = respx.get("http://gpu:8001/v1/models").mock(
        return_value=httpx.Response(200, json=models_payload("nullish-chat", "Qwen/small-4B"))
    )
    st = await check_chat(make_settings())
    assert st.ok and st.latency_ms is not None
    assert st.model == "Qwen/small-4B"  # status page shows which profile the GPU runs
    assert route.calls[0].request.headers["authorization"] == "Bearer k"


@respx.mock
async def test_chat_alias_not_served():
    respx.get("http://gpu:8001/v1/models").mock(
        return_value=httpx.Response(200, json=models_payload("something-else", "x"))
    )
    st = await check_chat(make_settings())
    assert not st.ok and "not served" in st.detail


@respx.mock
async def test_embed_health_http_error():
    respx.get("http://embed/v1/models").mock(return_value=httpx.Response(503))
    st = await check_embed(make_settings())
    assert not st.ok and st.detail == "HTTP 503"


@respx.mock
async def test_embed_model_mismatch_is_flagged():
    respx.get("http://embed/v1/models").mock(
        return_value=httpx.Response(200, json=models_payload("nullish-embed", "Other/Embedder"))
    )
    st = await check_embed(make_settings(embed_model_id="Qwen/Qwen3-Embedding-0.6B"))
    assert not st.ok and "index expects Qwen/Qwen3-Embedding-0.6B" in st.detail


@respx.mock
async def test_embed_model_match_ok():
    respx.get("http://embed/v1/models").mock(
        return_value=httpx.Response(
            200, json=models_payload("nullish-embed", "Qwen/Qwen3-Embedding-0.6B")
        )
    )
    st = await check_embed(make_settings(embed_model_id="Qwen/Qwen3-Embedding-0.6B"))
    assert st.ok and st.model == "Qwen/Qwen3-Embedding-0.6B"


@respx.mock
async def test_embed_dimension_guard():
    respx.post("http://embed/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})
    )
    with pytest.raises(EmbeddingDimensionError):
        await EmbedClient(make_settings()).embed(["x"])


@respx.mock
async def test_rerank_tei_sorted_and_capped():
    route = respx.post("http://rerank/rerank").mock(
        return_value=httpx.Response(200, json=[{"index": 0, "score": 0.1},
                                               {"index": 1, "score": 0.9}])
    )
    s = make_settings(rerank_kind="tei", rerank_max_candidates=2)
    res = await RerankClient(s).rerank("q", ["a", "b", "c"])
    assert [r.index for r in res] == [1, 0]
    assert b'"c"' not in route.calls[0].request.content  # capped at 2 candidates


@respx.mock
async def test_rerank_vllm_sends_key_and_parses():
    route = respx.post("http://rerank/v1/rerank").mock(
        return_value=httpx.Response(200, json={"results": [{"index": 1, "relevance_score": 0.8},
                                                           {"index": 0, "relevance_score": 0.2}]})
    )
    res = await RerankClient(make_settings(rerank_kind="vllm")).rerank("q", ["a", "b"])
    assert [r.index for r in res] == [1, 0]
    assert route.calls[0].request.headers["authorization"] == "Bearer k"


@respx.mock
async def test_rerank_gpu_falls_back_to_cpu_tei():
    respx.post("http://rerank/v1/rerank").mock(side_effect=httpx.ConnectError("gpu off"))
    respx.post("http://cpu-rr/rerank").mock(
        return_value=httpx.Response(200, json=[{"index": 0, "score": 0.5}])
    )
    s = make_settings(rerank_kind="vllm", rerank_fallback_url="http://cpu-rr")
    res = await RerankClient(s).rerank("q", ["a"])
    assert res[0].score == 0.5


@respx.mock
async def test_embed_sends_api_key():
    route = respx.post("http://embed/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0] * 4}]})
    )
    assert len((await EmbedClient(make_settings()).embed(["x"]))[0]) == 4
    assert route.calls[0].request.headers["authorization"] == "Bearer k"
