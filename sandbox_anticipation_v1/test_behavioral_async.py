# -*- coding: utf-8 -*-
"""TDD — version ASYNC du miroir comportemental (judge = coroutine, futur appel Ollama)."""
import pytest
from behavioral_mirror import behavioral_mirror_async, make_behavioral_mirror_async


async def judge_sain(draft):
    return '{"orniere":{"viole":false,"score":0.1},"logos":{"opere":true},"honnete":{"assume":true}}'

async def judge_orniere(draft):
    return '{"orniere":{"viole":true,"score":0.85},"logos":{"opere":true},"honnete":{"assume":true}}'

async def judge_json_casse(draft):
    return "ollama a renvoye de la prose au lieu de json"

async def judge_timeout(draft):
    raise TimeoutError("ollama injoignable")


@pytest.mark.asyncio
async def test_async_brouillon_sain_passe():
    ok, rej = await behavioral_mirror_async("analyse active", judge_sain)
    assert ok is True and rej is None

@pytest.mark.asyncio
async def test_async_orniere_leve_veto():
    ok, rej = await behavioral_mirror_async("complainte", judge_orniere)
    assert ok is False and "ORNIERE" in rej

@pytest.mark.asyncio
async def test_async_json_casse_laisse_passer():
    ok, rej = await behavioral_mirror_async("x", judge_json_casse)
    assert ok is True and rej is None          # doctrine inverse : doute -> on n'entrave pas

@pytest.mark.asyncio
async def test_async_timeout_laisse_passer():
    ok, rej = await behavioral_mirror_async("x", judge_timeout)
    assert ok is True and rej is None

@pytest.mark.asyncio
async def test_make_async_mirror_wrappe_bien():
    m = make_behavioral_mirror_async(judge_orniere)
    ok, rej = await m("x")
    assert ok is False and "[PREFRONTAL_BEHAVIORAL_VETO]" in rej
